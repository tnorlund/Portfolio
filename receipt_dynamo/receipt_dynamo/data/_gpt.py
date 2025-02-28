# infra/lambda_layer/python/dynamo/data/_gpt.py
import re
from json import JSONDecodeError, dumps, loads
from os import environ, getenv

import requests
from requests.models import Response

from receipt_dynamo import Receipt, ReceiptLine, ReceiptWord, ReceiptWordTag


def gpt_request_tagging_validation(
    receipt: Receipt,
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    receipt_word_tags: list[ReceiptWordTag],
    gpt_api_key=None,
) -> tuple[dict, str, str]:
    """
    Makes a request to the OpenAI API to validate the tagging of a receipt.

    Returns:
        tuple[dict, str, str]: The formatted response from the OpenAI API, the query
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
    response = requests.post(url, headers=headers, json=payload)

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
        tuple[dict, str, str]: The formatted response from the OpenAI API, the query
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
    response = requests.post(url, headers=headers, json=payload)

    return (
        _validate_gpt_response_initial_tagging(response),
        query,
        response.text,
    )


def _validate_gpt_response_initial_tagging(response: Response) -> dict:
    """Validates the response from the OpenAI API.

    Validate the response from OpenAI API and raise an error if the response
    is not formatted as expected. If the response is valid, return the parsed content.

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
        content = loads(content.replace("```json", "").replace("```", ""))
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
                        "The response message content values do not contain 'l' and 'w'."
                    )
            elif isinstance(value, dict):
                if not ("l" in value and "w" in value):
                    raise ValueError(
                        "The response message content values do not contain 'l' and 'w'."
                    )
            else:
                raise ValueError(
                    "The response message content values must be a list or a dict."
                )
    return content


def _validate_gpt_response_tagging_validation(response: Response) -> dict:
    """Validates the response from the OpenAI API.

    Validate the response from OpenAI API and raise an error if the response
    is not formatted as expected. If the response is valid, return the parsed content.

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
        "\nYou are a helpful assistant that extracts structured data from a receipt.\n"
        "\nBelow is a sample of the JSON you will receive. Notice that each 'word' has a 'text', a 'centroid' [x, y], and a line/word ID (l, w):\n"
        "\n```json\n"
        "\n{"
        '  "receipt": {\n'
        '    "receipt_id": 123\n'
        "    // more receipt-level fields\n"
        "  },\n"
        '  "w": [\n'
        "    {\n"
        '      "t": "BANANAS",\n'
        '      "c": [0.1234, 0.5678],\n'
        '      "l": 0,\n'
        '      "w": 0\n'
        "    },\n"
        "    {\n"
        '      "t": "1.99",\n'
        '      "c": [0.2345, 0.6789],\n'
        '      "l": 0,\n'
        '      "w": 1\n'
        "    }\n"
        "    // ...\n"
        "  ]\n"
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
        "       * line_item: includes all words that contribute to any line item\n"
        "       * line_item_name: includes words that contribute to the item name\n"
        "       * line_item_price: includes words that contribute to the item price\n\n"
        '   Instead of returning the text or centroid, **return an array of {"l": <line_id>, "w": <word_id>} '
        "   for each field.**\n"
        "   - For example, if you think the first two words (line_id=0, word_id=0 and line_id=0, word_id=1) "
        "     make up 'store_name', return:\n"
        '     "store_name": [\n'
        '       {"l": 0, "w": 0},\n'
        '       {"l": 0, "w": 1}\n'
        "     ]\n"
        "   - If you cannot find a particular field, return an empty array for it.\n\n"
        "**Output Requirements**:\n"
        " - Output must be valid JSON.\n"
        " - Do not return additional keys or text.\n"
        ' - Do not invent new {"l", "w"} pairs. Only use those provided in the \'words\' list.\n'
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
    receipt_word_tags: list[ReceiptWordTag],
) -> str:
    """
    Generates a prompt string for validating and potentially revising receipt word tags.

    This function constructs a detailed prompt intended for a language model (such as ChatGPT)
    to review receipt data that includes receipt dimensions, lines, words, and their associated tags.
    It formats the receipt information as a JSON structure that describes:
      - The receipt metadata (e.g., width and height).
      - A list of receipt lines, where each line contains:
          - A unique line identifier and bounding box.
          - The full text of the line.
          - A list of tagged words, with each word including its identifier, text,
            bounding box, and a list of tag objects (each with a tag name and a validation flag).

    The prompt then provides instructions on how to generate a structured JSON output. The expected
    output should be a list of dictionaries, each containing:
      - "line_id": The unique identifier of the line.
      - "word_id": The unique identifier of the word.
      - "initial_tag": The tag originally provided in the data.
      - "revised_tag": The corrected tag suggested by the language model.
      - "confidence": A numeric confidence level (from 1 to 5) indicating certainty.
      - "flag": A status string ("ok" if the tag is correct, or "needs_review" if it is uncertain).

    Parameters:
        receipt (Receipt): The receipt object containing metadata such as width and height.
        receipt_lines (list[ReceiptLine]): A list of receipt line objects.
        receipt_words (list[ReceiptWord]): A list of receipt word objects.
        receipt_word_tags (list[ReceiptWordTag]): A list of tags associated with receipt words.

    Returns:
        str: A prompt string that includes:
            - A description of the expected JSON structure.
            - The receipt data formatted as a JSON string.
            - Detailed instructions for reviewing and revising word tags,
              ensuring the output is a structured JSON matching the specified schema.
    """
    receipt_dict = {
        "width": receipt.width,
        "height": receipt.height,
    }
    line_dicts = []

    for line in receipt_lines:
        words_in_line_with_tags = [
            word
            for word in receipt_words
            if word.line_id == line.line_id
            and word.receipt_id == receipt.receipt_id
            and len(word.tags) > 0
        ]

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
                            "tag": tag.tag,
                            "validated": tag.validated,
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
        "You are provided with JSON data that conforms to the following structure:\n"
        "```json\n"
        "{\n"
        '    "receipt": {\n'
        '        "width": <integer>,\n'
        '        "height": <integer>\n'
        "    },\n"
        '    "lines": [\n'
        "        {\n"
        '            "line_id": <integer>,\n'
        '            "bounding_box": {\n'
        '                "x": <integer>,\n'
        '                "y": <integer>,\n'
        '                "width": <integer>,\n'
        '                "height": <integer>\n'
        "            },\n"
        '            "tagged_words": [\n'
        "                {\n"
        '                    "word_id": <integer>,\n'
        '                    "text": <string>,\n'
        '                    "bounding_box": {\n'
        '                        "x": <integer>,\n'
        '                        "y": <integer>,\n'
        '                        "width": <integer>,\n'
        '                        "height": <integer>\n'
        "                    },\n"
        '                    "tags": [\n'
        "                        {\n"
        '                            "tag": <string>,\n'
        '                            "validated": <boolean>\n'
        "                        }\n"
        "                    ]\n"
        "                }\n"
        "            ]\n"
        "        }\n"
        "    ]\n"
        "}\n"
        "```\n"
        "\n"
        f"Here is the data (JSON):\n"
        f"{context_json}\n"
        "\n"
        "I'm expecting a structured output in JSON format with the following fields:\n"
        "```json\n"
        "[\n"
        "  {\n"
        '    "line_id": 1,\n'
        '    "word_id": 10,\n'
        '    "initial_tag": "phone_number",\n'
        '    "revised_tag": "phone_number",\n'
        '    "confidence": 5,\n'
        '    "flag": "ok"\n'
        "  },\n"
        "  {\n"
        '    "line_id": 3,\n'
        '    "word_id": 11,\n'
        '    "initial_tag": "date",\n'
        '    "revised_tag": "time",\n'
        '    "confidence": 2,\n'
        '    "flag": "needs_review"\n'
        "  }\n"
        "]\n"
        "```\n"
        "\n"
        "- The 'line_id' and 'word_id' fields are the unique identifiers for each line and word. They should match the data provided.\n"
        "- 'initial_tag' is the tag provided in the data.\n"
        "- 'revised_tag' is the corrected tag you suggest.\n"
        "- 'confidence' is your confidence level from 1 to 5.\n"
        "- 'flag' is 'ok' if the tag is correct, 'needs_review' if unsure.\n"
        "\n"
        "You must review each word in \"tagged_words\" and provide the correct tag. If the tag is correct, mark 'ok' and provide a high confidence level.\n"
        'Return all the "tagged_words" with the structure above.\n'
        "Do not create new 'line_id' or 'word_id' pairs. Only use the ones provided in the data.\n"
        "Return this JSON structure. Nothing else.\n"
    )
