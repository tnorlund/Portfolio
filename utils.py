from __future__ import annotations

import json
import hashlib
from typing import Tuple
from pathlib import Path
from dynamo import DynamoClient, Line, Word, Letter
from pulumi.automation import select_stack
from dynamo import Receipt, ReceiptWord


def load_env(env: str = "dev") -> tuple[str, str, str]:
    """
    Uses Pulumi to get the values of the RAW_IMAGE_BUCKET, LAMBDA_FUNCTION, and DYNAMO_DB_TABLE
    from the specified stack.

    Args:
        env: The name of the Pulumi stack/environment (e.g. 'dev', 'prod').

    Returns:
        A tuple of (raw_bucket_name, lambda_function_name, dynamo_db_table_name).
    """
    # The working directory is in the "infra" directory next to this script.
    script_dir = Path(__file__).parent.resolve()
    work_dir = script_dir / "infra"

    if not env:
        raise ValueError("The ENV environment variable is not set")

    stack_name = env.lower()
    project_name = "development"  # Adjust if your Pulumi project name differs

    stack = select_stack(
        stack_name=stack_name,
        project_name=project_name,
        work_dir=str(work_dir),
    )
    outputs = stack.outputs()
    raw_bucket = str(outputs["image_bucket_name"].value)
    lambda_function = str(outputs["cluster_lambda_function_name"].value)
    dynamo_db_table = str(outputs["table_name"].value)

    return raw_bucket, lambda_function, dynamo_db_table


def get_max_index_in_images(client: DynamoClient) -> int:
    """
    Get the maximum index in the list of images.
    """
    images, _ = client.listImageDetails()
    if not images:
        return 0
    image_indexes = [index for index in images.keys()]
    image_indexes.sort()
    # Find where the indexes are not consecutive
    for i, index in enumerate(image_indexes):
        if i + 1 != index:
            return i + 1
    return len(image_indexes) + 1


def process_ocr_dict(ocr_data: dict, image_id: int) -> Tuple[list, list, list]:
    """
    Process the OCR data and return lists of lines, words, and letters.
    """
    lines = []
    words = []
    letters = []
    for line_id, line_data in enumerate(ocr_data["lines"]):
        line_id = line_id + 1
        line_obj = Line(
            image_id=image_id,
            id=line_id,
            text=line_data["text"],
            bounding_box=line_data["bounding_box"],
            top_right=line_data["top_right"],
            top_left=line_data["top_left"],
            bottom_right=line_data["bottom_right"],
            bottom_left=line_data["bottom_left"],
            angle_degrees=line_data["angle_degrees"],
            angle_radians=line_data["angle_radians"],
            confidence=line_data["confidence"],
        )
        lines.append(line_obj)
        for word_id, word_data in enumerate(line_data["words"]):
            word_id = word_id + 1
            word_obj = Word(
                image_id=image_id,
                line_id=line_id,
                id=word_id,
                text=word_data["text"],
                bounding_box=word_data["bounding_box"],
                top_right=word_data["top_right"],
                top_left=word_data["top_left"],
                bottom_right=word_data["bottom_right"],
                bottom_left=word_data["bottom_left"],
                angle_degrees=word_data["angle_degrees"],
                angle_radians=word_data["angle_radians"],
                confidence=word_data["confidence"],
            )
            words.append(word_obj)
            for letter_id, letter_data in enumerate(word_data["letters"]):
                letter_id = letter_id + 1
                letter_obj = Letter(
                    image_id=image_id,
                    line_id=line_id,
                    word_id=word_id,
                    id=letter_id,
                    text=letter_data["text"],
                    bounding_box=letter_data["bounding_box"],
                    top_right=letter_data["top_right"],
                    top_left=letter_data["top_left"],
                    bottom_right=letter_data["bottom_right"],
                    bottom_left=letter_data["bottom_left"],
                    angle_degrees=letter_data["angle_degrees"],
                    angle_radians=letter_data["angle_radians"],
                    confidence=letter_data["confidence"],
                )
                letters.append(letter_obj)
    return lines, words, letters


def calculate_sha256(file_path):
    """
    Calculate the SHA-256 hash of a file.

    Example
    -------
    png_file_path = "example.png"  # Replace with your PNG file path
    hash_value = calculate_sha256(png_file_path)
    print(f"SHA-256: {hash_value}")
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def llm_prompt(receipt: Receipt, words: list[ReceiptWord]) -> str:
    ocr_text = json.dumps(
    {
        "receipt": dict(receipt),
        "words": [
            {
                "text": word.text,
                "centroid": {
                    "x": word.calculate_centroid()[0],
                    "y": word.calculate_centroid()[1],
                },
            }
            for word in words
        ],
    }
)
    return f"""
You are a helpful assistant that extracts structured data from a receipt.
The receipt's OCR text is:

{ocr_text}

**Your task**: Identify the following fields and output them as valid JSON:
    - store_name (string)
    - date (string)
    - time (string)
    - phone_number (string)
    - total_amount (number)
    - items (array of objects with fields: "item_name" (string) and "price" (number))
    - taxes (number)
    - any other relevant details

Additionally, for **every field** you return, **please include**:
1) The field's **value** (e.g. "SPROUTS FARMERS MARKET").
2) An array of "word_centroids" that correspond to the OCR words. 
     - This array should list the (x, y) coordinates of each word that you used to form that field's value.
     - Use the same centroids from the "words" array above.

If a particular field is not found, return an empty string or null for that field.

**The JSON structure** should look like this (conceptually):
```json
{{
"store_name": {{
    "value": "...",
    "word_centroids": [
      {{"x": ..., "y": ...}},
      ...
    ]
  }},
...
"items": [
        {{
            "item_name": {{
                "value": "...",
                "word_centroids": [...]
            }},
            "price": {{
                "value": 0.0,
                "word_centroids": [...]
            }}
        }}
    ],
}}
```
IMPORTANT: Make sure your output is valid JSON, with double quotes around keys and strings.
"""