# infra/lambda_layer/python/dynamo/data/_gpt.py
import re
from json import JSONDecodeError, dumps, loads
from os import environ, getenv
from typing import Any, Dict, List, Optional, cast

import requests
from requests.models import Response

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


def gpt_request_tagging_validation(
    receipt: Receipt,
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    receipt_word_tags: list[ReceiptWordLabel],
    gpt_api_key=None,
) -> tuple[List[Dict[str, Any]], str, str]:
    """
    Makes a request to the OpenAI API to validate the tagging of a receipt.

    Returns:
        tuple[dict, str, str]: The formatted response from the OpenAI API, the
            query sent to OpenAI API, and the raw response from OpenAI API.
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
    query = _llm_prompt_tagging_validation(
        receipt, receipt_lines, receipt_words, receipt_word_tags
    )
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": "You extract structured data from text.",
            },
            {"role": "user", "content": query},
        ],
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)

    return (
        _validate_gpt_response_tagging_validation(response),
        query,
        response.text,
    )


def gpt_request_initial_tagging(
    receipt: Receipt, receipt_words: list[ReceiptWord], gpt_api_key=None
) -> tuple[dict, str, str]:
    """Makes a request to the OpenAI API to label the receipt.

    Returns:
        tuple[dict, str, str]: The formatted response from the OpenAI API, the
            query sent to OpenAI API, and the raw response from OpenAI API.
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
    query = _llm_prompt_initial_tagging(receipt, receipt_words)
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": "You extract structured data from text.",
            },
            {"role": "user", "content": query},
        ],
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)

    return (
        _validate_gpt_response_initial_tagging(response),
        query,
        response.text,
    )


def _validate_gpt_response_initial_tagging(
    response: Response,
) -> Dict[str, Any]:
    """Validates the response from the OpenAI API.

    Validate the response from OpenAI API and raise an error if the response
    is not formatted as expected. If the response is valid, return the parsed
    content.

    Args:
        response (Response): The response from the OpenAI API

    Returns:
        dict: The validated response (parsed JSON) from the OpenAI API.
    """
    response.raise_for_status()
    data = response.json()
    if "choices" not in data or not data["choices"]:
        raise ValueError("The response does not contain any choices.")
    if not isinstance(data["choices"], list):
        raise ValueError("The response choices are not a list.")
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
        parsed_content: Dict[str, Any] = loads(
            content.replace("```json", "").replace("```", "")
        )
        content = parsed_content
    except JSONDecodeError:
        raise ValueError(
            f"The response message content is not valid JSON.\n{response.text}"
        )
    # Ensure all keys in the parsed content are strings.
    if not all(isinstance(key, str) for key in content.keys()):
        raise ValueError("The response message content keys are not strings.")
    # For each field, if its value is non-empty, check that it contains both
    # "l" and "w".
    for key, value in content.items():
        if value:  # Only check non-empty values.
            if isinstance(value, list):
                if not all(
                    isinstance(tag, dict) and "l" in tag and "w" in tag
                    for tag in value
                ):
                    raise ValueError(
                        "The response message content values do not contain "
                        "'l' and 'w'."
                    )
            elif isinstance(value, dict):
                if not ("l" in value and "w" in value):
                    raise ValueError(
                        "The response message content values do not contain "
                        "'l' and 'w'."
                    )
            else:
                raise ValueError(
                    "The response message content values must be a list or "
                    "a dict."
                )
    return cast(Dict[str, Any], content)


def _validate_gpt_response_tagging_validation(
    response: Response,
) -> List[Dict[str, Any]]:
    """Validates the response from the OpenAI API.

    Validate the response from OpenAI API and raise an error if the response
    is not formatted as expected. If the response is valid, return the parsed
    content.

    Args:
        response (Response): The response from the OpenAI API

    Returns:
        dict: The validated response (parsed JSON) from the OpenAI API.
    """
    response.raise_for_status()
    data = response.json()
    if "choices" not in data or not data["choices"]:
        raise ValueError("The response does not contain any choices.")
    if not isinstance(data["choices"], list):
        raise ValueError("The response choices are not a list.")
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
        # Check if the content contains a JSON code block.
        if "```json" in content:
            # Look for the JSON code block using a regular expression.
            match = re.search(r"```json(.*?)```", content, flags=re.DOTALL)
            if match:
                # Extract the JSON text between the markers, stripping any
                # extra whitespace.
                json_text = match.group(1).strip()
            else:
                # Fallback: if the marker is present but the regex didn't
                # match, use the full content.
                json_text = content
        else:
            # No markers found; assume the entire content is JSON.
            json_text = content

        # Attempt to parse the extracted JSON.
        content = loads(json_text)
    except JSONDecodeError as e:
        raise ValueError(
            f"The response message content is not valid JSON.\n{response.text}"
        ) from e
    if not isinstance(content, list):
        raise ValueError("The response message content is not a list.")
    for item in content:
        if not isinstance(item, dict):
            raise ValueError("The response items are not dictionaries.")
        if not all(
            key in item
            for key in [
                "line_id",
                "word_id",
                "initial_tag",
                "revised_tag",
                "confidence",
                "flag",
            ]
        ):
            raise ValueError(
                "The response items do not contain the expected fields."
            )
    return content


def _reduce_precision(value: tuple, precision: int = 2) -> tuple:
    """Reduces the precision of the word's centroid.

    The word's centroid is a tuple with two float values. This function reduces
    the precision of those float values to the specified precision.

    Args:
        value (tuple): The centroid of the word.
        precision (int): The number of decimal places to keep.

    Returns:
        tuple: The centroid with reduced precision.
    """
    return tuple(round(v, precision) for v in value)


def _llm_prompt_initial_tagging(
    receipt: Receipt, receipt_words: list[ReceiptWord]
) -> str:
    """Generates a prompt for the ChatGPT API based on the receipt.

    Returns:
        str: The prompt for the ChatGPT API.
    """
    ocr_text = dumps(
        {
            "r": dict(receipt),
            "w": [
                {
                    "t": word.text,
                    "c": list(_reduce_precision(word.calculate_centroid(), 4)),
                    "l": word.line_id,
                    "w": word.word_id,
                }
                for word in receipt_words
            ],
        }
    )
    return (
        "\nYou are a helpful assistant that extracts structured data from a "
        "receipt.\n"
        "\nBelow is a sample of the JSON you will receive. Notice that each "
        "'word' has a 'text', a 'centroid' [x, y], and a line/word ID (l, "
        "w):\n"
        "\n```json\n"
        "\n{"
        '  "receipt": {\n'
        '    "receipt_id": 123\n'
        "    // more receipt-level fields\n"
        "},\n"
        '  "w": [\n'
        "    {\n"
        '      "t": "BANANAS",\n'
        '      "c": [0.1234, 0.5678],\n'
        '      "l": 0,\n'
        '      "w": 0\n'
        "},\n"
        "    {\n"
        '      "t": "1.99",\n'
        '      "c": [0.2345, 0.6789],\n'
        '      "l": 0,\n'
        '      "w": 1\n'
        "}\n"
        "    // ...\n"
        "]\n"
        "}\n"
        "The receipt's OCR text is:\n\n"
        f"{ocr_text}\n\n"
        "Your task:\n"
        "   Identify the following fields in the text:\n"
        "   - store_name (string)\n"
        "   - date (string)\n"
        "   - time (string)\n"
        "   - phone_number (string)\n"
        "   - total_amount (number)\n"
        "   - taxes (number)\n"
        "   - address (string)\n"
        "   - For line items, return three separate fields:\n"
        "       * line_item: includes all words that contribute to any line "
        "item\n"
        "       * line_item_name: includes words that contribute to the item "
        "name\n"
        "       * line_item_price: includes words that contribute to the item "
        "price\n\n"
        "   Instead of returning the text or centroid, **return an array of "
        '{"l": <line_id>, "w": <word_id>} '
        "   for each field.**\n"
        "   - For example, if you think the first two words (line_id=0, "
        "word_id=0 and line_id=0, word_id=1) "
        "     make up 'store_name', return:\n"
        '     "store_name": [\n'
        '       {"l": 0, "w": 0},\n'
        '       {"l": 0, "w": 1}\n'
        "]\n"
        "   - If you cannot find a particular field, return an empty array "
        "for it.\n\n"
        "**Output Requirements**:\n"
        " - Output must be valid JSON.\n"
        " - Do not return additional keys or text.\n"
        ' - Do not invent new {"l", "w"} pairs. Only use those provided in '
        "the 'words' list.\n"
        " - If none found, return empty arrays.\n\n"
        "Example output:\n"
        "```json\n"
        "{\n"
        '  "store_name": [{"l": 0, "w": 0}, {"l": 0, "w": 1}],\n'
        '  "date": [{"l": 2, "w": 5}],\n'
        '  "time": [],\n'
        '  "phone_number": [],\n'
        '  "total_amount": [],\n'
        '  "taxes": [],\n'
        '  "address": [],\n'
        '  "line_item": [{"l": 5, "w": 1}],\n'
        '  "line_item_name": [{"l": 5, "w": 2}],\n'
        '  "line_item_price": [{"l": 5, "w": 3}]\n'
        "}\n"
        "```\n"
        "Return only this JSON structure. Nothing else.\n"
    )


def _llm_prompt_tagging_validation(
    receipt: Receipt,
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    receipt_word_tags: list[ReceiptWordLabel],
) -> str:
    """
    Generates a prompt string for validating and potentially revising
    receipt word tags.

    This function constructs a detailed prompt intended for a language
    model (such as ChatGPT) to review receipt data that includes receipt
    dimensions, lines, words, and their associated tags. It formats the
    receipt information as a JSON structure that describes:
      - The receipt metadata (e.g., width and height).
      - A list of receipt lines, where each line contains:
          - A unique line identifier and bounding box.
          - The full text of the line.
          - A list of tagged words, with each word including its
            identifier, text, bounding box, and a list of tag objects
            (each with a tag name and a validation flag).

    The prompt then provides instructions on how to generate a structured
    JSON output. The expected output should be a list of dictionaries,
    each containing:
      - "line_id": The unique identifier of the line.
      - "word_id": The unique identifier of the word.
      - "initial_tag": The tag originally provided in the data.
      - "revised_tag": The corrected tag suggested by the language model.
      - "confidence": A numeric confidence level (from 1 to 5) indicating
        certainty.
      - "flag": A status string ("ok" if the tag is correct, or
        "needs_review" if it is uncertain).

    Parameters:
        receipt (Receipt): The receipt object containing metadata such as
            width and height.
        receipt_lines (list[ReceiptLine]): A list of receipt line objects.
        receipt_words (list[ReceiptWord]): A list of receipt word objects.
        receipt_word_tags (list[ReceiptWordTag]): A list of tags associated
            with receipt words.

    Returns:
        str: A prompt string that includes:
            - A description of the expected JSON structure.
            - The receipt data formatted as a JSON string.
            - Detailed instructions for reviewing and revising word tags,
              ensuring the output is a structured JSON matching the
              specified schema.
    """
    receipt_dict = {
        "width": receipt.width,
        "height": receipt.height,
    }
    line_dicts = []

    for line in receipt_lines:
        # Filter words that have corresponding tags in receipt_word_tags
        words_in_line = [
            word
            for word in receipt_words
            if word.line_id == line.line_id
            and word.receipt_id == receipt.receipt_id
        ]

        # Find words that have tags
        words_in_line_with_tags = []
        for word in words_in_line:
            has_tags = any(
                tag.word_id == word.word_id
                and tag.line_id == word.line_id
                and tag.receipt_id == receipt.receipt_id
                for tag in receipt_word_tags
            )
            if has_tags:
                words_in_line_with_tags.append(word)

        tagged_words = []
        for word in words_in_line_with_tags:
            # For each word get its tags
            tags = [
                tag
                for tag in receipt_word_tags
                if tag.word_id == word.word_id
                and tag.line_id == word.line_id
                and tag.receipt_id == receipt.receipt_id
            ]
            tagged_words.append(
                {
                    "word_id": word.word_id,
                    "text": word.text,
                    "bounding_box": word.bounding_box,
                    "tags": [
                        {
                            "tag": tag.label,
                            "validated": tag.validation_status,
                        }
                        for tag in tags
                    ],
                }
            )

        line_dicts.append(
            {
                "line_id": line.line_id,
                "bounding_box": line.bounding_box,
                "text": line.text,
                "tagged_words": tagged_words,
            }
        )

    # Now, AFTER the loop, build the final JSON and return the full prompt:
    context_json = dumps({"receipt": receipt_dict, "lines": line_dicts})

    return (
        "You are provided with JSON data that conforms to the following "
        "structure:\n"
        "```json\n"
        "{\n"
        '    "receipt": {\n'
        '        "width": <integer>,\n'
        '        "height": <integer>\n'
        "},\n"
        '    "lines": [\n'
        "        {\n"
        '            "line_id": <integer>,\n'
        '            "bounding_box": {\n'
        '                "x": <integer>,\n'
        '                "y": <integer>,\n'
        '                "width": <integer>,\n'
        '                "height": <integer>\n'
        "},\n"
        '            "tagged_words": [\n'
        "                {\n"
        '                    "word_id": <integer>,\n'
        '                    "text": <string>,\n'
        '                    "bounding_box": {\n'
        '                        "x": <integer>,\n'
        '                        "y": <integer>,\n'
        '                        "width": <integer>,\n'
        '                        "height": <integer>\n'
        "},\n"
        '                    "tags": [\n'
        "                        {\n"
        '                            "tag": <string>,\n'
        '                            "validated": <boolean>\n'
        "}\n"
        "]\n"
        "}\n"
        "]\n"
        "}\n"
        "]\n"
        "}\n"
        "```\n"
        "\n"
        f"Here is the data (JSON):\n"
        f"{context_json}\n"
        "\n"
        "I'm expecting a structured output in JSON format with the "
        "following fields:\n"
        "```json\n"
        "[\n"
        "  {\n"
        '    "line_id": 1,\n'
        '    "word_id": 10,\n'
        '    "initial_tag": "phone_number",\n'
        '    "revised_tag": "phone_number",\n'
        '    "confidence": 5,\n'
        '    "flag": "ok"\n'
        "},\n"
        "  {\n"
        '    "line_id": 3,\n'
        '    "word_id": 11,\n'
        '    "initial_tag": "date",\n'
        '    "revised_tag": "time",\n'
        '    "confidence": 2,\n'
        '    "flag": "needs_review"\n'
        "}\n"
        "]\n"
        "```\n"
        "\n"
        "- The 'line_id' and 'word_id' fields are the unique identifiers for "
        "each line and word. They should match the data provided.\n"
        "- 'initial_tag' is the tag provided in the data.\n"
        "- 'revised_tag' is the corrected tag you suggest.\n"
        "- 'confidence' is your confidence level from 1 to 5.\n"
        "- 'flag' is 'ok' if the tag is correct, 'needs_review' if unsure.\n"
        "\n"
        'You must review each word in "tagged_words" and provide the '
        "correct tag. If the tag is correct, mark 'ok' and provide a high "
        "confidence level.\n"
        'Return all the "tagged_words" with the structure above.\n'
        "Do not create new 'line_id' or 'word_id' pairs. Only use the ones "
        "provided in the data.\n"
        "Return this JSON structure. Nothing else.\n"
    )


def gpt_request_structure_analysis(
    receipt: Receipt,
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    places_api_data: Dict[str, Any],
    gpt_api_key=None,
) -> tuple[dict, str, str]:
    """
    Makes a request to the OpenAI API to analyze the structure of a receipt.

    This function analyzes the spatial and content patterns in the receipt to
    identify natural sections and their relationships, using both the receipt
    data and Google Places API context.

    Args:
        receipt (Receipt): The receipt object containing metadata.
        receipt_lines (list[ReceiptLine]): List of receipt line objects.
        receipt_words (list[ReceiptWord]): List of receipt word objects.
        places_api_data (dict): Business context from Google Places API.
        gpt_api_key (str, optional): The OpenAI API key. Defaults to None.

    Returns:
        tuple[dict, str, str]: The formatted response from the OpenAI API, the
            query sent to OpenAI API, and the raw response from OpenAI API.
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
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": "You analyze receipt structure to identify natural "
                "sections based on spatial and content patterns, using "
                "business context from Google Places API.",
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


def _llm_prompt_structure_analysis(
    receipt: Receipt,
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    places_api_data: Dict[str, Any],
) -> str:
    """
    Generates a prompt for analyzing the structure of a receipt.

    Args:
        receipt (Receipt): The receipt object containing metadata.
        receipt_lines (list[ReceiptLine]): List of receipt line objects.
        receipt_words (list[ReceiptWord]): List of receipt word objects.
        places_api_data (dict): Business context from Google Places API.

    Returns:
        str: The formatted prompt string.
    """
    # Calculate receipt dimensions and statistics
    max_y = max(
        line.bounding_box["y"] + line.bounding_box["height"]
        for line in receipt_lines
    )
    max_x = max(
        line.bounding_box["x"] + line.bounding_box["width"]
        for line in receipt_lines
    )

    # Format lines with spatial information
    formatted_lines = []
    prev_y = None
    y_gap_threshold = max_y * 0.02  # 2% of receipt height
    current_group: List[str] = []
    current_y = None

    for line in sorted(receipt_lines, key=lambda l: l.bounding_box["y"]):
        # Normalize coordinates
        x = line.bounding_box["x"] / max_x
        y = line.bounding_box["y"] / max_y
        w = line.bounding_box["width"] / max_x

        # Check if this line belongs to current group
        if current_y is not None and abs(y - current_y) <= y_gap_threshold:
            current_group.append(f"L{line.line_id}: {line.text}")
        else:
            # Output previous group if exists
            if current_group:
                spatial_info = f" [y:{current_y:.2f}"
                if (
                    len(
                        set(
                            l.split(":")[1].strip()[:10] for l in current_group
                        )
                    )
                    == 1
                ):
                    spatial_info += " repeated"
                spatial_info += "]"
                formatted_lines.append(
                    f"{' | '.join(current_group)}{spatial_info}"
                )

            # Start new group
            current_group = [f"L{line.line_id}: {line.text}"]
            current_y = y

    # Add last group
    if current_group:
        spatial_info = f" [y:{current_y:.2f}]"
        formatted_lines.append(f"{' | '.join(current_group)}{spatial_info}")

    # Extract essential business context
    business_context = {
        "name": places_api_data.get("name", ""),
        "type": places_api_data.get("types", [])[:3],  # Only first 3 types
        "address": places_api_data.get("formatted_address", ""),
        "hours": places_api_data.get("opening_hours", {}).get(
            "weekday_text", []
        )[
            :1
        ],  # Only first day
    }

    return (
        "Analyze this receipt's structure by identifying natural sections "
        "based on spatial and content patterns.\n\n"
        "Business Context:\n"
        f"Name: {business_context['name']}\n"
        f"Type: {', '.join(business_context['type'])}\n"
        f"Address: {business_context['address']}\n"
        f"Hours: {', '.join(business_context['hours'])}\n\n"
        f"Receipt ({len(receipt_lines)} lines):\n"
        f"{chr(10).join(formatted_lines)}\n\n"
        "RESPONSE REQUIREMENTS:\n"
        "1. Return EXACTLY this JSON structure:\n"
        "{\n"
        '  "discovered_sections": [\n'
        "    {\n"
        '      "name": <string, one of: "business_info", '
        '"transaction_details", "items", "payment", "footer">,\n'
        '      "line_ids": <array of integers>,\n'
        '      "spatial_patterns": <array with exactly one string>,\n'
        '      "content_patterns": <array with exactly one string>,\n'
        '      "confidence": <float between 0-1>\n'
        "    }\n"
        "  ],\n"
        '  "overall_confidence": <float between 0-1>\n'
        "}\n\n"
        "2. CRITICAL RULES:\n"
        "   - ALL arrays MUST be properly defined, even if empty\n"
        "   - EVERY section MUST have exactly one spatial_pattern and one "
        "content_pattern\n"
        "   - ALL line_ids MUST be valid integers from the input\n"
        "   - ALL confidence scores MUST be between 0 and 1\n"
        "   - Section names MUST be from the predefined list\n\n"
        "3. Section Guidelines:\n"
        "   - Group lines that are spatially close and logically related\n"
        "   - Use business context to validate section content\n"
        "   - Match business name, address, and hours against receipt "
        "content\n\n"
        "4. Pattern Types:\n"
        "   Spatial Patterns (choose one):\n"
        "   - 'aligned_left': Text aligned to left margin\n"
        "   - 'aligned_right': Text aligned to right margin\n"
        "   - 'aligned_center': Text centered on receipt\n"
        "   - 'tabular': Data in columns\n"
        "   - 'indented': Text indented from margin\n\n"
        "   Content Patterns (choose one):\n"
        "   - 'business_header': Company name, logo, contact\n"
        "   - 'transaction_info': Date, time, receipt number\n"
        "   - 'itemized_list': Products/services with prices\n"
        "   - 'payment_summary': Subtotal, tax, total\n"
        "   - 'footer_info': Thank you message, policies\n\n"
        "5. Confidence Scoring:\n"
        "   - 0.9-1.0: Clear section with matching business context\n"
        "   - 0.7-0.9: Clear section but no context verification\n"
        "   - 0.5-0.7: Possible section with some uncertainty\n"
        "   - <0.5: High uncertainty, avoid using\n\n"
        "Return ONLY the JSON object. No other text."
    )


def _validate_gpt_response_structure_analysis(
    response: Response,
) -> Dict[str, Any]:
    """
    Validates the structure analysis response from the OpenAI API.

    Args:
        response (Response): The response from the OpenAI API.

    Returns:
        dict: The validated response (parsed JSON) from the OpenAI API.

    Raises:
        ValueError: If the response format is invalid.
    """
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

        if "discovered_sections" not in parsed:
            raise ValueError("Missing 'discovered_sections' key.")

        if "overall_confidence" not in parsed:
            raise ValueError("Missing 'overall_confidence' key.")

        if not isinstance(parsed["discovered_sections"], list):
            raise ValueError("'discovered_sections' must be a list.")

        if not isinstance(parsed["overall_confidence"], (int, float)):
            raise ValueError("'overall_confidence' must be a number.")

        # Validate each section
        for section in parsed["discovered_sections"]:
            if not isinstance(section, dict):
                raise ValueError("Each section must be a dictionary.")

            required_keys = [
                "name",
                "line_ids",
                "spatial_patterns",
                "content_patterns",
                "confidence",
            ]
            if not all(key in section for key in required_keys):
                raise ValueError(
                    f"Section missing required keys: {required_keys}"
                )

            if not isinstance(section["line_ids"], list):
                raise ValueError("'line_ids' must be a list.")

            if not isinstance(section["spatial_patterns"], list):
                raise ValueError("'spatial_patterns' must be a list.")

            if not isinstance(section["content_patterns"], list):
                raise ValueError("'content_patterns' must be a list.")

            if not isinstance(section["confidence"], (int, float)):
                raise ValueError("Section 'confidence' must be a number.")

        return parsed

    except JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in response: {e}\nResponse text: {response.text}"
        )
    except Exception as e:
        raise ValueError(
            f"Error validating response: {e}\nResponse text: {response.text}"
        )


def _map_labels_to_word_ids(
    labeled_words: list[dict], section_words: list[ReceiptWord]
) -> list[dict]:
    """
    Maps labeled text back to word IDs.

    Args:
        labeled_words: List of words labeled by GPT with their text and labels
        section_words: Original word objects with IDs

    Returns:
        List of word labels with IDs instead of text
    """
    result = []

    # Create a mapping of text to word objects
    # Some words might appear multiple times, so we need to track all instances
    text_to_words: Dict[str, Any] = {}
    for word in section_words:
        if word.text not in text_to_words:
            text_to_words[word.text] = []
        text_to_words[word.text].append(word)

    # Process each labeled word/phrase
    for labeled_word in labeled_words:
        text = labeled_word["text"]
        words = text.split()

        # Single word case
        if len(words) == 1:
            if text in text_to_words and text_to_words[text]:
                word = text_to_words[text].pop(
                    0
                )  # Get and remove first matching word
                result.append(
                    {
                        "line_id": word.line_id,
                        "word_id": word.word_id,
                        "label": labeled_word["label"],
                        "confidence": labeled_word["confidence"],
                    }
                )
        # Multi-word phrase case
        else:
            matched_words = []
            for word_text in words:
                if word_text in text_to_words and text_to_words[word_text]:
                    matched_words.append(text_to_words[word_text].pop(0))

            # Only process if we found all words in the phrase
            if len(matched_words) == len(words):
                # Sort by word_id to maintain order
                matched_words.sort(key=lambda w: w.word_id)

                # Add each word with a reference to the previous word
                prev_word_id = None
                for word in matched_words:
                    result.append(
                        {
                            "line_id": word.line_id,
                            "word_id": word.word_id,
                            "label": labeled_word["label"],
                            "confidence": labeled_word["confidence"],
                            "continues_from": prev_word_id,
                        }
                    )
                    prev_word_id = word.word_id

    return result


def gpt_request_field_labeling(
    receipt: Receipt,
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    section_boundaries: Dict[str, Any],
    places_api_data: Dict[str, Any],
    gpt_api_key=None,
) -> tuple[dict, str, str]:
    """
    Makes a request to the OpenAI API to label individual words in a receipt.
    Now uses a simpler format for GPT responses and maps the labels back to
    word IDs.
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
            l for l in receipt_lines if l.line_id in section["line_ids"]
        ]
        section_words = [
            w for w in receipt_words if w.line_id in section["line_ids"]
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
            "model": "gpt-3.5-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"You label words in the {section['name']} section of "
                        "receipts, using business context for accuracy."
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
                    response = requests.post(
                        url, headers=headers, json=payload, timeout=60
                    )
                    response.raise_for_status()
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
                review_reasons.extend(
                    section_result["metadata"]["review_reasons"]
                )

        except Exception as e:
            raise ValueError(
                f"Error processing section {section['name']}: {str(e)}"
            )

    # Combine results
    if not all_labels:
        raise ValueError("No labels were generated for any section.")

    final_result = {
        "labels": all_labels,
        "metadata": {
            "total_labeled_words": len(all_labels),
            "average_confidence": sum(l["confidence"] for l in all_labels)
            / len(all_labels),
            "requires_review": requires_review,
            "review_reasons": list(set(review_reasons)),  # Remove duplicates
        },
    }

    return (
        final_result,
        "\n---\n".join(queries),  # Join all queries for reference
        "\n---\n".join(responses),  # Join all responses for reference
    )


def _llm_prompt_field_labeling_section(
    receipt: Receipt,
    section_lines: list[ReceiptLine],
    section_words: list[ReceiptWord],
    section_info: Dict[str, Any],
    places_api_data: Dict[str, Any],
) -> str:
    """
    Generates a prompt for labeling words in a specific section of a receipt.
    Focuses on semantic meaning with clear examples.
    """
    # Format section content with line numbers for clarity
    formatted_lines = []
    for line in sorted(section_lines, key=lambda l: l.line_id):
        words = [w for w in section_words if w.line_id == line.line_id]
        words.sort(key=lambda w: w.word_id)
        word_texts = [word.text for word in words]
        formatted_lines.append(f"Line {line.line_id}: {' '.join(word_texts)}")

    # Extract relevant business context
    business_context = {
        "name": places_api_data.get("name", ""),
        "address": places_api_data.get("formatted_address", ""),
        "phone": places_api_data.get("formatted_phone_number", ""),
        "types": places_api_data.get("types", [])[:3],
    }

    # Get label types for this section
    label_types = _get_section_label_types(section_info["name"])

    # Create examples based on section type
    examples = []
    if "business_info" in section_info["name"].lower():
        examples = [
            (
                '{"text": "COSTCO WHOLESALE", "label": "business_name", '
                '"confidence": 0.95}'
            ),
            (
                '{"text": "5700 Lindero Canyon Rd", "label": "address_line", '
                '"confidence": 0.95}'
            ),
            '{"text": "#117", "label": "store_id", "confidence": 0.90}',
        ]
    elif "payment" in section_info["name"].lower():
        examples = [
            '{"text": "TOTAL", "label": "total", "confidence": 0.95}',
            '{"text": "$63.27", "label": "amount", "confidence": 0.95}',
            (
                '{"text": "APPROVED", "label": "payment_status", '
                '"confidence": 0.90}'
            ),
        ]
    elif "transaction" in section_info["name"].lower():
        examples = [
            '{"text": "12/17/2024", "label": "date", "confidence": 0.95}',
            '{"text": "17:19", "label": "time", "confidence": 0.95}',
            (
                '{"text": "Tran ID#: 12345", "label": "transaction_id", '
                '"confidence": 0.90}'
            ),
        ]

    return (
        f"Label words in the {section_info['name'].upper()} section of this "
        f"receipt.\n\n"
        f"Business Context:\n{dumps(business_context, indent=2)}\n\n"
        f"Receipt Content:\n"
        + "\n".join(formatted_lines)
        + "\n\nAvailable Labels:\n"
        + "\n".join(
            f"- {label}: {desc}" for label, desc in label_types.items()
        )
        + "\n\nExample Labelings:\n"
        + "\n".join(examples)
        + "\n\nINSTRUCTIONS:\n"
        "1. Label each piece of meaningful text with the most specific "
        "applicable label\n"
        "2. Combine words that form a single meaningful unit (e.g., 'Lindero "
        "Canyon Rd' as one address_line)\n"
        "3. Never use 'unknown' or labels not in the list above\n"
        "4. Set high confidence (0.9+) for clear matches, lower (0.7-0.8) "
        "if uncertain\n"
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


def _detect_line_pattern(
    words: list[ReceiptWord],
    line: ReceiptLine,
    places_api_data: Dict[str, Any],
) -> Optional[str]:
    """
    Detects common patterns in a line of text.

    Args:
        words (list[ReceiptWord]): Words in the line
        line (ReceiptLine): The line object
        places_api_data (dict): Business context

    Returns:
        str: Detected pattern or None
    """
    text = line.text.strip()

    # Address patterns
    if places_api_data.get("formatted_address"):
        addr_parts = places_api_data["formatted_address"].split(",")
        if any(part.strip() in text for part in addr_parts):
            return "address"

    # Street address pattern
    if re.match(
        r"^\d+\s+[A-Za-z\s]+(?:St|Street|Rd|Road|Ave|Avenue|Blvd|"
        r"Boulevard|Ln|Lane|Dr|Drive|Way|Ct|Court|Circle|Cir)\.?",
        text,
    ):
        return "street_address"

    # City, State ZIP pattern
    if re.match(r"^[A-Za-z\s]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$", text):
        return "city_state_zip"

    # Business name pattern
    if (
        places_api_data.get("name")
        and places_api_data["name"].upper() in text.upper()
    ):
        return "business_name"

    # Phone number pattern
    if re.search(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text):
        return "phone_number"

    # Price pattern
    if re.search(r"\$?\d+\.\d{2}\b", text):
        return "price"

    # Date pattern
    if re.search(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", text):
        return "date"

    # Time pattern
    if re.search(r"\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?", text):
        return "time"

    return None


def _extract_relevant_business_context(
    section_name: str, places_api_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Extracts relevant business context based on section type.

    Args:
        section_name (str): Name of the section
        places_api_data (dict): Full business context

    Returns:
        dict: Relevant context for this section
    """
    context: Dict[str, Any] = {}
    section_name_lower = section_name.lower()

    if "business" in section_name_lower or "header" in section_name_lower:
        context.update(
            {
                "name": places_api_data.get("name", ""),
                "address": places_api_data.get("formatted_address", ""),
                "phone": places_api_data.get("formatted_phone_number", ""),
                "types": places_api_data.get("types", [])[:3],
            }
        )

    if "payment" in section_name_lower or "total" in section_name_lower:
        context.update(
            {
                "price_level": places_api_data.get("price_level", ""),
                "currency": "USD",  # Default or get from configuration
            }
        )

    if "hours" in section_name_lower or "schedule" in section_name_lower:
        context.update(
            {
                "hours": places_api_data.get("opening_hours", {}).get(
                    "weekday_text", []
                )
            }
        )

    return context


def _get_pattern_examples(section_name: str) -> Dict[str, Any]:
    """
    Returns pattern examples for different label types.

    Args:
        section_name (str): Name of the section

    Returns:
        dict: Label types with pattern examples
    """
    base_patterns = {
        "address": ["123 Main St.", "New York, NY 10001", "Suite 100"],
        "business_name": ["STORE NAME", "Company LLC", "Brand & Co."],
        "phone": ["(123) 456-7890", "123-456-7890", "1234567890"],
        "date": ["01/01/2024", "2024-01-01", "Jan 1, 2024"],
        "time": ["13:45:00", "1:45 PM", "13:45"],
        "price": ["$12.34", "12.34", "-12.34"],
    }

    section_patterns = {
        "payment": {
            "card_number": ["XXXX-XXXX-XXXX-1234", "**** **** **** 1234"],
            "auth_code": ["Auth: 123456", "Approval: ABC123"],
            "payment_method": ["VISA", "MASTERCARD", "DEBIT", "CASH"],
        },
        "items": {
            "item_name": ["Product Name", "Service Description"],
            "quantity": ["2 @", "QTY: 2", "2 ITEMS"],
            "unit_price": ["@ $5.99", "$5.99 EA"],
        },
    }

    # Get patterns for this section
    section_name_lower = section_name.lower()
    patterns = base_patterns.copy()

    for key, values in section_patterns.items():
        if key in section_name_lower:
            patterns.update(values)

    return patterns


def _get_section_label_types(section_name: str) -> Dict[str, Any]:
    """
    Returns relevant label types for a specific section.
    Simplified and focused on clear semantic meanings.
    """
    section_name_lower = section_name.lower()

    # Define label types based on section
    if "business_info" in section_name_lower or "header" in section_name_lower:
        return {
            "business_name": "Store or company name (e.g., COSTCO WHOLESALE)",
            "address_line": (
                "Any part of the address (e.g., street, city, state, ZIP)"
            ),
            "phone": "Phone number in any format",
            "store_id": "Store number or identifier (e.g., #117)",
        }

    elif "transaction" in section_name_lower:
        return {
            "date": "Transaction date in any format",
            "time": "Transaction time in any format",
            "transaction_id": "Receipt or transaction number",
            "cashier": "Cashier name or ID",
        }

    elif "item" in section_name_lower:
        return {
            "item_name": "Name or description of purchased item",
            "quantity": "Number of items (including units)",
            "price": "Price amount (with or without currency symbol)",
            "discount": "Any discount or reduction amount",
        }

    elif "payment" in section_name_lower:
        return {
            "subtotal": "Pre-tax amount",
            "tax": "Tax amount or rate",
            "total": "Final total amount",
            "payment_method": "Form of payment (e.g., CREDIT, DEBIT, CASH)",
            "payment_status": "Payment status (e.g., APPROVED, DECLINED)",
            "card_info": "Masked card number or card-related info",
            "amount": "Any monetary amount with clear purpose",
        }

    elif "footer" in section_name_lower:
        return {
            "message": "Thank you message or general store message",
            "policy": "Store policy or return information",
            "promo": "Promotional text or special offers",
            "survey": "Survey or feedback information",
        }

    # Default types for any section
    return {
        "date_time": "Any date or time information",
        "amount": "Any monetary amount",
        "identifier": "Any ID or reference number",
        "text": "General text without specific category",
    }


def _validate_gpt_response_field_labeling(
    response: Response,
) -> Dict[str, Any]:
    """
    Validates the field labeling response from the OpenAI API.
    Expects a simpler format focusing on text labels rather than IDs.
    """
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
                raise ValueError(
                    f"Label missing required keys: {required_label_keys}"
                )

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
            raise ValueError(
                f"Metadata missing required keys: {required_metadata_keys}"
            )

        if not isinstance(metadata["requires_review"], bool):
            raise ValueError("'requires_review' must be a boolean.")
        if not isinstance(metadata["review_reasons"], list):
            raise ValueError("'review_reasons' must be a list.")

        return parsed

    except JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON in response: {e}\nResponse text: {response.text}"
        )
    except Exception as e:
        raise ValueError(
            f"Error validating response: {e}\nResponse text: {response.text}"
        )
