"""Script to validate receipts multiple times and pick the best result."""

import json
import logging
import os
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_secrets
from receipt_dynamo.data.export_image import export_image
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordLabel,
    ReceiptMetadata,
)
from receipt_label.merchant_validation.handler import MerchantValidationHandler
from receipt_label.merchant_validation.field_extraction import (
    extract_candidate_merchant_fields,
)

logger = logging.getLogger(__name__)
LIST_LIMIT = 1000
EXPORT_DIR = Path("dev.local_export")
VALIDATION_RUNS = 3  # Number of times to validate each receipt


def _fetch_google_places_api_key_from_pulumi() -> str:
    """Fetch secret from Pulumi config if env var is missing."""
    stack = os.environ.get("PULUMI_STACK", "dev")
    secrets = load_secrets(
        stack, project_dir=Path(__file__).resolve().parent / "infra"
    )
    logger.info("Secrets: %s", secrets)

    if "portfolio:GOOGLE_PLACES_API_KEY" in secrets:
        return secrets["portfolio:GOOGLE_PLACES_API_KEY"]["value"]
    logger.warning(
        "Could not read GOOGLE_PLACES_API_KEY from Pulumi (stack=%s): %s",
        stack,
        "GOOGLE_PLACES_API_KEY not found in Pulumi secrets",
    )
    return ""


def get_receipts_without_metadata(
    dynamo_client: DynamoClient,
) -> List[Receipt]:
    """Get a list of Receipt objects that don't have corresponding metadata."""
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


def pick_best_metadata(
    validation_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Pick the best metadata result from multiple validation runs.

    Criteria:
    1. Prefer results with place_id over those without
    2. Prefer results with more matched fields
    3. Use majority voting for merchant_name
    4. Prefer non-INFERENCE validation methods
    """
    if not validation_results:
        return None

    if len(validation_results) == 1:
        return validation_results[0]

    # Score each result
    scored_results = []
    for result in validation_results:
        metadata = result["metadata"]
        status = result["status"]

        score = 0

        # Has place_id (+10 points)
        if metadata.place_id:
            score += 10

        # Has address (+5 points)
        if metadata.address:
            score += 5

        # Has phone (+5 points)
        if metadata.phone_number:
            score += 5

        # Number of matched fields (+1 per field)
        if metadata.matched_fields:
            score += len(metadata.matched_fields)

        # Not INFERENCE method (+3 points)
        if metadata.validated_by and metadata.validated_by != "INFERENCE":
            score += 3

        # Status is processed (+5 points)
        if status.get("status") == "processed":
            score += 5

        scored_results.append((score, result))

    # Sort by score (highest first)
    scored_results.sort(key=lambda x: x[0], reverse=True)

    # Get the best result
    best_result = scored_results[0][1]

    # Log the decision
    logger.info("📊 Validation comparison:")
    for score, result in scored_results:
        m = result["metadata"]
        logger.info(
            f"  Score {score}: {m.merchant_name or 'Unknown'} "
            f"(place_id: {'✓' if m.place_id else '✗'}, "
            f"method: {m.validated_by or 'N/A'})"
        )

    # Use majority voting for merchant name only if scores are close
    # Don't override a high-scoring result with place_id
    merchant_names = [
        r["metadata"].merchant_name
        for r in validation_results
        if r["metadata"].merchant_name
    ]
    if merchant_names and len(set(merchant_names)) > 1:
        name_counts = Counter(merchant_names)
        most_common = name_counts.most_common(1)[0][0]
        best_score = scored_results[0][0]

        # Only apply majority vote if scores are within 5 points of each other
        # and the best result doesn't have significantly more data
        close_scores = all(
            abs(score - best_score) <= 5 for score, _ in scored_results[:3]
        )

        if (
            close_scores
            and best_result["metadata"].merchant_name != most_common
        ):
            logger.info(f"  Majority vote would suggest: {most_common}")
            # Only override if best result doesn't have place_id
            if not best_result["metadata"].place_id:
                logger.info(f"  Applying majority vote to name: {most_common}")
                best_result["metadata"].merchant_name = most_common

    return best_result


def save_validation_results(
    export_path: Path,
    validation_results: List[Dict[str, Any]],
    best_result: Dict[str, Any],
):
    """Save validation results back to the JSON file."""
    with open(export_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Add all validation results
    data["validation_runs"] = []
    for i, result in enumerate(validation_results):
        # Convert ReceiptMetadata to dict using asdict
        metadata_dict = asdict(result["metadata"])
        # Convert timestamp to ISO format string for JSON serialization
        if "timestamp" in metadata_dict and metadata_dict["timestamp"]:
            if hasattr(metadata_dict["timestamp"], "isoformat"):
                metadata_dict["timestamp"] = metadata_dict[
                    "timestamp"
                ].isoformat()
            else:
                metadata_dict["timestamp"] = str(metadata_dict["timestamp"])

        data["validation_runs"].append(
            {
                "run": i + 1,
                "metadata": metadata_dict,
                "status": result["status"],
                "timestamp": result["timestamp"],
            }
        )

    # Add the best result
    if best_result:
        selected_dict = asdict(best_result["metadata"])
        # Convert timestamp to ISO format string
        if "timestamp" in selected_dict and selected_dict["timestamp"]:
            if hasattr(selected_dict["timestamp"], "isoformat"):
                selected_dict["timestamp"] = selected_dict[
                    "timestamp"
                ].isoformat()
            else:
                selected_dict["timestamp"] = str(selected_dict["timestamp"])
        selected_dict["selection_reason"] = (
            "Best score from multiple validation runs"
        )
        data["selected_metadata"] = selected_dict

        # Also add to receipt_metadatas array for compatibility
        if "receipt_metadatas" not in data:
            data["receipt_metadatas"] = []

        # Remove selection_reason for the receipt_metadatas version
        receipt_metadata = {
            k: v for k, v in selected_dict.items() if k != "selection_reason"
        }

        # Replace or append
        existing_index = None
        for idx, existing in enumerate(data.get("receipt_metadatas", [])):
            if (
                existing.get("image_id") == receipt_metadata["image_id"]
                and existing.get("receipt_id")
                == receipt_metadata["receipt_id"]
            ):
                existing_index = idx
                break

        if existing_index is not None:
            data["receipt_metadatas"][existing_index] = receipt_metadata
        else:
            data["receipt_metadatas"].append(receipt_metadata)

    # Save back to file
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    logger.info(f"💾 Saved validation results to {export_path}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Reduce noise from httpx
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Save tracing locally instead of sending to OpenAI
    TRACE_DIR = Path("dev.traces")
    TRACE_DIR.mkdir(exist_ok=True)
    os.environ["AGENT_TRACE_DIR"] = str(TRACE_DIR)

    print("Starting multi-validation script...")
    print(f"Will run {VALIDATION_RUNS} validations per receipt")

    # Get Google Places API key
    GOOGLE_PLACES_API_KEY = (
        os.environ.get("GOOGLE_PLACES_API_KEY")
        or _fetch_google_places_api_key_from_pulumi()
    )

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
        print(f"Receipt: {receipt.image_id}#{receipt.receipt_id}")
        print(f"{'='*60}")

        # Export if needed
        export_path = EXPORT_DIR / f"{receipt.image_id}.json"
        if not export_path.exists():
            logger.info("📥 Exporting image %s", receipt.image_id)
            export_image(
                os.environ["DYNAMO_TABLE_NAME"],
                receipt.image_id,
                EXPORT_DIR,
            )

        # Read the exported image
        with open(export_path, "r", encoding="utf-8") as f:
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
        
        # Filter lines and words for THIS specific receipt
        receipt_lines = [
            ReceiptLine(**line) for line in image_dict["receipt_lines"]
            if line["receipt_id"] == receipt.receipt_id
        ]
        receipt_words = [
            ReceiptWord(**word) for word in image_dict["receipt_words"]
            if word["receipt_id"] == receipt.receipt_id
        ]

        # Log extraction info once
        extracted_fields = extract_candidate_merchant_fields(receipt_words)
        logger.info("📝 Extracted fields from receipt:")
        logger.info(f"  Name: {extracted_fields.name or 'None'}")
        logger.info(f"  Address: {extracted_fields.address or 'None'}")
        logger.info(f"  Phone: {extracted_fields.phone_number or 'None'}")

        # Run multiple validations
        validation_results = []
        for run in range(1, VALIDATION_RUNS + 1):
            print(f"\n🔄 Validation run {run}/{VALIDATION_RUNS}")

            # Add a small delay between runs to avoid overwhelming Ollama
            if run > 1:
                print("  ⏱️  Waiting 0.5 seconds before next run...")
                time.sleep(0.5)

            # Create handler with Ollama client
            # Change model here: llama3:8b, llama3.1:latest, mixtral, etc.
            handler = MerchantValidationHandler(
                google_places_api_key=GOOGLE_PLACES_API_KEY,
                model="gpt-oss:20b",
                openai_client=client,
            )

            # Validate
            metadata, status = handler.validate_receipt_merchant(
                image_id=receipt.image_id,
                receipt_id=receipt.receipt_id,
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
            )

            validation_results.append(
                {
                    "metadata": metadata,
                    "status": status,
                    "timestamp": datetime.now().isoformat(),
                    "run_number": run,
                }
            )

            # Display this run's results
            print(f"  Result: {metadata.merchant_name or 'Not found'}")
            if metadata.place_id:
                print(f"  Place ID: {metadata.place_id}")
            print(f"  Method: {metadata.validated_by or 'N/A'}")

        # Pick the best result
        best_result = pick_best_metadata(validation_results)

        print(f"\n✅ Selected best result:")
        print(f"  Merchant: {best_result['metadata'].merchant_name}")
        if best_result["metadata"].address:
            print(f"  Address: {best_result['metadata'].address}")
        if best_result["metadata"].place_id:
            print(f"  Place ID: {best_result['metadata'].place_id}")
        print(f"  Method: {best_result['metadata'].validated_by}")

        # Save to JSON
        save_validation_results(export_path, validation_results, best_result)

        # Continue to next receipt automatically
        print(f"✅ Completed {idx}/{len(receipts_missing_metadata)}\n")
