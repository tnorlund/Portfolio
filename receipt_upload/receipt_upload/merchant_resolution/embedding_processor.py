"""
Merchant-resolving embedding processor for unified upload container.

This processor:
1. Uses receipt_chroma.create_embeddings_and_compaction_run() to generate embeddings
   and get snapshot+delta pre-merged clients
2. Resolves merchant information using the MerchantResolver (two-tier strategy)
3. Enriches the receipt in DynamoDB with merchant data
4. Returns immediately while compaction happens asynchronously
"""

import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from receipt_chroma import create_embeddings_and_compaction_run
from receipt_upload.merchant_resolution.resolver import (
    MerchantResolver,
    MerchantResult,
)

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import ReceiptLine, ReceiptWord, ReceiptWordLabel

logger = logging.getLogger(__name__)


def _log(msg: str) -> None:
    """Log message with immediate flush for CloudWatch visibility."""
    print(f"[MERCHANT_EMBEDDING_PROCESSOR] {msg}", flush=True)
    logger.info(msg)


class MerchantResolvingEmbeddingProcessor:
    """
    Generate embeddings and resolve merchant information for a receipt.

    This processor:
    1. Generates embeddings using receipt_chroma orchestration
    2. Queries the merged snapshot+delta clients for merchant resolution
    3. Updates DynamoDB with merchant information
    4. Triggers async compaction via SQS
    """

    def __init__(
        self,
        table_name: str,
        chromadb_bucket: str,
        chroma_http_endpoint: Optional[str] = None,
        google_places_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        lines_queue_url: Optional[str] = None,
        words_queue_url: Optional[str] = None,
    ):
        """
        Initialize the processor.

        Args:
            table_name: DynamoDB table name
            chromadb_bucket: S3 bucket for ChromaDB snapshots/deltas
            chroma_http_endpoint: Optional HTTP endpoint (unused, kept for compat)
            google_places_api_key: Google Places API key for Tier 2 resolution
            openai_api_key: OpenAI API key for embeddings
            lines_queue_url: SQS queue URL for lines compaction (set via env)
            words_queue_url: SQS queue URL for words compaction (set via env)
        """
        self.dynamo = DynamoClient(table_name)
        self.chromadb_bucket = chromadb_bucket
        self.openai_api_key = openai_api_key
        self.google_places_api_key = google_places_api_key

        # Set queue URLs in environment for receipt_chroma to use
        if lines_queue_url:
            os.environ["CHROMADB_LINES_QUEUE_URL"] = lines_queue_url
        if words_queue_url:
            os.environ["CHROMADB_WORDS_QUEUE_URL"] = words_queue_url

        # Initialize Places client if API key provided
        self.places_client = None
        if google_places_api_key:
            try:
                from receipt_places import PlacesClient

                self.places_client = PlacesClient(api_key=google_places_api_key)
            except ImportError:
                _log("WARNING: receipt_places not available")

        # Initialize merchant resolver
        self.merchant_resolver = MerchantResolver(
            dynamo_client=self.dynamo,
            places_client=self.places_client,
        )

        # S3 client for snapshot downloads
        self.s3_client = boto3.client("s3")

    def process_embeddings(
        self,
        image_id: str,
        receipt_id: int,
        lines: Optional[List[ReceiptLine]] = None,
        words: Optional[List[ReceiptWord]] = None,
    ) -> Dict[str, Any]:
        """
        Generate embeddings, resolve merchant, and enrich receipt.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id
            lines: Optional list of ReceiptLine entities (fetched if not provided)
            words: Optional list of ReceiptWord entities (fetched if not provided)

        Returns:
            Dict with success status, merchant info, and compaction run details
        """
        # Fetch lines/words if not provided
        if lines is None or words is None:
            lines = self.dynamo.list_receipt_lines_from_receipt(image_id, receipt_id)
            words = self.dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
            _log(f"Fetched {len(lines)} lines and {len(words)} words from DynamoDB")
        else:
            _log(f"Using provided {len(lines)} lines and {len(words)} words")

        # Get word labels for enrichment
        word_labels: List[ReceiptWordLabel] = []
        try:
            word_labels, _ = self.dynamo.list_receipt_word_labels_for_receipt(
                image_id, receipt_id
            )
        except Exception as e:
            _log(f"Could not fetch word labels: {e}")

        # Get existing receipt metadata
        receipt_metadata = None
        try:
            receipt_metadata = self.dynamo.get_receipt_metadata(image_id, receipt_id)
        except Exception as e:
            _log(f"Could not fetch receipt metadata: {e}")

        # Step 1: Generate embeddings and get merged snapshot+delta clients
        _log(
            f"Creating embeddings for {image_id}#{receipt_id} "
            f"({len(lines)} lines, {len(words)} words)"
        )

        try:
            result = create_embeddings_and_compaction_run(
                receipt_lines=lines,
                receipt_words=words,
                image_id=image_id,
                receipt_id=receipt_id,
                chromadb_bucket=self.chromadb_bucket,
                dynamo_client=self.dynamo,
                s3_client=self.s3_client,
                receipt_metadata=receipt_metadata,
                receipt_word_labels=word_labels,
                merchant_name=(
                    receipt_metadata.merchant_name if receipt_metadata else None
                ),
                sqs_notify=True,  # Trigger async compaction
            )
        except Exception as e:
            _log(f"ERROR: Failed to create embeddings: {e}")
            logger.exception("Embedding creation failed")
            return {
                "success": False,
                "error": str(e),
                "merchant_found": False,
            }

        merchant_result = MerchantResult()

        try:
            # Step 2: Resolve merchant using merged snapshot+delta clients
            _log("Resolving merchant using snapshot+delta clients")
            merchant_result = self.merchant_resolver.resolve(
                lines_client=result.lines_client,
                lines=lines,
                words=words,
                image_id=image_id,
                receipt_id=receipt_id,
            )

            # Step 3: Enrich receipt in DynamoDB if merchant found
            if merchant_result.place_id:
                _log(
                    f"Enriching receipt with merchant: {merchant_result.merchant_name} "
                    f"(place_id={merchant_result.place_id}, "
                    f"tier={merchant_result.resolution_tier})"
                )
                self._enrich_receipt_metadata(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    merchant_result=merchant_result,
                    existing_metadata=receipt_metadata,
                )
            else:
                _log("No merchant found - receipt will not be enriched")

        except Exception as e:
            _log(f"WARNING: Merchant resolution failed: {e}")
            logger.exception("Merchant resolution failed")
            # Don't fail the whole operation if merchant resolution fails
            # The embeddings are already created and compaction is queued

        finally:
            # Always close the clients to release file locks
            result.close()

        return {
            "success": True,
            "run_id": result.compaction_run.run_id,
            "lines_count": len(lines),
            "words_count": len(words),
            "merchant_found": merchant_result.place_id is not None,
            "merchant_name": merchant_result.merchant_name,
            "merchant_place_id": merchant_result.place_id,
            "merchant_resolution_tier": merchant_result.resolution_tier,
            "merchant_confidence": merchant_result.confidence,
        }

    def _enrich_receipt_metadata(
        self,
        image_id: str,
        receipt_id: int,
        merchant_result: MerchantResult,
        existing_metadata: Optional[Any] = None,
    ) -> None:
        """
        Update receipt metadata in DynamoDB with merchant information.

        Args:
            image_id: Receipt's image_id
            receipt_id: Receipt's receipt_id
            merchant_result: Resolved merchant information
            existing_metadata: Optional pre-fetched metadata to avoid duplicate query
        """
        try:
            # Use provided metadata or fetch if not available
            metadata = existing_metadata or self.dynamo.get_receipt_metadata(
                image_id, receipt_id
            )

            if metadata:
                # Update with merchant info
                updates = {}

                if merchant_result.place_id:
                    updates["place_id"] = merchant_result.place_id

                if merchant_result.merchant_name:
                    # Only update if current is empty or different
                    if not metadata.merchant_name:
                        updates["merchant_name"] = merchant_result.merchant_name

                if merchant_result.address:
                    if not metadata.address:
                        updates["address"] = merchant_result.address

                if merchant_result.phone:
                    if not metadata.phone_number:
                        updates["phone_number"] = merchant_result.phone

                if updates:
                    self.dynamo.update_receipt_metadata(
                        image_id=image_id,
                        receipt_id=receipt_id,
                        **updates,
                    )
                    _log(f"Updated receipt metadata with: {list(updates.keys())}")
            else:
                _log(f"No existing metadata for {image_id}#{receipt_id}")

        except Exception as e:
            _log(f"ERROR: Failed to enrich receipt metadata: {e}")
            logger.exception("Metadata enrichment failed")
            raise
