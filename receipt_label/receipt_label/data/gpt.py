import re
from json import JSONDecodeError, dumps, loads
from os import environ, getenv
import requests
from requests.models import Response
from typing import Dict, List, Optional, Tuple, Union
import logging
from decimal import Decimal
from json import JSONEncoder
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine
from datetime import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Configure logger
logger = logging.getLogger(__name__)

MODEL = "gpt-3.5-turbo"

# Create a thread pool executor for running synchronous requests
_executor = ThreadPoolExecutor(max_workers=4)

class DecimalEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(DecimalEncoder, self).default(obj)

async def _async_post(url: str, headers: Dict, json: Dict, timeout: int) -> Response:
    """Run synchronous requests.post in a thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor, 
        lambda: requests.post(url, headers=headers, json=json, timeout=timeout)
    )

async def gpt_request_structure_analysis(
    receipt: Receipt,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    places_api_data: Dict,
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Makes a request to the OpenAI API to analyze the structure of a receipt.

    This function analyzes the spatial and content patterns in the receipt to identify
    natural sections and their relationships, using both the receipt data and
    Google Places API context.

    Args:
        receipt (Receipt): The receipt object containing metadata.
        receipt_lines (List[ReceiptLine]): List of receipt line objects.
        receipt_words (List[ReceiptWord]): List of receipt word objects.
        places_api_data (Dict): Business context from Google Places API.
        gpt_api_key (str, optional): The OpenAI API key. Defaults to None.

    Returns:
        Tuple[Dict, str, str]: The formatted response from the OpenAI API, the query
            sent to OpenAI API, and the raw response from OpenAI API.
    """
    if not gpt_api_key and not getenv("OPENAI_API_KEY"):
        raise ValueError("The OPENAI_API_KEY environment variable is not set.")
    if gpt_api_key:
        environ["OPENAI_API_KEY"] = gpt_api_key
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {getenv('OPENAI_API_KEY')}",
    }
    query = _llm_prompt_structure_analysis(
        receipt, receipt_lines, receipt_words, places_api_data
    )
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You analyze receipt structure to identify natural sections based on "
                    "spatial and content patterns, using business context from Google "
                    "Places API."
                ),
            },
            {"role": "user", "content": query},
        ],
        "temperature": 0.3,  # Lower temperature for more consistent analysis
    }

    response = await _async_post(url, headers=headers, json=payload, timeout=30)
    
    return (
        _validate_gpt_response_structure_analysis(response),
        query,
        response.text,
    )

async def gpt_request_field_labeling(
    receipt: Receipt,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    section_boundaries: Dict,
    places_api_data: Dict,
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """Makes a request to the OpenAI API to label individual words in a receipt.
    Now uses a simpler format for GPT responses and maps the labels back to word IDs.
    """
    if not gpt_api_key and not getenv("OPENAI_API_KEY"):
        raise ValueError("The OPENAI_API_KEY environment variable is not set.")
    if gpt_api_key:
        environ["OPENAI_API_KEY"] = gpt_api_key

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {getenv('OPENAI_API_KEY')}",
    }

    # Process each section separately
    all_labels = []
    requires_review = False
    review_reasons = []
    queries = []
    responses = []

    for section in section_boundaries["discovered_sections"]:
        # Get words for this section
        section_lines = [
            line for line in receipt_lines 
            if (hasattr(line, 'line_id') and line.line_id in section["line_ids"]) or
               (isinstance(line, dict) and line.get('line_id') in section["line_ids"])
        ]
        section_words = [
            word for word in receipt_words 
            if (hasattr(word, 'line_id') and word.line_id in section["line_ids"]) or
               (isinstance(word, dict) and word.get('line_id') in section["line_ids"])
        ]

        if not section_words:
            continue

        query = _llm_prompt_field_labeling_section(
            receipt=receipt,
            section_lines=section_lines,
            section_words=section_words,
            section_info=section,
            places_api_data=places_api_data,
        )
        queries.append(query)

        payload = {
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You label words in the {section['name']} section of receipts, "
                        "using business context for accuracy."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "temperature": 0.3,
        }

        try:
            # Increased timeout to 60 seconds and added retries
            for attempt in range(3):  # Try up to 3 times
                try:
                    response = await _async_post(url, headers=headers, json=payload, timeout=60)
                    break  # If successful, break the retry loop
                except requests.Timeout:
                    if attempt == 2:  # Last attempt
                        raise  # Re-raise the timeout error
                    print(
                        f"Timeout processing section {section['name']}, "
                        f"attempt {attempt + 1}/3"
                    )
                    continue  # Try again
                except requests.RequestException as e:
                    if attempt == 2:  # Last attempt
                        raise  # Re-raise the error
                    print(
                        f"Error processing section {section['name']}, "
                        f"attempt {attempt + 1}/3: {str(e)}"
                    )
                    continue  # Try again

            responses.append(response.text)

            # Validate and process section results
            section_result = _validate_gpt_response_field_labeling(response)

            # Map the text labels back to word IDs
            mapped_labels = _map_labels_to_word_ids(
                section_result["labeled_words"], section_words
            )
            all_labels.extend(mapped_labels)

            if section_result["metadata"]["requires_review"]:
                requires_review = True
                review_reasons.extend(section_result["metadata"]["review_reasons"])

        except Exception as e:
            raise ValueError(f"Error processing section {section['name']}: {str(e)}")

    # Combine results
    if not all_labels:
        raise ValueError("No labels were generated for any section.")

    final_result = {
        "labels": [
            {
                **label,
                "text": next((
                    word.text if hasattr(word, 'text') else word.get('text', '')
                    for word in receipt_words 
                    if (
                        (hasattr(word, 'line_id') and word.line_id == label["line_id"] and 
                         hasattr(word, 'word_id') and word.word_id == label["word_id"]) or
                        (isinstance(word, dict) and word.get('line_id') == label["line_id"] and 
                         word.get('word_id') == label["word_id"])
                    )
                ), "")
            } for label in all_labels
        ],
        "metadata": {
            "total_labeled_words": len(all_labels),
            "average_confidence": (
                sum(label["confidence"] for label in all_labels) / len(all_labels)
            ),
            "requires_review": requires_review,
            "review_reasons": list(set(review_reasons)),  # Remove duplicates
        },
    }

    return (
        final_result,
        "\n---\n".join(queries),  # Join all queries for reference
        "\n---\n".join(responses),  # Join all responses for reference
    )

async def gpt_request_line_item_analysis(
    receipt: Receipt,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    traditional_analysis: Dict,
    places_api_data: Dict,
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Makes a request to the OpenAI API to analyze line items in a receipt.

    This function analyzes receipt line items using both traditional analysis results
    and GPT processing to provide accurate item extraction with prices and quantities.

    Args:
        receipt (Receipt): The receipt object containing metadata.
        receipt_lines (List[ReceiptLine]): List of receipt line objects.
        receipt_words (List[ReceiptWord]): List of receipt word objects.
        traditional_analysis (Dict): Results from traditional line item processing.
        places_api_data (Dict): Business context from Google Places API.
        gpt_api_key (str, optional): The OpenAI API key. Defaults to None.

    Returns:
        Tuple[Dict, str, str]: The formatted response from the OpenAI API, the query
            sent to OpenAI API, and the raw response from OpenAI API.
    """
    if not gpt_api_key and not getenv("OPENAI_API_KEY"):
        raise ValueError("The OPENAI_API_KEY environment variable is not set.")
    if gpt_api_key:
        environ["OPENAI_API_KEY"] = gpt_api_key

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {getenv('OPENAI_API_KEY')}",
    }

    query = _llm_prompt_line_item_analysis(
        receipt, receipt_lines, receipt_words, traditional_analysis, places_api_data
    )

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You analyze receipt line items with high precision, focusing on "
                    "accurate extraction of items, quantities, prices, and totals. "
                    "Consider spatial layout and business context."
                ),
            },
            {"role": "user", "content": query},
        ],
        "temperature": 0.1,  # Low temperature for consistent numerical analysis
    }

    response = await _async_post(url, headers=headers, json=payload, timeout=60)
    
    return (
        _validate_gpt_response_line_item_analysis(response),
        query,
        response.text,
    )

def _llm_prompt_structure_analysis(
    receipt: Receipt,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    places_api_data: Optional[Dict] = None,
) -> str:
    """Generate prompt for structure analysis."""
    # Format receipt content
    formatted_lines = []
    for line in receipt_lines:
        # Get words for this line
        line_words = [word for word in receipt_words if word.get('line_id') == line.get('line_id')]
        line_text = " ".join(word.get('text', '') for word in line_words)
        formatted_lines.append(f"Line {line.get('line_id')}: {line_text}")

    receipt_content = "\n".join(formatted_lines)

    # Format business context
    business_context = {
        "name": places_api_data.get("name", "Unknown"),
        "address": places_api_data.get("formatted_address", "Unknown"),
        "phone": places_api_data.get("formatted_phone_number", "Unknown"),
        "website": places_api_data.get("website", "Unknown"),
        "types": places_api_data.get("types", []),
    }

    return (
        f"Analyze the structure of this receipt.\n\n"
        f"Business Context:\n{dumps(business_context, indent=2)}\n\n"
        f"Receipt Content:\n" + receipt_content + "\n\nINSTRUCTIONS:\n"
        "1. Identify natural sections in the receipt based on spatial and content patterns\n"
        "2. Consider business context when identifying sections\n"
        "3. Look for patterns like:\n"
        "   - Header sections with business info\n"
        "   - Transaction details sections\n"
        "   - Payment/total sections\n"
        "   - Footer sections with additional info\n"
        "4. For each section, note:\n"
        "   - Line IDs included\n"
        "   - Spatial patterns (alignment, spacing)\n"
        "   - Content patterns (keywords, formatting)\n"
        "   - Confidence in the section identification\n\n"
        "Return this JSON structure:\n"
        "{\n"
        '  "discovered_sections": [\n'
        "    {\n"
        '      "name": "section_name",\n'
        '      "line_ids": [1, 2, 3],\n'
        '      "spatial_patterns": ["pattern1", "pattern2"],\n'
        '      "content_patterns": ["pattern1", "pattern2"],\n'
        '      "confidence": 0.95\n'
        "    }\n"
        "  ],\n"
        '  "overall_confidence": 0.95\n'
        "}\n\n"
        "Return ONLY the JSON object. No other text."
    )


def _llm_prompt_field_labeling_section(
    receipt: Receipt,
    section_lines: List[ReceiptLine],
    section_words: List[ReceiptWord],
    section_info: Dict,
    places_api_data: Optional[Dict] = None,
) -> str:
    """Generate prompt for field labeling of a specific section."""
    # Format receipt content
    formatted_lines = []
    for line in section_lines:
        # Get words for this line
        line_id = line.line_id if hasattr(line, 'line_id') else line.get('line_id')
        line_words = [
            word for word in section_words 
            if (hasattr(word, 'line_id') and word.line_id == line_id) or
               (isinstance(word, dict) and word.get('line_id') == line_id)
        ]
        # Get text from words
        word_texts = []
        for word in line_words:
            if hasattr(word, 'text'):
                word_texts.append(word.text)
            elif isinstance(word, dict):
                word_texts.append(word.get('text', ''))
        line_text = " ".join(word_texts)
        formatted_lines.append(line_text)

    receipt_content = "\n".join(formatted_lines)

    # Format business context
    business_context = {
        "name": places_api_data.get("name", "Unknown"),
        "address": places_api_data.get("formatted_address", "Unknown"),
        "phone": places_api_data.get("formatted_phone_number", "Unknown"),
        "website": places_api_data.get("website", "Unknown"),
        "types": places_api_data.get("types", []),
    }

    # Define label types and their descriptions
    label_types = {
        "business_name": "Name of the business",
        "address_line": "Part of the business address",
        "phone": "Phone number",
        "date": "Date of transaction",
        "time": "Time of transaction",
        "transaction_id": "Unique transaction identifier",
        "store_id": "Store or location identifier",
        "subtotal": "Subtotal amount",
        "tax": "Tax amount",
        "total": "Total amount",
        "payment_status": "Payment status (e.g., APPROVED)",
        "payment_method": "Method of payment",
        "cashier_id": "Cashier identifier",
        "item": "Individual item on receipt",
        "item_quantity": "Quantity of an item",
        "item_price": "Price of an item",
        "item_total": "Total for an item",
        "discount": "Discount amount",
        "coupon": "Coupon code or amount",
        "loyalty_id": "Loyalty program identifier",
        "membership_id": "Membership identifier",
    }

    # Default examples for any section type
    default_examples = [
        '{"text": "COSTCO WHOLESALE", "label": "business_name", "confidence": 0.95}',
        '{"text": "5700 Lindero Canyon Rd", "label": "address_line", "confidence": 0.95}',
        '{"text": "12/17/2024", "label": "date", "confidence": 0.95}',
        '{"text": "17:19", "label": "time", "confidence": 0.95}',
        '{"text": "TOTAL", "label": "total", "confidence": 0.95}',
    ]

    # Select examples based on section type
    if "business_info" in section_info["name"].lower():
        examples = [
            '{"text": "COSTCO WHOLESALE", "label": "business_name", "confidence": 0.95}',
            '{"text": "5700 Lindero Canyon Rd", "label": "address_line", "confidence": 0.95}',
            '{"text": "#117", "label": "store_id", "confidence": 0.90}',
        ]
    elif "payment" in section_info["name"].lower():
        examples = [
            '{"text": "TOTAL", "label": "total", "confidence": 0.95}',
            '{"text": "$63.27", "label": "amount", "confidence": 0.95}',
            '{"text": "APPROVED", "label": "payment_status", "confidence": 0.90}',
        ]
    elif "transaction" in section_info["name"].lower():
        examples = [
            '{"text": "12/17/2024", "label": "date", "confidence": 0.95}',
            '{"text": "17:19", "label": "time", "confidence": 0.95}',
            '{"text": "Tran ID#: 12345", "label": "transaction_id", "confidence": 0.90}',
        ]
    else:
        examples = default_examples

    return (
        f"Label words in the {section_info['name'].upper()} section of this receipt.\n\n"
        f"Business Context:\n{dumps(business_context, indent=2)}\n\n"
        f"Receipt Content:\n"
        + receipt_content
        + "\n\nAvailable Labels:\n"
        + "\n".join(f"- {label}: {desc}" for label, desc in label_types.items())
        + "\n\nExample Labelings:\n"
        + "\n".join(examples)
        + "\n\nINSTRUCTIONS:\n"
        "1. Label each piece of meaningful text with the most specific applicable label\n"
        "2. Combine words that form a single meaningful unit (e.g., 'Lindero Canyon Rd' as one address_line)\n"
        "3. Never use 'unknown' or labels not in the list above\n"
        "4. Set high confidence (0.9+) for clear matches, lower (0.7-0.8) if uncertain\n"
        "5. Mark requires_review true if you're unsure about any labels\n"
        "6. Calculate average_confidence as the mean of all word confidences\n\n"
        "Return this JSON structure:\n"
        "{\n"
        '  "labeled_words": [\n'
        "    {\n"
        '      "text": "exact text from receipt",\n'
        '      "label": "label from list above",\n'
        '      "confidence": 0.95\n'
        "    }\n"
        "  ],\n"
        '  "metadata": {\n'
        '    "requires_review": false,\n'
        '    "review_reasons": [],\n'
        '    "average_confidence": 0.95\n'
        "  }\n"
        "}\n\n"
        "Return ONLY the JSON object. No other text."
    )


def _llm_prompt_line_item_analysis(
    receipt: Receipt,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    traditional_analysis: Dict,
    places_api_data: Optional[Dict] = None,
) -> str:
    """Generate prompt for line item analysis."""
    # Format receipt content
    formatted_lines = []
    for line in receipt_lines:
        # Get words for this line
        line_words = [word for word in receipt_words if word.line_id == line.line_id]
        line_text = " ".join(word.text for word in line_words)
        formatted_lines.append(f"Line {line.line_id}: {line_text}")

    receipt_content = "\n".join(formatted_lines)

    # Format business context
    business_context = {
        "name": places_api_data.get("name", "Unknown"),
        "types": places_api_data.get("types", []),
    }

    return (
        f"Analyze line items in this receipt with high precision.\n\n"
        f"Business Context:\n{dumps(business_context, indent=2)}\n\n"
        f"Receipt Content:\n" + receipt_content +
        f"Traditional Analysis:\n{dumps(traditional_analysis, indent=2, cls=DecimalEncoder)}\n\n"
        "INSTRUCTIONS:\n"
        "1. Extract all line items with their descriptions, quantities, and prices\n"
        "2. Consider spatial layout (items typically left-aligned, prices right-aligned)\n"
        "3. Look for quantity indicators (e.g., @, x, EA) and unit prices\n"
        "4. Validate price calculations (quantity * unit price = extended price)\n"
        "5. Compare against receipt totals for consistency\n"
        "6. Note any discrepancies or uncertain items\n\n"
        "Return this JSON structure:\n"
        "{\n"
        '  "line_items": [\n'
        "    {\n"
        '      "description": "complete item description",\n'
        '      "quantity": {"amount": number, "unit": "string"},\n'
        '      "price": {"unit_price": number, "extended_price": number},\n'
        '      "confidence": number,\n'
        '      "line_ids": [number]\n'
        "    }\n"
        "  ],\n"
        '  "analysis": {\n'
        '    "total_found": number,\n'
        '    "subtotal": number,\n'
        '    "tax": number,\n'
        '    "total": number,\n'
        '    "discrepancies": [string],\n'
        '    "confidence": number\n'
        "  }\n"
        "}\n\n"
        "Return ONLY the JSON object. No other text."
    )


def _validate_gpt_response_structure_analysis(response: Response) -> Dict:
    """Validate the structure analysis response from the OpenAI API."""
    response.raise_for_status()
    data = response.json()

    if "choices" not in data or not data["choices"]:
        raise ValueError("The response does not contain any choices.")

    first_choice = data["choices"][0]
    if "message" not in first_choice:
        raise ValueError("The response does not contain a message.")

    message = first_choice["message"]
    if "content" not in message:
        raise ValueError("The response message does not contain content.")

    content = message["content"]
    if not content:
        raise ValueError("The response message content is empty.")

    try:
        # Handle potential JSON code blocks
        if "```json" in content:
            match = re.search(r"```json(.*?)```", content, flags=re.DOTALL)
            content = match.group(1) if match else content

        # Parse the JSON content
        parsed = loads(content.strip())

        # Validate structure
        if not isinstance(parsed, dict):
            raise ValueError("The response must be a dictionary.")

        required_keys = ["discovered_sections", "overall_confidence"]
        if not all(key in parsed for key in required_keys):
            raise ValueError(f"Missing required keys: {required_keys}")

        # Validate discovered_sections array
        if not isinstance(parsed["discovered_sections"], list):
            raise ValueError("'discovered_sections' must be a list.")

        for section in parsed["discovered_sections"]:
            if not isinstance(section, dict):
                raise ValueError("Each section must be a dictionary.")

            required_section_keys = [
                "name",
                "line_ids",
                "spatial_patterns",
                "content_patterns",
                "confidence",
            ]
            if not all(key in section for key in required_section_keys):
                raise ValueError(
                    f"Section missing required keys: {required_section_keys}"
                )

            if not isinstance(section["name"], str):
                raise ValueError("'name' must be a string.")
            if not isinstance(section["line_ids"], list):
                raise ValueError("'line_ids' must be a list.")
            if not isinstance(section["spatial_patterns"], list):
                raise ValueError("'spatial_patterns' must be a list.")
            if not isinstance(section["content_patterns"], list):
                raise ValueError("'content_patterns' must be a list.")
            if not isinstance(section["confidence"], (int, float)):
                raise ValueError("'confidence' must be a number.")
            if not (0 <= section["confidence"] <= 1):
                raise ValueError("'confidence' must be between 0 and 1.")

        # Validate overall_confidence
        if not isinstance(parsed["overall_confidence"], (int, float)):
            raise ValueError("'overall_confidence' must be a number.")
        if not (0 <= parsed["overall_confidence"] <= 1):
            raise ValueError("'overall_confidence' must be between 0 and 1.")

        return parsed

    except JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in response: {e}\nResponse text: {response.text}"
        )
    except Exception as e:
        raise ValueError(
            f"Error validating response: {e}\nResponse text: {response.text}"
        )


def _validate_gpt_response_field_labeling(response: Response) -> Dict:
    """Validate the field labeling response from the OpenAI API."""
    try:
        response.raise_for_status()
        data = response.json()

        if "choices" not in data or not data["choices"]:
            raise ValueError("The response does not contain any choices.")

        first_choice = data["choices"][0]
        if "message" not in first_choice or "content" not in first_choice["message"]:
            raise ValueError("The response choice does not contain a message with content.")

        try:
            parsed = loads(first_choice["message"]["content"])
        except JSONDecodeError:
            raise ValueError(f"Invalid JSON in message content: {first_choice['message']['content']}")

        # Validate required top-level keys
        required_keys = ["labeled_words", "metadata"]
        if not all(key in parsed for key in required_keys):
            raise ValueError(f"Response missing required keys: {required_keys}")

        # Validate labeled_words structure
        if not isinstance(parsed["labeled_words"], list):
            raise ValueError("'labeled_words' must be a list.")

        for word in parsed["labeled_words"]:
            if not isinstance(word, dict):
                raise ValueError("Each labeled word must be a dictionary.")
            required_word_keys = ["text", "label", "confidence"]
            if not all(key in word for key in required_word_keys):
                raise ValueError(f"Labeled word missing required keys: {required_word_keys}")
            if not isinstance(word["confidence"], (int, float)):
                raise ValueError("Word confidence must be a number.")
            if not (0 <= word["confidence"] <= 1):
                raise ValueError("Word confidence must be between 0 and 1.")

        # Validate metadata structure
        required_metadata_keys = ["requires_review", "review_reasons", "average_confidence"]
        if not all(key in parsed["metadata"] for key in required_metadata_keys):
            raise ValueError(f"Metadata missing required keys: {required_metadata_keys}")

        return parsed

    except JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in response: {e}\nResponse text: {response.text}")
    except Exception as e:
        raise ValueError(f"Error validating response: {e}\nResponse text: {response.text}")


def _validate_gpt_response_line_item_analysis(response: Response) -> Dict:
    """Validate the line item analysis response from the OpenAI API."""
    response.raise_for_status()
    data = response.json()

    if "choices" not in data or not data["choices"]:
        raise ValueError("The response does not contain any choices.")

    first_choice = data["choices"][0]
    if "message" not in first_choice:
        raise ValueError("The response does not contain a message.")

    message = first_choice["message"]
    if "content" not in message:
        raise ValueError("The response message does not contain content.")

    content = message["content"]
    if not content:
        raise ValueError("The response message content is empty.")

    try:
        # Handle potential JSON code blocks
        if "```json" in content:
            match = re.search(r"```json(.*?)```", content, flags=re.DOTALL)
            content = match.group(1) if match else content

        # Parse the JSON content
        parsed = loads(content.strip())

        # Validate structure
        if not isinstance(parsed, dict):
            raise ValueError("The response must be a dictionary.")

        required_keys = ["line_items", "analysis"]
        if not all(key in parsed for key in required_keys):
            raise ValueError(f"Missing required keys: {required_keys}")

        # Validate line_items array
        if not isinstance(parsed["line_items"], list):
            raise ValueError("'line_items' must be a list.")

        for item in parsed["line_items"]:
            if not isinstance(item, dict):
                raise ValueError("Each item must be a dictionary.")

            required_item_keys = ["description", "quantity", "price", "confidence", "line_ids"]
            if not all(key in item for key in required_item_keys):
                raise ValueError(f"Item missing required keys: {required_item_keys}")

            # Validate quantity structure - allow null
            if item["quantity"] is not None:
                if not isinstance(item["quantity"], dict):
                    raise ValueError("When present, 'quantity' must be a dictionary.")
                if not all(key in item["quantity"] for key in ["amount", "unit"]):
                    raise ValueError("When present, 'quantity' must have 'amount' and 'unit' keys.")

            # Validate price structure
            if not isinstance(item["price"], dict):
                raise ValueError("'price' must be a dictionary.")
            if not all(key in item["price"] for key in ["unit_price", "extended_price"]):
                raise ValueError("'price' must have 'unit_price' and 'extended_price' keys.")

        # Validate analysis structure
        analysis = parsed["analysis"]
        required_analysis_keys = ["total_found", "subtotal", "tax", "total", "discrepancies", "confidence"]
        if not all(key in analysis for key in required_analysis_keys):
            raise ValueError(f"Analysis missing required keys: {required_analysis_keys}")

        return parsed

    except JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in response: {e}\nResponse text: {response.text}")
    except Exception as e:
        raise ValueError(f"Error validating response: {e}\nResponse text: {response.text}")


def normalize_text(text: str) -> str:
    """Normalize text by removing punctuation and standardizing spacing."""
    # Remove punctuation and standardize spacing
    text = re.sub(r'[^\w\s]', '', text)
    return ' '.join(text.lower().split())


def _map_labels_to_word_ids(labeled_words: List[Dict], section_words: List[Union[Dict, ReceiptWord]]) -> List[Dict]:
    """Map labeled words back to their original word IDs using normalized token matching."""
    mapped_labels = []
    
    # Create normalized version of section words with mapping to original words
    normalized_words = {}
    for word in section_words:
        # Handle both object and dictionary access
        text = word.text if hasattr(word, 'text') else word.get("text", "")
        normalized = normalize_text(text)
        if normalized:  # Only add non-empty strings
            if normalized not in normalized_words:
                normalized_words[normalized] = []
            normalized_words[normalized].append(word)
    
    for labeled_word in labeled_words:
        original_text = labeled_word.get("text", "")
        normalized_label = normalize_text(original_text)
        if not normalized_label:  # Skip empty strings
            continue
            
        words = normalized_label.split()
        matches_found = False
        
        # Try exact phrase match first
        if normalized_label in normalized_words:
            matches_found = True
            for word in normalized_words[normalized_label]:
                mapped_labels.append({
                    "word_id": word.word_id if hasattr(word, 'word_id') else word.get("word_id"),
                    "line_id": word.line_id if hasattr(word, 'line_id') else word.get("line_id"),
                    "label": labeled_word.get("label"),
                    "confidence": labeled_word.get("confidence", 0.0)
                })
        
        # If no exact match, try matching individual words
        if not matches_found:
            matched_words = []
            for word in words:
                if word in normalized_words:
                    matched_words.extend(normalized_words[word])
            
            if matched_words:
                # Adjust confidence based on how many words were matched
                confidence_adjustment = len(matched_words) / len(words)
                for word in matched_words:
                    mapped_labels.append({
                        "word_id": word.word_id if hasattr(word, 'word_id') else word.get("word_id"),
                        "line_id": word.line_id if hasattr(word, 'line_id') else word.get("line_id"),
                        "label": labeled_word.get("label"),
                        "confidence": labeled_word.get("confidence", 0.0) * confidence_adjustment
                    })
            else:
                # Log warning if no matches found
                logger.warning(f"Could not find matching word ID for labeled text: {original_text}")
    
    return mapped_labels


def normalize_address(address: str) -> str:
    """Normalize an address by removing punctuation and standardizing spacing."""
    # Remove punctuation and standardize spacing
    address = re.sub(r'[^\w\s]', '', address)
    return ' '.join(address.lower().split())


def validate_receipt_data(receipt: Receipt, places_api_data: Dict) -> Dict:
    """Validate receipt data against Google Places API data."""
    validation_results = {
        "address_verification": [],
        "hours_verification": [],
    }

    # 1. Business Name Verification
    receipt_name = receipt.name
    api_name = places_api_data.get("name", "")
    
    if receipt_name != api_name:
        validation_results["business_name_verification"] = {
            "type": "warning",
            "message": f"Business name mismatch: Receipt '{receipt_name}' vs API '{api_name}'",
        }

    # 2. Address Verification
    if "address_line" in receipt.fields:
        receipt_address = " ".join(receipt.fields["address_line"])
        api_address = places_api_data.get("formatted_address", "")
        
        # Normalize addresses for comparison
        receipt_address_norm = normalize_address(receipt_address)
        api_address_norm = normalize_address(api_address)
        
        if receipt_address_norm not in api_address_norm and api_address_norm not in receipt_address_norm:
            validation_results["address_verification"].append(
                {
                    "type": "warning",
                    "message": f"Address mismatch: Receipt '{receipt_address}' vs API '{api_address}'",
                }
            )

    # 4. Hours Verification
    if "date" in receipt.fields and "time" in receipt.fields:
        receipt_date = " ".join(receipt.fields["date"])
        receipt_time = " ".join(receipt.fields["time"])
        try:
            # Try multiple date formats
            date_formats = [
                "%Y-%m-%d %H:%M",
                "%m/%d/%Y %H:%M",
                "%m/%d/%y %H:%M",
                "%m/%d/%y %I:%M %p",
                "%m/%d/%y %H:%M %p",
                "%m/%d/%y %H:%M:%S",
                "%m/%d/%y %H:%M:%S %p",
                "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M",
                "%m/%d/%y %H:%M:%S",
                "%m/%d/%y %I:%M %p",
                "%A, %B %d, %Y %I:%M %p",
                "%m/%d/%Y %I:%M %p",
            ]

            receipt_datetime = None
            for fmt in date_formats:
                try:
                    receipt_datetime = datetime.strptime(
                        f"{receipt_date} {receipt_time}", fmt
                    )
                    break
                except ValueError:
                    continue

            if receipt_datetime is None:
                error_msg = f"Could not parse date/time: {receipt_date} {receipt_time}"
                logger.error(error_msg)
                raise ValueError(error_msg)

            # TODO: Add business hours verification when available in Places API
            pass
        except ValueError as e:
            validation_results["hours_verification"].append(
                {
                    "type": "error",
                    "message": f"Invalid date/time format: {receipt_date} {receipt_time}",
                }
            )

    return validation_results
