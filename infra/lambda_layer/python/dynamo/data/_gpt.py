from requests.models import Response
import requests
from json import dumps, loads, JSONDecodeError
from os import getenv, environ

from dynamo.entities.receipt import Receipt
from dynamo.entities.receipt_word import ReceiptWord


def gpt_request(
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
    query = _llm_prompt(receipt, receipt_words)
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You extract structured data from text."},
            {"role": "user", "content": query},
        ],
    }
    response = requests.post(url, headers=headers, json=payload)

    return (
        _validate_gpt_response(requests.post(url, headers=headers, json=payload)),
        query,
        response.text,
    )


def _validate_gpt_response(response: Response) -> dict:
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
        raise ValueError(f"The response message content is not valid JSON.\n{response.text}")
    # Ensure all keys in the parsed content are strings.
    if not all(isinstance(key, str) for key in content.keys()):
        raise ValueError("The response message content keys are not strings.")
    # For each field, if its value is non-empty, check that it contains both "l" and "w".
    for key, value in content.items():
        if value:  # Only check non-empty values.
            if isinstance(value, list):
                if not all(
                    isinstance(tag, dict) and "l" in tag and "w" in tag for tag in value
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


def _llm_prompt(receipt, receipt_words) -> str:
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
