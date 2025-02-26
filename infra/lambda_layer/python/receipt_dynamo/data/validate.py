import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    WordTag,
    GPTValidation
)
from receipt_dynamo.data._gpt import gpt_request_tagging_validation


def validate(table_name: str, image_id: str) -> None:
    """
    Makes a second pass at validating tagged words using GPT for all receipts in an image.
    
    This function:
    1. Retrieves all receipt-related data for the given image from DynamoDB
    2. For each receipt in the image:
        - Gets GPT to validate the existing word tags
        - Creates a GPTValidation record of the validation attempt
        - Updates the validation status of existing tags based on GPT's response
        - Updates any tags that GPT suggests should be revised
        - Updates both ReceiptWordTags and WordTags in DynamoDB
    
    Args:
        table_name (str): The DynamoDB table name where receipt data is stored
        image_id (str): UUID of the image containing receipts to validate

    Raises:
        ValueError: If table_name is not provided and the environment variable DYNAMO_DB_TABLE is not set
        Exception: If there are errors communicating with DynamoDB or GPT

    Example:
        >>> validate("ReceiptsTable", "550e8400-e29b-41d4-a716-446655440000")
    """
    if not table_name:
        # Check the environment variable
        table_name = os.getenv("DYNAMO_DB_TABLE")
        if not table_name:
            raise ValueError("The table_name parameter is required")

    # Initialize DynamoDB client
    dynamo_client = DynamoClient(table_name)

    # Get all relevant data from DynamoDB
    (
        _,
        _,
        _,
        word_tags,
        _,
        receipts,
        _,
        receipt_lines,
        receipt_words,
        receipt_word_tags,
        _,
        _,
        _,
    ) = dynamo_client.getImageDetails(image_id)

    if not receipts:
        raise ValueError(f"No receipts found for image {image_id}")

    # Process each receipt
    gpt_validations_to_add = []
    receipt_word_tags_to_update = []
    word_tags_to_update = []
    for receipt in receipts:
        # Filter data for this receipt
        r_lines = [l for l in receipt_lines if l.receipt_id == receipt.receipt_id]
        r_words = [w for w in receipt_words if w.receipt_id == receipt.receipt_id]
        r_word_tags = [t for t in receipt_word_tags if t.receipt_id == receipt.receipt_id]

        # Request GPT validation
        content, query, response_text = gpt_request_tagging_validation(
            receipt, r_lines, r_words, r_word_tags
        )

        # Create GPT validation record
        gpt_validation = GPTValidation(
            image_id=image_id,
            receipt_id=receipt.receipt_id,
            query=query,
            response=response_text,
            timestamp_added=datetime.now(timezone.utc).isoformat(),
        )
        gpt_validations_to_add.append(gpt_validation)

        for word_data in content:
            word_id = word_data["word_id"]
            line_id = word_data["line_id"]
            confidence = word_data["confidence"]
            flag = word_data["flag"]
            initial_tag = word_data["initial_tag"]
            revised_tag = word_data["revised_tag"]

            # Find and update the receipt word tag
            matching_receipt_word_tag = next(
                (
                    tag
                    for tag in r_word_tags
                    if tag.word_id == word_id 
                    and tag.line_id == line_id 
                    # and tag.tag == initial_tag
                ),
                None,
            )
            # Skip this tag when it does not exist
            if not matching_receipt_word_tag:
                continue
            matching_receipt_word_tag.validated = flag == "ok"
            matching_receipt_word_tag.timestamp_validated = datetime.now(timezone.utc).isoformat()
            matching_receipt_word_tag.gpt_confidence = confidence
            matching_receipt_word_tag.flag = flag
            matching_receipt_word_tag.revised_tag = revised_tag
            receipt_word_tags_to_update.append(matching_receipt_word_tag)

            # Find the matching word tag
            matching_word_tag = next(
                (
                    tag
                    for tag in word_tags
                    if tag.word_id == word_id 
                    and tag.line_id == line_id 
                    # and tag.tag == initial_tag
                ),
                None,
            )
            if not matching_word_tag:
                continue
            matching_word_tag.validated = flag == "ok"
            matching_word_tag.timestamp_validated = datetime.now(timezone.utc).isoformat()
            matching_word_tag.gpt_confidence = confidence
            matching_word_tag.flag = flag
            matching_word_tag.revised_tag = revised_tag
            word_tags_to_update.append(matching_word_tag)

        # Deduplicate the lists
        receipt_word_tags_to_update = list(set(receipt_word_tags_to_update))
        word_tags_to_update = list(set(word_tags_to_update))

        # Check that all word tags and receipt word tags match
        receipt_keys = [f"{item.word_id}_{item.line_id}_{item.tag}" for item in receipt_word_tags_to_update]
        word_keys = [f"{item.word_id}_{item.line_id}_{item.tag}" for item in word_tags_to_update]
        if set(receipt_keys) != set(word_keys):
            Exception("Could not match receipt word tags to word tags")

    # Batch update DynamoDB
    dynamo_client.addGPTValidations(gpt_validations_to_add)
    dynamo_client.addReceiptWordTags(receipt_word_tags_to_update)
    dynamo_client.addWordTags(word_tags_to_update)
