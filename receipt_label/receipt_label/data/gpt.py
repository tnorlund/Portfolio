import re
from json import JSONDecodeError, dumps, loads
from os import environ, getenv
import requests
from requests.models import Response
from typing import Dict, List, Optional, Tuple

from ..models.receipt import Receipt, ReceiptWord

def gpt_request_structure_analysis(
    receipt: Receipt,
    receipt_lines: List[Dict],
    receipt_words: List[Dict],
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
        receipt_lines (List[Dict]): List of receipt line objects.
        receipt_words (List[Dict]): List of receipt word objects.
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
    query = _llm_prompt_structure_analysis(receipt, receipt_lines, receipt_words, places_api_data)
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": "You analyze receipt structure to identify natural sections based on spatial and content patterns, using business context from Google Places API.",
            },
            {"role": "user", "content": query},
        ],
        "temperature": 0.3,  # Lower temperature for more consistent analysis
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)

    return (
        _validate_gpt_response_structure_analysis(response),
        query,
        response.text,
    )

def gpt_request_field_labeling(
    receipt: Receipt,
    receipt_lines: List[Dict],
    receipt_words: List[Dict],
    section_boundaries: Dict,
    places_api_data: Dict,
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Makes a request to the OpenAI API to label individual words in a receipt.
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
        section_lines = [l for l in receipt_lines if l.line_id in section["line_ids"]]
        section_words = [w for w in receipt_words if w["line_id"] in section["line_ids"]]
        
        if not section_words:
            continue

        query = _llm_prompt_field_labeling_section(
            receipt=receipt,
            section_lines=section_lines,
            section_words=section_words,
            section_info=section,
            places_api_data=places_api_data
        )
        queries.append(query)
        
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": f"You label words in the {section['name']} section of receipts, using business context for accuracy.",
                },
                {"role": "user", "content": query},
            ],
            "temperature": 0.3,
        }
        
        try:
            # Increased timeout to 60 seconds and added retries
            for attempt in range(3):  # Try up to 3 times
                try:
                    response = requests.post(url, headers=headers, json=payload, timeout=60)
                    response.raise_for_status()
                    break  # If successful, break the retry loop
                except requests.Timeout:
                    if attempt == 2:  # Last attempt
                        raise  # Re-raise the timeout error
                    print(f"Timeout processing section {section['name']}, attempt {attempt + 1}/3")
                    continue  # Try again
                except requests.RequestException as e:
                    if attempt == 2:  # Last attempt
                        raise  # Re-raise the error
                    print(f"Error processing section {section['name']}, attempt {attempt + 1}/3: {str(e)}")
                    continue  # Try again
            
            responses.append(response.text)
            
            # Validate and process section results
            section_result = _validate_gpt_response_field_labeling(response)
            
            # Map the text labels back to word IDs
            mapped_labels = _map_labels_to_word_ids(
                section_result["labeled_words"],
                section_words
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
        "labels": all_labels,
        "metadata": {
            "total_labeled_words": len(all_labels),
            "average_confidence": sum(l["confidence"] for l in all_labels) / len(all_labels),
            "requires_review": requires_review,
            "review_reasons": list(set(review_reasons))  # Remove duplicates
        }
    }

    return (
        final_result,
        "\n---\n".join(queries),  # Join all queries for reference
        "\n---\n".join(responses)  # Join all responses for reference
    )

def _llm_prompt_structure_analysis(
    receipt: Receipt,
    receipt_lines: List[Dict],
    receipt_words: List[Dict],
    places_api_data: Dict,
) -> str:
    """Generate the prompt for structure analysis."""
    # Format receipt content
    formatted_lines = []
    for line in receipt_lines:
        line_words = [w for w in receipt_words if w["line_id"] == line.line_id]
        line_text = " ".join(w["text"] for w in line_words)
        formatted_lines.append(f"Line {line.line_id}: {line_text}")

    # Format business context
    business_context = {
        "name": places_api_data.get("name", "Unknown"),
        "address": places_api_data.get("formatted_address", "Unknown"),
        "phone": places_api_data.get("formatted_phone_number", "Unknown"),
        "website": places_api_data.get("website", "Unknown"),
        "types": places_api_data.get("types", [])
    }

    return (
        f"Analyze the structure of this receipt.\n\n"
        f"Business Context:\n{dumps(business_context, indent=2)}\n\n"
        f"Receipt Content:\n"
        + "\n".join(formatted_lines)
        + "\n\nINSTRUCTIONS:\n"
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
    section_lines: List[Dict],
    section_words: List[Dict],
    section_info: Dict,
    places_api_data: Dict,
) -> str:
    """Generate the prompt for field labeling of a specific section."""
    # Format section content
    formatted_lines = []
    for line in section_lines:
        line_words = [w for w in section_words if w["line_id"] == line.line_id]
        line_text = " ".join(w["text"] for w in line_words)
        formatted_lines.append(f"Line {line.line_id}: {line_text}")

    # Format business context
    business_context = {
        "name": places_api_data.get("name", "Unknown"),
        "address": places_api_data.get("formatted_address", "Unknown"),
        "phone": places_api_data.get("formatted_phone_number", "Unknown"),
        "website": places_api_data.get("website", "Unknown"),
        "types": places_api_data.get("types", [])
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
        "membership_id": "Membership identifier"
    }

    # Default examples for any section type
    default_examples = [
        '{"text": "COSTCO WHOLESALE", "label": "business_name", "confidence": 0.95}',
        '{"text": "5700 Lindero Canyon Rd", "label": "address_line", "confidence": 0.95}',
        '{"text": "12/17/2024", "label": "date", "confidence": 0.95}',
        '{"text": "17:19", "label": "time", "confidence": 0.95}',
        '{"text": "TOTAL", "label": "total", "confidence": 0.95}'
    ]

    # Select examples based on section type
    if "business_info" in section_info["name"].lower():
        examples = [
            '{"text": "COSTCO WHOLESALE", "label": "business_name", "confidence": 0.95}',
            '{"text": "5700 Lindero Canyon Rd", "label": "address_line", "confidence": 0.95}',
            '{"text": "#117", "label": "store_id", "confidence": 0.90}'
        ]
    elif "payment" in section_info["name"].lower():
        examples = [
            '{"text": "TOTAL", "label": "total", "confidence": 0.95}',
            '{"text": "$63.27", "label": "amount", "confidence": 0.95}',
            '{"text": "APPROVED", "label": "payment_status", "confidence": 0.90}'
        ]
    elif "transaction" in section_info["name"].lower():
        examples = [
            '{"text": "12/17/2024", "label": "date", "confidence": 0.95}',
            '{"text": "17:19", "label": "time", "confidence": 0.95}',
            '{"text": "Tran ID#: 12345", "label": "transaction_id", "confidence": 0.90}'
        ]
    else:
        examples = default_examples
    
    return (
        f"Label words in the {section_info['name'].upper()} section of this receipt.\n\n"
        f"Business Context:\n{dumps(business_context, indent=2)}\n\n"
        f"Receipt Content:\n"
        + "\n".join(formatted_lines)
        + "\n\nAvailable Labels:\n"
        + "\n".join(f"- {label}: {desc}" for label, desc in label_types.items())
        + "\n\nExample Labelings:\n"
        + "\n".join(examples)
        + "\n\nINSTRUCTIONS:\n"
        "1. Label each piece of meaningful text with the most specific applicable label\n"
        "2. Combine words that form a single meaningful unit (e.g., 'Lindero Canyon Rd' as one address_line)\n"
        "3. Never use 'unknown' or labels not in the list above\n"
        "4. Set high confidence (0.9+) for clear matches, lower (0.7-0.8) if uncertain\n"
        "5. Mark requires_review true if you're unsure about any labels\n\n"
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
        '    "review_reasons": []\n'
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
            
            required_section_keys = ["name", "line_ids", "spatial_patterns", "content_patterns", "confidence"]
            if not all(key in section for key in required_section_keys):
                raise ValueError(f"Section missing required keys: {required_section_keys}")
            
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
        raise ValueError(f"Invalid JSON in response: {e}\nResponse text: {response.text}")
    except Exception as e:
        raise ValueError(f"Error validating response: {e}\nResponse text: {response.text}")

def _validate_gpt_response_field_labeling(response: Response) -> Dict:
    """Validate the field labeling response from the OpenAI API."""
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
        
        required_keys = ["labeled_words", "metadata"]
        if not all(key in parsed for key in required_keys):
            raise ValueError(f"Missing required keys: {required_keys}")
        
        # Validate labeled_words array
        if not isinstance(parsed["labeled_words"], list):
            raise ValueError("'labeled_words' must be a list.")
        
        for label in parsed["labeled_words"]:
            if not isinstance(label, dict):
                raise ValueError("Each label must be a dictionary.")
            
            required_label_keys = ["text", "label", "confidence"]
            if not all(key in label for key in required_label_keys):
                raise ValueError(f"Label missing required keys: {required_label_keys}")
            
            if not isinstance(label["text"], str):
                raise ValueError("'text' must be a string.")
            if not isinstance(label["label"], str):
                raise ValueError("'label' must be a string.")
            if not isinstance(label["confidence"], (int, float)):
                raise ValueError("'confidence' must be a number.")
            if not (0 <= label["confidence"] <= 1):
                raise ValueError("'confidence' must be between 0 and 1.")
        
        # Validate metadata
        metadata = parsed["metadata"]
        required_metadata_keys = ["requires_review", "review_reasons"]
        if not all(key in metadata for key in required_metadata_keys):
            raise ValueError(f"Metadata missing required keys: {required_metadata_keys}")
        
        if not isinstance(metadata["requires_review"], bool):
            raise ValueError("'requires_review' must be a boolean.")
        if not isinstance(metadata["review_reasons"], list):
            raise ValueError("'review_reasons' must be a list.")
        
        return parsed
        
    except JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in response: {e}\nResponse text: {response.text}")
    except Exception as e:
        raise ValueError(f"Error validating response: {e}\nResponse text: {response.text}")

def _map_labels_to_word_ids(
    labeled_words: List[Dict],
    section_words: List[Dict]
) -> List[Dict]:
    """Map text labels back to word IDs."""
    mapped_labels = []
    for label in labeled_words:
        # Find matching word(s) in section
        matching_words = []
        label_text = label["text"].strip()
        
        # Try exact match first
        for word in section_words:
            if word["text"].strip() == label_text:
                matching_words.append(word)
                break
        
        # If no exact match, try partial matches
        if not matching_words:
            words = label_text.split()
            if len(words) > 1:
                # Try to match consecutive words
                for i in range(len(section_words) - len(words) + 1):
                    if all(
                        section_words[i + j]["text"].strip() == words[j]
                        for j in range(len(words))
                    ):
                        matching_words.extend(section_words[i:i + len(words)])
                        break
        
        if matching_words:
            # Create a label for each matching word
            for word in matching_words:
                mapped_labels.append({
                    "line_id": word["line_id"],
                    "word_id": word["word_id"],
                    "label": label["label"],
                    "confidence": label["confidence"]
                })
    
    return mapped_labels 