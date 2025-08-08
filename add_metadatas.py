"""Script to identify receipts that don't have corresponding metadata."""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List

from openai import AsyncOpenAI

from receipt_dynamo.data._pulumi import load_secrets
from receipt_dynamo import DynamoClient
from receipt_dynamo.data.export_image import export_image
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
)
from receipt_label.merchant_validation.handler import MerchantValidationHandler

logger = logging.getLogger(__name__)
LIST_LIMIT = 1000
EXPORT_DIR = Path("dev.local_export")


def _fetch_google_places_api_key_from_pulumi() -> str:
    """Fetch secret from Pulumi config if env var is missing.

    Looks up `portfolio:GOOGLE_PLACES_API_KEY` in the active stack
    (defaulting to 'dev' if $PULUMI_STACK is unset) within the `infra/` dir.
    """
    stack = os.environ.get("PULUMI_STACK", "dev")
    secrets = load_secrets(
        stack, project_dir=Path(__file__).resolve().parent / "infra"
    )

    if "portfolio:GOOGLE_PLACES_API_KEY" in secrets:
        return secrets["portfolio:GOOGLE_PLACES_API_KEY"]["value"]
    logger.warning(
        "Could not read GOOGLE_PLACES_API_KEY from Pulumi (stack=%s): %s",
        stack,
        "GOOGLE_PLACES_API_KEY not found in Pulumi secrets",
    )
    return ""


GOOGLE_PLACES_API_KEY = (
    os.environ.get("GOOGLE_PLACES_API_KEY")
    or _fetch_google_places_api_key_from_pulumi()
)

# Initialize client later to avoid module-level blocking
client = None


def get_receipts_without_metadata(
    dynamo_client: DynamoClient,
) -> List[Receipt]:
    """
    Get a list of Receipt objects that don't have corresponding metadata.

    Args:
        dynamo_client: Initialized DynamoClient instance

    Returns:
        List of Receipt objects without corresponding metadata, sorted by
        (image_id, receipt_id)
    """
    # List all receipts
    receipts, last_evaluated_key = dynamo_client.list_receipts(
        limit=LIST_LIMIT, last_evaluated_key=None
    )
    while last_evaluated_key:
        next_receipts, last_evaluated_key = dynamo_client.list_receipts(
            limit=LIST_LIMIT, last_evaluated_key=last_evaluated_key
        )
        receipts.extend(next_receipts)

    # List all metadatas
    metadatas, last_evaluated_key = dynamo_client.list_receipt_metadatas(
        limit=LIST_LIMIT, last_evaluated_key=None
    )
    while last_evaluated_key:
        (
            next_metadatas,
            last_evaluated_key,
        ) = dynamo_client.list_receipt_metadatas(
            limit=LIST_LIMIT, last_evaluated_key=last_evaluated_key
        )
        metadatas.extend(next_metadatas)

    # Create sets of (image_id, receipt_id) tuples for efficient comparison
    receipt_keys = {
        (receipt.image_id, receipt.receipt_id) for receipt in receipts
    }
    metadata_keys = {
        (metadata.image_id, metadata.receipt_id) for metadata in metadatas
    }

    # Find receipts that don't have corresponding metadata
    receipts_without_metadata_keys = receipt_keys - metadata_keys

    # Filter the original receipt objects based on the missing keys
    receipts_without_metadata: List[Receipt] = [
        r
        for r in receipts
        if (r.image_id, r.receipt_id) in receipts_without_metadata_keys
    ]

    # Return in a deterministic order
    receipts_without_metadata.sort(key=lambda r: (r.image_id, r.receipt_id))
    return receipts_without_metadata


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    
    # Reduce noise from httpx but keep agent logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    # Save tracing locally instead of sending to OpenAI
    # This creates a local trace file for debugging
    TRACE_DIR = Path("dev.traces")
    TRACE_DIR.mkdir(exist_ok=True)
    os.environ["AGENT_TRACE_DIR"] = str(TRACE_DIR)
    
    print("Starting script...")
    print(f"Using DynamoDB table: {os.environ.get('DYNAMO_TABLE_NAME', 'NOT SET')}")
    
    # Initialize Ollama client
    client = AsyncOpenAI(
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )
    
    receipts_missing_metadata = get_receipts_without_metadata(
        DynamoClient(os.environ["DYNAMO_TABLE_NAME"])
    )
    logger.info(
        "Found %d receipts without metadata", len(receipts_missing_metadata)
    )

    for idx, receipt in enumerate(receipts_missing_metadata, 1):
        print(f"\n{'='*60}")
        print(f"Processing receipt {idx}/{len(receipts_missing_metadata)}")
        print(f"{'='*60}")
        
        if f"{receipt.image_id}.json" not in os.listdir(EXPORT_DIR):
            logger.info("📥 Exporting image %s", receipt.image_id)
            export_image(
                os.environ["DYNAMO_TABLE_NAME"],
                receipt.image_id,
                EXPORT_DIR,
            )
        # Read the exported image
        with open(
            EXPORT_DIR / f"{receipt.image_id}.json", "r", encoding="utf-8"
        ) as f:
            image_dict = json.load(f)

        # Find the specific receipt in the image data
        receipt_data = None
        for r in image_dict["receipts"]:
            if r["receipt_id"] == receipt.receipt_id:
                receipt_data = r
                break
        
        if not receipt_data:
            logger.error(f"Receipt {receipt.receipt_id} not found in exported data for image {receipt.image_id}")
            continue
        
        receipt = Receipt(**receipt_data)
        
        # Filter lines, words, letters, and labels for THIS specific receipt
        receipt_lines = [
            ReceiptLine(**line) for line in image_dict["receipt_lines"]
            if line["receipt_id"] == receipt.receipt_id
        ]
        receipt_words = [
            ReceiptWord(**word) for word in image_dict["receipt_words"]
            if word["receipt_id"] == receipt.receipt_id
        ]
        receipt_letters = [
            ReceiptLetter(**letter) for letter in image_dict["receipt_letters"]
            if letter["receipt_id"] == receipt.receipt_id
        ]
        receipt_word_labels = [
            ReceiptWordLabel(**label)
            for label in image_dict["receipt_word_labels"]
            if label["receipt_id"] == receipt.receipt_id
        ]

        # Validate merchant using local LLM
        logger.info(
            "🔍 Validating merchant for %s#%s",
            receipt.image_id,
            receipt.receipt_id,
        )
        
        # Log what we're extracting
        from receipt_label.merchant_validation.field_extraction import extract_candidate_merchant_fields
        
        # Check if words have labels
        words_with_labels = [w for w in receipt_words if hasattr(w, 'labels') and w.labels]
        logger.info(f"📊 Receipt has {len(receipt_words)} words, {len(words_with_labels)} have labels")
        
        # Sample some labels if they exist
        if words_with_labels:
            sample_labels = []
            for w in words_with_labels[:5]:
                sample_labels.append(f"{w.text}: {w.labels}")
            logger.info(f"  Sample labels: {sample_labels}")
        
        extracted_fields = extract_candidate_merchant_fields(receipt_words)
        logger.info("📝 Extracted fields from receipt:")
        logger.info(f"  Name: {extracted_fields.name or 'None'}")
        logger.info(f"  Address: {extracted_fields.address or 'None'}")
        logger.info(f"  Phone: {extracted_fields.phone_number or 'None'}")
        logger.info(f"  Emails: {extracted_fields.emails or 'None'}")
        logger.info(f"  URLs: {extracted_fields.urls or 'None'}")

        # Create handler with your Ollama client
        handler = MerchantValidationHandler(
            google_places_api_key=GOOGLE_PLACES_API_KEY,
            model="gpt-oss:20b",
            openai_client=client,  # Pass your AsyncOpenAI client
        )

        # Use the standard validate_receipt_merchant method
        # Note: This runs synchronously even though the client is async
        metadata, status = handler.validate_receipt_merchant(
            image_id=receipt.image_id,
            receipt_id=receipt.receipt_id,
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
        )

        # Display results
        print(f"\n📊 Results:")
        print(f"  Status: {status.get('status', 'unknown')}")
        print(f"  Merchant: {metadata.merchant_name or 'Not found'}")
        if metadata.address:
            print(f"  Address: {metadata.address}")
        if metadata.phone_number:
            print(f"  Phone: {metadata.phone_number}")
        if metadata.place_id:
            print(f"  Place ID: {metadata.place_id}")
        print(f"  Validation Method: {metadata.validated_by if metadata.validated_by else 'N/A'}")
        
        # Save a detailed trace for this receipt with timestamp to prevent overwriting
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trace_file = TRACE_DIR / f"receipt_{receipt.image_id}_{receipt.receipt_id}_{timestamp}.json"
        trace_summary = {
            "receipt": {
                "image_id": receipt.image_id,
                "receipt_id": receipt.receipt_id,
                "lines": len(receipt_lines),
                "words": len(receipt_words),
                "first_lines": [line.text for line in receipt_lines[:5]]
            },
            "validation_result": {
                "status": status,
                "merchant_name": metadata.merchant_name,
                "address": metadata.address,
                "phone_number": metadata.phone_number,
                "place_id": metadata.place_id,
                "validated_by": metadata.validated_by if metadata.validated_by else None,
                "reasoning": metadata.reasoning
            },
            "timestamp": datetime.now().isoformat()
        }
        
        with open(trace_file, "w") as f:
            json.dump(trace_summary, f, indent=2)
        
        try:
            relative_path = trace_file.relative_to(Path.cwd())
        except ValueError:
            # If not relative to cwd, just use the name
            relative_path = trace_file.name
        print(f"\n💾 Trace saved to: {relative_path}")

        break

        # Optional: Save metadata to DynamoDB
        # dynamo_client = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])
        # dynamo_client.add_receipt_metadata(metadata)
