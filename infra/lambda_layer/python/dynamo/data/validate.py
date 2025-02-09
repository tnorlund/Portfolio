from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from dynamo.data.dynamo_client import DynamoClient
from dynamo.entities import (
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    WordTag,
    GPTValidation
)
from dynamo.data._gpt import gpt_request_tagging_validation


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
        ValueError: If table_name is empty or if no receipts are found for the image_id
        Exception: If there are errors communicating with DynamoDB or GPT

    Example:
        >>> validate("ReceiptsTable", "550e8400-e29b-41d4-a716-446655440000")
    """
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
        receipt_lines,
        receipt_words,
        receipt_word_tags,
        _,
        _,
    ) = dynamo_client.getImageDetails(image_id)

    if not receipts:
        raise ValueError(f"No receipts found for image {image_id}")

    # Process each receipt
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

        # Update the validation status of tags
        receipt_tags_to_update = []
        word_tags_to_update = []
        
        for word_data in content:
            word_id = word_data["word_id"]
            line_id = word_data["line_id"]
            confidence = word_data["confidence"]
            flag = word_data["flag"]
            initial_tag = word_data["initial_tag"]
            revised_tag = word_data["revised_tag"]

            # Find and update the receipt word tag
            matching_receipt_tag = next(
                (
                    tag
                    for tag in r_word_tags
                    if tag.word_id == word_id 
                    and tag.line_id == line_id 
                    and tag.tag == initial_tag
                ),
                None,
            )

            if matching_receipt_tag:
                # Update the tag properties
                matching_receipt_tag.validated = flag == "ok"
                matching_receipt_tag.gpt_confidence = confidence
                matching_receipt_tag.flag = flag
                matching_receipt_tag.timestamp_validated = datetime.now(timezone.utc).isoformat()
                if initial_tag != revised_tag:
                    matching_receipt_tag.tag = revised_tag
                receipt_tags_to_update.append(matching_receipt_tag)

                # Also update the corresponding WordTag
                matching_word_tag = next(
                    (
                        tag
                        for tag in word_tags
                        if tag.word_id == word_id 
                        and tag.line_id == line_id 
                        and tag.tag == initial_tag
                    ),
                    None,
                )
                
                if matching_word_tag:
                    if initial_tag != revised_tag:
                        matching_word_tag.tag = revised_tag
                    matching_word_tag.timestamp_validated = datetime.now(timezone.utc).isoformat()
                    word_tags_to_update.append(matching_word_tag)

        # Batch update DynamoDB
        dynamo_client.addGPTValidation(gpt_validation)
        if receipt_tags_to_update:
            dynamo_client.addReceiptWordTags(receipt_tags_to_update)
        if word_tags_to_update:
            dynamo_client.addWordTags(word_tags_to_update)
