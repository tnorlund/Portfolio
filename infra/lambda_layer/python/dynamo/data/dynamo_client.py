import boto3
import os
import requests
import json
from dynamo.data._image import _Image
from dynamo.data._line import _Line
from dynamo.data._word import _Word
from dynamo.data._letter import _Letter
from dynamo.data._receipt import _Receipt
from dynamo.data._receipt_line import _ReceiptLine
from dynamo.data._receipt_word import _ReceiptWord
from dynamo.data._receipt_letter import _ReceiptLetter
from dynamo.data._word_tag import _WordTag
from dynamo.data._receipt_word_tag import _ReceiptWordTag


class DynamoClient(
    _Image,
    _Line,
    _Word,
    _Letter,
    _Receipt,
    _ReceiptLine,
    _ReceiptWord,
    _ReceiptLetter,
    _WordTag,
    _ReceiptWordTag,
):
    """A class used to represent a DynamoDB client."""

    def __init__(self, table_name: str, region: str = "us-east-1"):
        """Initializes a DynamoClient instance.

        Args:
            table_name (str): The name of the DynamoDB table.
            region (str, optional): The AWS region where the DynamoDB table is located. Defaults to "us-east-1".

        Attributes:
            _client (boto3.client): The Boto3 DynamoDB client.
            table_name (str): The name of the DynamoDB table.
        """

        self._client = boto3.client("dynamodb", region_name=region)
        self.table_name = table_name
        # Ensure the table already exists
        try:
            self._client.describe_table(TableName=self.table_name)
        except self._client.exceptions.ResourceNotFoundException:
            raise ValueError(
                f"The table '{self.table_name}' does not exist in region '{region}'."
            )

    def gpt_receipt(self, image_id: int):
        """Uses the ChatGPT API to label words in a receipt.

        Args:
            gpt_receipt (int): The image ID to label.
        """
        image, lines, words, letters, receipt_details = self.getImageDetails(image_id=1)
        for receipt_detail in receipt_details:
            receipt = receipt_detail["receipt"]
            receipt_lines = receipt_detail["lines"]
            receipt_words = receipt_detail["words"]
            receipt_letters = receipt_detail["letters"]

            response = self._gpt_request(receipt, receipt_words)

            # Check if the response is valid
            if response.status_code != 200:
                # Store the response in S3 next to the image's raw file
                # Example:
                # raw/a68c3576-6df0-4d1b-9aa8-f861664d74f4.png -> raw/a68c3576-6df0-4d1b-9aa8-f861664d74f4_failure_image_00001_receipt_00001.txt
                failure_key = image.raw_s3_key.replace(
                    ".png",
                    f"_failure_image_{image.id:05d}_receipt_{receipt.id:05d}.txt",
                )
                s3 = boto3.client("s3")
                s3.put_object(
                    Bucket=image.raw_s3_bucket,
                    Key=failure_key,
                    Body=response.text,
                    ContentType="text/plain",
                )
                raise ValueError(
                    f"An error occurred while making the request: {response.text}"
                )

            # Parse the response
            response_json = response.json()

    def _gpt_request(self, receipt, receipt_words) -> dict:
        """Makes a request to the OpenAI API to label the receipt.

        Returns:
            dict: The response from the OpenAI API.
        """
        if os.getenv("OPENAI_API_KEY") is None:
            raise ValueError("The OPENAI_API_KEY environment variable is not set.")
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
        }
        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": "You extract structured data from text."},
                {"role": "user", "content": self._llm_prompt(receipt, receipt_words)},
            ],
        }
        return requests.post(url, headers=headers, json=payload)

    def _llm_prompt(self, receipt, receipt_words) -> str:
        """Generates a prompt for the ChatGPT API based on the receipt.

        Returns:
            str: The prompt for the ChatGPT API.
        """
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
                    for word in receipt_words
                ],
            }
        )
        return (
            "\nYou are a helpful assistant that extracts structured data from a receipt.\n"
            "The receipt's OCR text is:\n\n"
            f"{ocr_text}\n\n"
            "**Your task**: Identify the following fields and output them as valid JSON:\n"
            "    - store_name (string)\n"
            "    - date (string)\n"
            "    - time (string)\n"
            "    - phone_number (string)\n"
            "    - total_amount (number)\n"
            '    - items (array of objects with fields: "item_name" (string) and "price" (number))\n'
            "    - taxes (number)\n"
            "    - address (string)\n\n"
            "Additionally, for **every field** you return, **please include**:\n"
            '1) The field\'s **value** (e.g. "SPROUTS FARMERS MARKET").\n'
            '2) An array of "word_centroids" that correspond to the OCR words. \n'
            "     - This array should list the (x, y) coordinates of each word that you used to form that field's value.\n"
            '     - Use the same centroids from the "words" array above.\n\n'
            "If a particular field is not found, return an empty string or null for that field.\n\n"
            "**The JSON structure** should look like this (conceptually):\n"
            "```json\n"
            "{\n"
            '"store_name": {\n'
            '    "value": "...",\n'
            '    "word_centroids": [\n'
            '      {"x": ..., "y": ...},\n'
            "      ...\n"
            "    ]\n"
            "  },\n"
            "...\n"
            '"items": [\n'
            "        {\n"
            '            "item_name": {\n'
            '                "value": "...",\n'
            '                "word_centroids": [...]\n'
            "            },\n"
            '            "price": {\n'
            '                "value": 0.0,\n'
            '                "word_centroids": [...]\n'
            "            }\n"
            "        }\n"
            "    ],\n"
            "}\n"
            "```\n"
            "IMPORTANT: Make sure your output is valid JSON, with double quotes around keys and strings.\n"
        )
