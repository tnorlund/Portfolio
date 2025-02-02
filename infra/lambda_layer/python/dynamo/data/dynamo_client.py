from datetime import datetime
import boto3
import os
import requests
from requests.models import Response
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
from dynamo.entities import WordTag, ReceiptWordTag, ReceiptWord, Word


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

    def gpt_receipt(self, image_id: str):
        """Uses the ChatGPT API to label words in a receipt.

        Args:
            gpt_receipt (str): The image UUID to label.
        """
        image, lines, words, word_tags, letters, receipt_details = self.getImageDetails(
            image_id
        )
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
                    f"_failure_image_{image.image_id}_receipt_{receipt.receipt_id:05d}.txt",
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

            raw_message = response.json()["choices"][0]["message"]["content"]
            raw_message = raw_message.replace("```json", "").replace("```", "")
            try:
                results = json.loads(raw_message)
            except json.JSONDecodeError:
                failure_key = image.raw_s3_key.replace(
                    ".png",
                    f"_failure_image_{image.image_id}_receipt_{receipt.receipt_id:05d}.txt",
                )
                s3 = boto3.client("s3")
                s3.put_object(
                    Bucket=image.raw_s3_bucket,
                    Key=failure_key,
                    Body=response.text,
                    ContentType="text/plain",
                )
                raise ValueError(
                    f"An error occurred while formatting the response: {response.text}"
                )

            s3_json_key = image.raw_s3_key.replace(
                ".png",
                f"_GPT_image_{image.image_id}_receipt_{receipt.receipt_id:05d}.json",
            )
            s3 = boto3.client("s3")
            s3.put_object(
                Bucket=image.raw_s3_bucket,
                Key=s3_json_key,
                Body=raw_message,
                ContentType="application/json",
            )

            words_to_update = []
            word_tags_to_add = []
            receipt_words_to_update = []
            receipt_word_tags_to_add = []
            for word_tag_in_response, details in results.items():
                if (
                    isinstance(details, dict)
                    and "value" in details
                    and "word_centroids" in details
                ):
                    # The word in the GPT response is a string
                    if isinstance(details["value"], str):
                        (
                            receipt_words_to_update,
                            receipt_word_tags_to_add,
                            words_to_update,
                            word_tags_to_add,
                        ) = self._process_gpt_response(
                            details["value"],
                            details["word_centroids"],
                            receipt_words,
                            receipt,
                            image,
                            words,
                            [word_tag_in_response],
                            receipt_words_to_update,
                            receipt_word_tags_to_add,
                            words_to_update,
                            word_tags_to_add,
                        )
                    # The word in the GPT response is a number
                    elif (
                        isinstance(details["value"], (int, float))
                        and "value" in details
                        and "word_centroids" in details
                    ):
                        (
                            receipt_words_to_update,
                            receipt_word_tags_to_add,
                            words_to_update,
                            word_tags_to_add,
                        ) = self._process_gpt_response(
                            str(details["value"]),
                            details["word_centroids"],
                            receipt_words,
                            receipt,
                            image,
                            words,
                            [word_tag_in_response],
                            receipt_words_to_update,
                            receipt_word_tags_to_add,
                            words_to_update,
                            word_tags_to_add,
                        )

                elif isinstance(details, list) and word_tag_in_response == "items":
                    # These are the line items. Iterate over each line item
                    for line_item in details:
                        if "item_name" not in line_item or "price" not in line_item:
                            s3_json_key = image.raw_s3_key.replace(
                                ".png",
                                f"_GPT_image_failure_{image.image_id}_receipt_{receipt.receipt_id:05d}.json",
                            )
                            s3 = boto3.client("s3")
                            s3.put_object(
                                Bucket=image.raw_s3_bucket,
                                Key=s3_json_key,
                                Body=raw_message,
                                ContentType="application/json",
                            )
                            ValueError("Item name or price missing")
                        item_name = line_item["item_name"]
                        price = line_item["price"]
                        if (
                            "value" not in item_name
                            or "word_centroids" not in item_name
                        ) or ("value" not in price or "word_centroids" not in price):
                            s3_json_key = image.raw_s3_key.replace(
                                ".png",
                                f"_GPT_image_failure_{image.image_id}_receipt_{receipt.receipt_id:05d}.json",
                            )
                            s3 = boto3.client("s3")
                            s3.put_object(
                                Bucket=image.raw_s3_bucket,
                                Key=s3_json_key,
                                Body=raw_message,
                                ContentType="application/json",
                            )
                            ValueError(
                                "Item name or price missing value or word_centroids"
                            )

                        # Process the item name
                        (
                            receipt_words_to_update,
                            receipt_word_tags_to_add,
                            words_to_update,
                            word_tags_to_add,
                        ) = self._process_gpt_response(
                            item_name["value"],
                            item_name["word_centroids"],
                            receipt_words,
                            receipt,
                            image,
                            words,
                            ["line_item_name", "line_item"],
                            receipt_words_to_update,
                            receipt_word_tags_to_add,
                            words_to_update,
                            word_tags_to_add,
                        )
                        # Process the price
                        (
                            receipt_words_to_update,
                            receipt_word_tags_to_add,
                            words_to_update,
                            word_tags_to_add,
                        ) = self._process_gpt_response(
                            str(price["value"]),
                            price["word_centroids"],
                            receipt_words,
                            receipt,
                            image,
                            words,
                            ["line_item_price", "line_item"],
                            receipt_words_to_update,
                            receipt_word_tags_to_add,
                            words_to_update,
                            word_tags_to_add,
                        )

            # Update the Receipt entities
            self.updateReceiptWords(receipt_words_to_update)
            self.addReceiptWordTags(receipt_word_tags_to_add)
            # Update the Image entities
            self.updateWords(words_to_update)
            self.addWordTags(word_tags_to_add)

    def _gpt_request(self, receipt, receipt_words) -> Response:
        """Makes a request to the OpenAI API to label the receipt.

        Returns:
            dict: The response from the OpenAI API.
        """
        if not os.getenv("OPENAI_API_KEY"):
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
            '     - Use the same centroids from the "words" array above. They **must** match based on the centroids given. Do not create new centroids.\n\n'
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

    def _process_gpt_response(
        self,
        value,
        word_centroids,
        receipt_words,
        receipt,
        image,
        words,
        word_tag_in_response,
        receipt_words_to_update,
        receipt_word_tags_to_add,
        words_to_update,
        word_tags_to_add,
    ) -> tuple[list[ReceiptWord], list[ReceiptWordTag], list[Word], list[WordTag]]:
        """Processes the response from the ChatGPT API."""
        for centroid_in_response in word_centroids:
            # Match the words in DynamoDB with the ones in the response
            matched_words = [
                receipt_word
                for receipt_word in receipt_words
                if receipt_word.calculate_centroid()
                == (centroid_in_response["x"], centroid_in_response["y"])
            ]
            if len(matched_words) == 0:
                # TODO Save JSON response to S3
                raise Exception("Response did not have any matching centroids")
            if len(matched_words) > 1:
                # TODO Save JSON response to S3
                raise Exception("Response had multiple matching centroids")
            matched_word = matched_words[0]
            if word_tag_in_response in matched_word.tags:
                Warning(
                    f"Word {matched_word.text} already has tag {word_tag_in_response}"
                )
                continue
            # Update Receipt entities
            matched_word.tags.extend(word_tag_in_response)
            receipt_words_to_update.append(matched_word)
            receipt_word_tags_to_add.extend(
                [
                    ReceiptWordTag(
                        receipt_id=receipt.receipt_id,
                        image_id=image.image_id,
                        line_id=matched_word.line_id,
                        word_id=matched_word.id,
                        tag=tag,
                        timestamp_added=datetime.now().isoformat(),
                    )
                    for tag in word_tag_in_response
                ]
            )

            # Update Image entities
            matched_words = [
                word
                for word in words
                if word.id == matched_word.id and word.line_id == matched_word.line_id
            ]
            if len(matched_words) == 0:
                # TODO Save JSON response to S3
                raise Exception(
                    "Failed to match Word with Receipt Word. No words with the same ID"
                )
            if len(matched_words) > 1:
                # TODO Save JSON response to S3
                print(matched_words)
                raise Exception(
                    "Failed to match Word with Receipt Word. Multiple words with the same ID"
                )
            matched_word = matched_words[0]
            if word_tag_in_response in matched_word.tags:
                Warning(
                    f"Word {matched_word.text} already has tag {word_tag_in_response}"
                )
                continue
            matched_word.tags.extend(word_tag_in_response)
            words_to_update.append(matched_word)
            word_tags_to_add.extend(
                [
                    WordTag(
                        image_id=image.image_id,
                        line_id=matched_word.line_id,
                        word_id=matched_word.id,
                        tag=tag,
                        timestamp_added=datetime.now().isoformat(),
                    )
                    for tag in word_tag_in_response
                ]
            )

        return (
            receipt_words_to_update,
            receipt_word_tags_to_add,
            words_to_update,
            word_tags_to_add,
        )
