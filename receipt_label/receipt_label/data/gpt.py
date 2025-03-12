import re
from json import JSONDecodeError, dumps, loads
from os import environ, getenv
import requests
from requests.models import Response
from typing import Dict, List, Optional, Tuple, Union, Any, Set
import logging
from decimal import Decimal
from json import JSONEncoder
from ..models.receipt import Receipt, ReceiptWord, ReceiptLine, ReceiptSection
from ..models.structure import StructureAnalysis
from datetime import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from dataclasses import dataclass

# Configure logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Type hints for HTTP requests
try:
    from requests import Response, post
except ImportError:
    # Define minimal interfaces for type checking when requests is not available
    class Response:
        def __init__(self):
            self.status_code = 200
            self.text = ""
            
        def json(self):
            return {}
            
        def raise_for_status(self):
            pass

MODEL = "gpt-3.5-turbo"

# Create a thread pool executor for running synchronous requests
_executor = ThreadPoolExecutor(max_workers=4)

class DecimalEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super(DecimalEncoder, self).default(obj)

async def _async_post(url: str, headers: Dict, json: Dict, timeout: int) -> Response:
    """
    Make an asynchronous POST request.
    
    Args:
        url: The URL to make the request to
        headers: Headers to include in the request
        json: JSON data to include in the request
        timeout: Timeout in seconds
        
    Returns:
        Response object
    """
    logger = logging.getLogger(__name__)
    
    try:
        logger.debug(f"Making API request to {url}")
        
        if "Authorization" in headers:
            # Log a masked version of the auth header
            auth_header = headers["Authorization"]
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]  # Remove "Bearer " prefix
                masked_token = token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
                logger.debug(f"Using Bearer token: {masked_token}")
            else:
                logger.warning("Authorization header doesn't use Bearer format")
        else:
            logger.error("No Authorization header present in request!")
            
        # Make the actual request
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            _executor, 
            lambda: requests.post(url, headers=headers, json=json, timeout=timeout)
        )
        
        # Log response info
        logger.debug(f"API response status code: {response.status_code}")
        if response.status_code >= 400:
            logger.error(f"API error: {response.status_code} - {response.text}")
        else:
            logger.debug("API request completed successfully")
            
        return response
        
    except Exception as e:
        logger.error(f"Error in API request: {str(e)}")
        # Recreate a Response-like object to handle the error
        
        @dataclass
        class ErrorResponse:
            status_code: int = 500
            text: str = ""
            
            def json(self):
                return {"error": self.text}
                
            def raise_for_status(self):
                raise requests.HTTPError(f"Error during API call: {self.text}")
                
        error_response = ErrorResponse(text=str(e))
        return error_response

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
    section_boundaries: Union[Dict, StructureAnalysis],
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

    # Handle either dict or StructureAnalysis object
    discovered_sections = []
    if isinstance(section_boundaries, dict):
        discovered_sections = section_boundaries.get("discovered_sections", section_boundaries.get("sections", []))
    else:
        # It's a StructureAnalysis object
        discovered_sections = section_boundaries.sections

    for section in discovered_sections:
        # Get section details based on section type
        section_name = "Unknown"
        section_line_ids = []
        if isinstance(section, dict):
            section_name = section.get("name", "Unknown")
            section_line_ids = section.get("line_ids", [])
        else:
            # It's a ReceiptSection object
            section_name = section.name
            section_line_ids = section.line_ids
        
        # Get words for this section
        section_lines = [
            line for line in receipt_lines 
            if (hasattr(line, 'line_id') and line.line_id in section_line_ids) or
               (isinstance(line, dict) and line.get('line_id') in section_line_ids)
        ]
        section_words = [
            word for word in receipt_words 
            if (hasattr(word, 'line_id') and word.line_id in section_line_ids) or
               (isinstance(word, dict) and word.get('line_id') in section_line_ids)
        ]
        
        if not section_words:
            # Skip empty sections
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
                        f"You label words in the {section_name} section of receipts, "
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
                        f"Timeout processing section {section_name}, "
                        f"attempt {attempt + 1}/3"
                    )
                    continue  # Try again
                except requests.RequestException as e:
                    if attempt == 2:  # Last attempt
                        raise  # Re-raise the error
                    print(
                        f"Error processing section {section_name}, "
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
            raise ValueError(f"Error processing section {section_name}: {str(e)}")

    # Combine results
    if not all_labels:
        raise ValueError("No labels were generated for any section.")
    
    # Extract and combine the analysis reasoning from all sections
    section_reasoning = []
    section_analysis_reasoning = []
    
    # We need to collect both section-level reasoning and GPT's analysis_reasoning for each section
    for i, section in enumerate(discovered_sections):
        # Get section name based on section type
        section_name = "Unknown"
        if isinstance(section, dict):
            section_name = section.get("name", "Unknown")
            section_reasoning_text = section.get("reasoning", "No reasoning provided")
        else:
            # It's a ReceiptSection object
            section_name = section.name
            section_reasoning_text = section.reasoning if hasattr(section, 'reasoning') else "No reasoning provided"
        
        # Add section structural reasoning
        section_reasoning.append(f"{section_name}: {section_reasoning_text}")
        
        # Get any available analysis_reasoning from GPT responses
        try:
            # Parse the response to extract metadata
            response_text = responses[i] if i < len(responses) else None
            if response_text:
                try:
                    # Extract the JSON part from the response
                    response_data = loads(response_text)
                    if "choices" in response_data and response_data["choices"]:
                        message_content = response_data["choices"][0].get("message", {}).get("content", "")
                        try:
                            parsed_content = loads(message_content)
                            if isinstance(parsed_content, dict) and "metadata" in parsed_content:
                                section_ar = parsed_content["metadata"].get("analysis_reasoning")
                                if section_ar:
                                    section_analysis_reasoning.append(f"{section_name}: {section_ar}")
                        except (JSONDecodeError, KeyError):
                            # If parsing fails, we just won't have this section's analysis reasoning
                            pass
                except (JSONDecodeError, KeyError):
                    # If parsing fails, we just won't have this section's analysis reasoning
                    pass
        except Exception:
            # We don't want to fail because of analysis reasoning extraction
            pass
    
    # Create a comprehensive analysis reasoning
    combined_reasoning = (
        f"Receipt analysis identified {len(discovered_sections)} "
        f"sections. Labels were applied based on content patterns, position, and business context. "
    )
    
    # Add section-level structural reasoning
    if section_reasoning:
        combined_reasoning += f"Section structural reasoning: {' | '.join(section_reasoning)}. "
    
    # Add GPT's analysis reasoning for each section
    if section_analysis_reasoning:
        combined_reasoning += f"Section labeling reasoning: {' | '.join(section_analysis_reasoning)}."
    else:
        combined_reasoning += "Detailed reasoning for each label is provided at the individual word level."

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
            "analysis_reasoning": combined_reasoning,
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

async def gpt_request_spatial_currency_analysis(
    receipt: Receipt,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    currency_contexts: List[Dict],
    gpt_api_key: Optional[str] = None,
) -> Tuple[Dict, str, str]:
    """
    Request OpenAI GPT to analyze currency amounts with spatial context.
    
    Args:
        receipt: Receipt object with metadata
        receipt_lines: List of ReceiptLine objects
        receipt_words: List of ReceiptWord objects
        currency_contexts: List of dictionaries with currency amounts and spatial context
        gpt_api_key: Optional OpenAI API key
    
    Returns:
        Tuple[Dict, str, str]: The formatted response from the OpenAI API, the query
            sent to OpenAI API, and the raw response from OpenAI API.
    """
    import os
    import logging
    logger = logging.getLogger(__name__)
    
    # Debug logging for API key detection
    logger.info("--- GPT SPATIAL ANALYSIS API KEY DEBUG ---")
    
    env_key = os.getenv("OPENAI_API_KEY")
    if env_key:
        # Only log first and last few characters for security
        masked_key = env_key[:4] + "..." + env_key[-4:] if len(env_key) > 8 else "***"
        logger.info(f"ENV variable OPENAI_API_KEY is set: {masked_key}")
    else:
        logger.warning("ENV variable OPENAI_API_KEY is NOT set")
        
    if gpt_api_key:
        masked_key = gpt_api_key[:4] + "..." + gpt_api_key[-4:] if len(gpt_api_key) > 8 else "***"
        logger.info(f"Function parameter API key: {masked_key}")
    else:
        logger.warning("No API key provided as parameter")
        
    # Log where we're looking for the API key
    if not gpt_api_key and not env_key:
        logger.error("No API key available from any source!")
    elif gpt_api_key:
        logger.info("Using provided parameter API key")
    else:
        logger.info("Using environment variable API key")
        
    logger.info("------------------------------")
    
    if not gpt_api_key and not os.getenv("OPENAI_API_KEY"):
        logger.error("The OPENAI_API_KEY environment variable is not set and no key was provided.")
        raise ValueError("The OPENAI_API_KEY environment variable is not set.")
    if gpt_api_key:
        os.environ["OPENAI_API_KEY"] = gpt_api_key
        
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
    }
    
    # Generate the prompt with spatial context information
    logger.info(f"Generating prompt with {len(currency_contexts)} currency contexts")
    
    # Debug currency contexts
    for i, ctx in enumerate(currency_contexts[:3]):  # Log first 3 for brevity
        logger.info(f"Context {i+1}: amount={ctx.get('amount')}, left_text={ctx.get('left_text')}")
    
    try:
        query = _llm_prompt_spatial_currency_analysis(receipt, currency_contexts)
        logger.info(f"Generated prompt of length {len(query)}")
        
        payload = {
            "model": MODEL,  # Using a more capable model for spatial understanding
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert receipt analyzer that understands how spatial layout of text relates to "
                        "the meaning of currency amounts on receipts. You identify line items, subtotals, taxes, "
                        "and totals based on their position and surrounding context."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "temperature": 0.3,  # Lower temperature for more consistent analysis
        }
        
        logger.info("Making API request to OpenAI")
        response = await _async_post(url, headers=headers, json=payload, timeout=60)
        logger.info(f"Received response with status code: {response.status_code if hasattr(response, 'status_code') else 'unknown'}")
        
        # Validate the response
        try:
            validated_result, validation_errors = _validate_gpt_response_spatial_currency(response)
            if validation_errors:
                logger.error(f"Validation failed with errors: {validation_errors}")
                # Return an empty dict as the result if validation failed
                return {}, query, response.text if hasattr(response, 'text') else str(response)
            
            logger.info("Response validation successful")
            return validated_result, query, response.text if hasattr(response, 'text') else str(response)
        except Exception as e:
            logger.error(f"Error validating response: {str(e)}")
            return {}, query, response.text if hasattr(response, 'text') else str(response)
            
    except Exception as e:
        logger.error(f"Error in spatial currency analysis: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {}, "", ""

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
        line_words = [word for word in receipt_words if word.line_id == line.line_id]
        line_text = " ".join(word.text for word in line_words)
        formatted_lines.append(f"Line {line.line_id}: {line_text}")

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
        "   - Detailed reasoning for why this is a valid section\n\n"
        "Return this JSON structure:\n"
        "{\n"
        '  "sections": [\n'
        "    {\n"
        '      "name": "header",\n'
        '      "line_ids": [1, 2, 3],\n'
        '      "spatial_patterns": [\n'
        '        {"pattern_type": "alignment", "description": "top of receipt"}\n'
        "      ],\n"
        '      "content_patterns": [\n'
        '        {"pattern_type": "business_info", "description": "contains business name"}\n'
        "      ],\n"
        '      "reasoning": "This section contains the business name and is located at the top of the receipt."\n'
        "    }\n"
        "  ],\n"
        '  "overall_reasoning": "The receipt is divided into logical sections based on content and layout."\n'
        "}\n\n"
        "Return ONLY the JSON object. No other text."
    )


def _llm_prompt_field_labeling_section(
    receipt: Receipt,
    section_lines: List[ReceiptLine],
    section_words: List[ReceiptWord],
    section_info: Union[Dict, ReceiptSection],
    places_api_data: Optional[Dict] = None,
) -> str:
    """Generate a prompt for the field labeling task for a specific section.

    Args:
        receipt: Receipt object
        section_lines: List of receipt lines for this section
        section_words: List of receipt words for this section
        section_info: Dictionary or ReceiptSection object containing section metadata
        places_api_data: Optional Places API data dictionary

    Returns:
        str: Generated prompt
    """
    # Get section name and reasoning based on type
    section_name = ""
    section_reasoning = ""
    if isinstance(section_info, dict):
        section_name = section_info.get("name", "Unknown Section")
        section_reasoning = section_info.get("reasoning", "")
    else:
        # It's a ReceiptSection object
        section_name = section_info.name if hasattr(section_info, 'name') else "Unknown Section"
        section_reasoning = section_info.reasoning if hasattr(section_info, 'reasoning') else ""

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
        "BUSINESS_NAME": "Name of the business",
        "ADDRESS_LINE": "Part of the business address",
        "PHONE": "Phone number",
        "DATE": "Date of transaction",
        "TIME": "Time of transaction",
        "TRANSACTION_ID": "Unique transaction identifier",
        "STORE_ID": "Store or location identifier",
        "SUBTOTAL": "Subtotal amount",
        "TAX": "Tax amount",
        "TOTAL": "Total amount",
        "PAYMENT_STATUS": "Payment status (e.g., APPROVED)",
        "PAYMENT_METHOD": "Method of payment",
        "CASHIER_ID": "Cashier identifier",
        "ITEM": "Individual item on receipt",
        "ITEM_QUANTITY": "Quantity of an item",
        "ITEM_PRICE": "Price of an item",
        "ITEM_TOTAL": "Total for an item",
        "DISCOUNT": "Discount amount",
        "COUPON": "Coupon code or amount",
        "LOYALTY_ID": "Loyalty program identifier",
        "MEMBERSHIP_ID": "Membership identifier",
    }

    # Default examples for any section type
    default_examples = [
        '{"text": "COSTCO WHOLESALE", "label": "BUSINESS_NAME", "reasoning": "Matches business name from Places API and appears at top of receipt"}',
        '{"text": "5700 Lindero Canyon Rd", "label": "ADDRESS_LINE", "reasoning": "Matches format of street address and verified against Places API"}',
        '{"text": "12/17/2024", "label": "DATE", "reasoning": "Matches date format MM/DD/YYYY and appears in transaction details section"}',
        '{"text": "17:19", "label": "TIME", "reasoning": "Matches time format HH:MM and appears next to date"}',
        '{"text": "TOTAL", "label": "TOTAL", "reasoning": "Clear total indicator in payment section with amount following"}'
    ]

    # Select examples based on section type - using reasoning instead of confidence
    if "business_info" in section_name.lower():
        examples = [
            '{"text": "COSTCO WHOLESALE", "label": "BUSINESS_NAME", "reasoning": "Matches business name from Places API and appears at top of receipt"}',
            '{"text": "5700 Lindero Canyon Rd", "label": "ADDRESS_LINE", "reasoning": "Matches format of street address and verified against Places API"}',
            '{"text": "#117", "label": "STORE_ID", "reasoning": "Store identifier format that appears near business name"}'
        ]
    elif "payment" in section_name.lower():
        examples = [
            '{"text": "TOTAL", "label": "TOTAL", "reasoning": "Clear total indicator in payment section"}',
            '{"text": "$63.27", "label": "TOTAL", "reasoning": "Currency format that appears after TOTAL indicator"}',
            '{"text": "APPROVED", "label": "PAYMENT_STATUS", "reasoning": "Payment confirmation status message"}'
        ]
    elif "transaction" in section_name.lower():
        examples = [
            '{"text": "12/17/2024", "label": "DATE", "reasoning": "Matches date format MM/DD/YYYY in transaction section"}',
            '{"text": "17:19", "label": "TIME", "reasoning": "Matches time format HH:MM near the date"}',
            '{"text": "Tran ID#: 12345", "label": "TRANSACTION_ID", "reasoning": "Clear transaction identifier format"}'
        ]
    else:
        examples = default_examples

    return (
        f"Label words in the {section_name.upper()} section of this receipt.\n\n"
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
        "4. Provide clear reasoning for each label assignment\n"
        "5. Mark requires_review true if you're unsure about any labels\n"
        "6. Explain overall labeling decisions in analysis_reasoning\n\n"
        "Return this JSON structure:\n"
        "{\n"
        '  "labeled_words": [\n'
        "    {\n"
        '      "text": "exact text from receipt",\n'
        '      "label": "label from list above",\n'
        '      "reasoning": "Detailed explanation of why this label was chosen, including context and verification"\n'
        "    }\n"
        "  ],\n"
        '  "metadata": {\n'
        '    "requires_review": false,\n'
        '    "review_reasons": [],\n'
        '    "analysis_reasoning": "Overall explanation of labeling decisions and any notable patterns or issues"\n'
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
        "6. Provide detailed reasoning for each line item and overall analysis\n\n"
        "Return this JSON structure:\n"
        "{\n"
        '  "line_items": [\n'
        "    {\n"
        '      "description": "complete item description",\n'
        '      "quantity": {"amount": number, "unit": "string"},\n'
        '      "price": {"unit_price": number, "extended_price": number},\n'
        '      "reasoning": "Detailed explanation of how this item was parsed and validated",\n'
        '      "line_ids": [number]\n'
        "    }\n"
        "  ],\n"
        '  "analysis": {\n'
        '    "total_found": number,\n'
        '    "subtotal": number,\n'
        '    "tax": number,\n'
        '    "total": number,\n'
        '    "discrepancies": [string],\n'
        '    "reasoning": "Comprehensive explanation of line item analysis, calculations, and any issues found"\n'
        "  }\n"
        "}\n\n"
        "Return ONLY the JSON object. No other text."
    )

def _llm_prompt_spatial_currency_analysis(receipt, currency_contexts):
    """Generate a prompt for the OpenAI API to analyze currency amounts with spatial context."""
    prompt = """You are an expert receipt analyzer, specialized in identifying line items, subtotals, taxes, and totals on receipts from any business type.
    
I'll provide you with a list of currency amounts detected in a receipt image, along with the text to the LEFT of each amount, the FULL LINE text, and their line positions. Your task is to analyze the entire receipt structure and classify each amount based on its context and position.

IMPORTANT: Different businesses use different terms for financial summary fields:
- For TOTAL: Look for terms like "Total", "Balance Due", "Amount Due", "Pay This Amount", "Credit", "Grand Total", "Balance", etc.
- For SUBTOTAL: Look for terms like "Subtotal", "Sub Total", "Sub-total", "Net", "Merchandise", etc.
- For TAX: Look for terms like "Tax", "VAT", "GST", "HST", "Sales Tax", etc.

Also consider the POSITION and CONTEXT of amounts in the receipt:
- Financial summary fields (subtotal, tax, total) typically appear near the bottom of the receipt
- The TOTAL is usually the last or one of the last currency amounts on the receipt
- Line items typically appear in sequence before the summary section
- Even if there's no clear label, if a currency amount appears after the list of items, it may be a subtotal or total

Here are the currency amounts with their spatial context information:

"""

    # Add currency amounts and their coordinates as numbered items
    for i, item in enumerate(currency_contexts, 1):
        amount_id = item.get("id", i)
        amount = item.get("amount", "")
        left_text = item.get("left_text", "")
        full_line = item.get("full_line", "")
        line_id = item.get("line_id", "")
        
        prompt += f"{amount_id}. Amount: {amount}, Text to Left: \"{left_text}\", Full Line: \"{full_line}\", Line: {line_id}\n"

    prompt += """
Analyze each amount and classify it into one of these categories:
- LINE_ITEM: A specific product or service with its price
- SUBTOTAL: Sum of line items before tax
- TAX: Any tax amount
- TOTAL: The final total amount to be paid (look for "Balance Due", "Credit", "Amount Due", etc.)
- DISCOUNT: Any reduction in price
- OTHER: Amounts that don't fit the above categories (like deposits, tips, etc.)

For each currency amount, consider:
1. The text to the left and full line text (check for keywords that indicate items or financial summary fields)
2. The position in the receipt (financial summaries usually appear near the bottom)
3. The value of the amount (the total is typically larger than individual line items)
4. The pattern of values (e.g., a set of smaller amounts, followed by a subtotal, tax, and total)

CRITICAL: For line items, extract meaningful descriptions:
1. DO NOT use generic "Item on line X" descriptions unless absolutely necessary
2. For each line item, analyze both the "Text to Left" and "Full Line" fields to find the most descriptive product name
3. Look for patterns like product names, brands, SKUs, or product categories
4. If you see quantity information (like "2x" or "1.5 lb"), include it in the description
5. Clean up the description by removing irrelevant symbols or repeated information
6. If multiple items appear on the same line, try to separate them
7. For grocery or retail receipts, look for product names or categories

Return your analysis as a JSON object with these fields:
1. "classification": A list of objects, each containing:
   - "amount": The currency amount (numeric value)
   - "category": One of the categories above (LINE_ITEM, SUBTOTAL, TAX, TOTAL, DISCOUNT, OTHER)
   - "reasoning": Why you classified it this way
   
2. "line_items": A list of objects containing:
   - "description": The meaningful item description extracted from context (NOT just "Item on line X")
   - "amount": The price of the item (numeric value)
   - "quantity": If detected, otherwise null
   - "unit_price": If detected, otherwise null
   - "line_id": The line number where this item appears

3. "financial_summary": An object containing:
   - "subtotal": The subtotal amount if found, otherwise null
   - "tax": The tax amount if found, otherwise null
   - "total": The total amount if found, otherwise null

4. "reasoning": Overall explanation of your classification logic, including how you identified the financial summary fields

CRITICAL: Identify a total amount for the receipt if present. Use contextual clues like position, relative value, and terms like "Balance Due" or "Credit". In most receipts, the total appears near the end.

Be thorough and accurate with your analysis. Return ONLY valid JSON with no additional text."""

    return prompt

def _validate_gpt_response_structure_analysis(response: Response) -> Dict:
    """Validates the response from the OpenAI API for structure analysis.
    
    Args:
        response (Response): The response from the OpenAI API.
        
    Returns:
        Dict: A dictionary containing the validated and formatted content from the API.
        
    Raises:
        ValueError: If the response is invalid or doesn't match the expected structure.
    """
    if not isinstance(response, Response):
        raise ValueError("Invalid response object.")
    
    try:
        resp_json = response.json()
    except JSONDecodeError:
        raise ValueError("Invalid JSON response.")
    
    if "choices" not in resp_json or len(resp_json["choices"]) == 0:
        raise ValueError("No choices in response.")
    
    try:
        content = resp_json["choices"][0]["message"]["content"]
        
        # Handle potential JSON code blocks
        if "```json" in content:
            match = re.search(r"```json(.*?)```", content, flags=re.DOTALL)
            content = match.group(1) if match else content
        
        # Parse the JSON content
        content_json = json.loads(content.strip())
        
        # Validate overall structure
        # Handle both legacy ("discovered_sections") and new format ("sections")
        if "sections" not in content_json and "discovered_sections" not in content_json:
            raise ValueError("Missing 'sections' or 'discovered_sections' in response.")
            
        # Get the sections using either key
        sections_data = content_json.get("sections", content_json.get("discovered_sections", []))
        if not isinstance(sections_data, list):
            raise ValueError("'sections' must be a list.")
            
        # If the response uses the old key, update it to the new key for consistency
        if "discovered_sections" in content_json and "sections" not in content_json:
            content_json["sections"] = content_json["discovered_sections"]
            
        if "overall_reasoning" not in content_json:
            raise ValueError("Missing 'overall_reasoning' in response.")
        if not isinstance(content_json["overall_reasoning"], str):
            raise ValueError("'overall_reasoning' must be a string.")
        
        # Validate each section
        for section in sections_data:
            # For dictionaries we check keys directly
            if isinstance(section, dict):
                if "name" not in section:
                    raise ValueError(
                        "Missing 'name' in section."
                    )
                if "line_ids" not in section:
                    raise ValueError(
                        "Missing 'line_ids' in section."
                    )
                if "spatial_patterns" not in section:
                    raise ValueError(
                        "Missing 'spatial_patterns' in section."
                    )
                if "content_patterns" not in section:
                    raise ValueError(
                        "Missing 'content_patterns' in section."
                    )
                if "reasoning" not in section:
                    raise ValueError(
                        "Missing 'reasoning' in section."
                    )

                if not isinstance(section["name"], str):
                    raise ValueError("'name' must be a string.")
                if not isinstance(section["line_ids"], list):
                    raise ValueError("'line_ids' must be a list.")
                if not isinstance(section["spatial_patterns"], list):
                    raise ValueError("'spatial_patterns' must be a list.")
                if not isinstance(section["content_patterns"], list):
                    raise ValueError("'content_patterns' must be a list.")
                if not isinstance(section["reasoning"], str):
                    raise ValueError("'reasoning' must be a string.")
            # For ReceiptSection objects we check attributes 
            elif hasattr(section, 'name') and hasattr(section, 'line_ids'):
                if not isinstance(section.name, str):
                    raise ValueError("'name' must be a string.")
                if not isinstance(section.line_ids, list):
                    raise ValueError("'line_ids' must be a list.")
                if not hasattr(section, 'spatial_patterns') or not isinstance(section.spatial_patterns, list):
                    raise ValueError("'spatial_patterns' must be a list.")
                if not hasattr(section, 'content_patterns') or not isinstance(section.content_patterns, list):
                    raise ValueError("'content_patterns' must be a list.")
                if not hasattr(section, 'reasoning') or not isinstance(section.reasoning, str):
                    raise ValueError("'reasoning' must be a string.")
            else:
                raise ValueError("Sections must be dictionaries or ReceiptSection objects.")
            
        return content_json
        
    except KeyError as e:
        raise ValueError(f"Missing key in response: {str(e)}")
    except JSONDecodeError:
        # Log the content for debugging
        logger.error(f"Failed to parse JSON content: {content if 'content' in locals() else 'No content'}")
        raise ValueError("Could not parse content as JSON.")


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
            required_word_keys = ["text", "label", "reasoning"]
            if not all(key in word for key in required_word_keys):
                raise ValueError(f"Labeled word missing required keys: {required_word_keys}")
            if not isinstance(word["reasoning"], str):
                raise ValueError("Word reasoning must be a string.")

        # Validate metadata structure
        required_metadata_keys = ["requires_review", "review_reasons", "analysis_reasoning"]
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

            required_item_keys = ["description", "quantity", "price", "reasoning", "line_ids"]
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
        required_analysis_keys = ["total_found", "subtotal", "tax", "total", "discrepancies", "reasoning"]
        if not all(key in analysis for key in required_analysis_keys):
            raise ValueError(f"Analysis missing required keys: {required_analysis_keys}")

        return parsed

    except JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in response: {e}\nResponse text: {response.text}")
    except Exception as e:
        raise ValueError(f"Error validating response: {e}\nResponse text: {response.text}")

def _validate_gpt_response_spatial_currency(response):
    """Validate the GPT response from spatial currency analysis."""
    validation_errors = []
    
    try:
        # First check if we have a Response object or already parsed data
        if hasattr(response, 'json'):
            try:
                response_data = response.json()
            except Exception as e:
                validation_errors.append(f"Failed to parse response as JSON: {str(e)}")
                return None, validation_errors
        else:
            response_data = response  # Assume it's already parsed
        
        # Check for the required JSON structure from OpenAI API
        if not isinstance(response_data, dict):
            validation_errors.append("Response is not a dictionary")
            return None, validation_errors
        
        if "choices" not in response_data:
            validation_errors.append("No 'choices' field in response")
            return None, validation_errors
        
        if not response_data["choices"] or not isinstance(response_data["choices"], list):
            validation_errors.append("'choices' field is empty or not a list")
            return None, validation_errors
        
        choice = response_data["choices"][0]
        if "message" not in choice:
            validation_errors.append("No 'message' field in first choice")
            return None, validation_errors
        
        if "content" not in choice["message"]:
            validation_errors.append("No 'content' field in message")
            return None, validation_errors
        
        content = choice["message"]["content"]
        
        # Extract JSON from content if it's wrapped in a code block
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        try:
            parsed_content = json.loads(content)
        except json.JSONDecodeError as e:
            validation_errors.append(f"Failed to parse content as JSON: {str(e)}")
            return None, validation_errors
        
        if not isinstance(parsed_content, dict):
            validation_errors.append("Parsed content is not a dictionary")
            return None, validation_errors
        
        # Check required keys in the parsed content
        required_keys = ["classification", "line_items"]
        for key in required_keys:
            if key not in parsed_content:
                validation_errors.append(f"Missing required key '{key}' in response")
                return None, validation_errors
        
        # Check classification format
        if not isinstance(parsed_content["classification"], list):
            validation_errors.append("'classification' is not a list")
            return None, validation_errors
        
        # Validate each classification item
        for i, item in enumerate(parsed_content["classification"]):
            if not isinstance(item, dict):
                validation_errors.append(f"Classification item {i} is not a dictionary")
                continue
                
            if "amount" not in item:
                validation_errors.append(f"Classification item {i} missing 'amount'")
            
            if "category" not in item:
                validation_errors.append(f"Classification item {i} missing 'category'")
            elif item["category"] not in ["LINE_ITEM", "SUBTOTAL", "TAX", "TOTAL", "DISCOUNT", "OTHER"]:
                validation_errors.append(f"Invalid category '{item['category']}' in classification item {i}")
                
            # Check for reasoning (optional but recommended)
            if "reasoning" not in item:
                logging.warning(f"Classification item {i} missing 'reasoning' field")
        
        # Validate line_items format
        if not isinstance(parsed_content["line_items"], list):
            validation_errors.append("'line_items' is not a list")
            return None, validation_errors
        
        # Validate each line item
        for i, item in enumerate(parsed_content["line_items"]):
            if not isinstance(item, dict):
                validation_errors.append(f"Line item {i} is not a dictionary")
                continue
                
            if "amount" not in item:
                validation_errors.append(f"Line item {i} missing 'amount'")
                
            if "description" not in item:
                validation_errors.append(f"Line item {i} missing 'description'")
                
            # Check for line_id (optional)
            if "line_id" not in item:
                logging.warning(f"Line item {i} missing 'line_id' field")
        
        # Check for financial_summary (optional but recommended)
        if "financial_summary" in parsed_content:
            if not isinstance(parsed_content["financial_summary"], dict):
                validation_errors.append("'financial_summary' is not a dictionary")
            else:
                # Check for expected fields in financial_summary
                for field in ["subtotal", "tax", "total"]:
                    if field not in parsed_content["financial_summary"]:
                        logging.warning(f"Financial summary missing '{field}' field")
        else:
            logging.warning("Response missing 'financial_summary' field")
        
        # Check for overall reasoning (optional)
        if "reasoning" not in parsed_content:
            logging.warning("Response missing overall 'reasoning' field")
        
        # Determine if validation passed
        if len(validation_errors) == 0:
            return parsed_content, []
        else:
            return None, validation_errors
            
    except Exception as e:
        validation_errors.append(f"Unexpected error during validation: {str(e)}")
        import traceback
        logging.error(f"Validation error: {traceback.format_exc()}")
        return None, validation_errors


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
                    "reasoning": labeled_word.get("reasoning")
                })
        
        # If no exact match, try matching individual words
        if not matches_found:
            matched_words = []
            for word in words:
                if word in normalized_words:
                    matched_words.extend(normalized_words[word])
            
            if matched_words:
                # Adjust reasoning based on how many words were matched
                reasoning_adjustment = len(matched_words) / len(words)
                for word in matched_words:
                    original_reasoning = labeled_word.get("reasoning", "")
                    adjusted_reasoning = f"{original_reasoning} (Partial match confidence: {reasoning_adjustment:.2f})"
                    mapped_labels.append({
                        "word_id": word.word_id if hasattr(word, 'word_id') else word.get("word_id"),
                        "line_id": word.line_id if hasattr(word, 'line_id') else word.get("line_id"),
                        "label": labeled_word.get("label"),
                        "reasoning": adjusted_reasoning
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
