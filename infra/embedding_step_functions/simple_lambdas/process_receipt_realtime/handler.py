"""Lambda handler for processing a single receipt with realtime embedding.

This handler processes one receipt (image_id + receipt_id) by:
1. Loading lines and words from DynamoDB
2. Resolving merchant name
3. Creating realtime embeddings
4. Uploading deltas to S3
5. Creating COMPACTION_RUN record
"""

import os
import logging
import tempfile
import uuid
from typing import Any, Dict

from receipt_dynamo import DynamoClient
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_label.merchant_resolution.embeddings import upsert_embeddings
from receipt_label.embedding.line.realtime import embed_lines_realtime
from receipt_label.embedding.word.realtime import embed_words_realtime
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_label.merchant_resolution.resolver import resolve_receipt
from receipt_label.data.places_api import PlacesAPI

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Process a single receipt with realtime embedding.

    Args:
        event: Lambda event with:
            - image_id: Image UUID
            - receipt_id: Receipt ID (integer)
        context: Lambda context (unused)

    Returns:
        Dictionary with processing results
    """
    logger.info("Starting process_receipt_realtime_embedding handler")

    try:
        # Extract parameters
        image_id = event.get("image_id")
        receipt_id = event.get("receipt_id")

        if not image_id or receipt_id is None:
            raise ValueError("image_id and receipt_id are required")

        receipt_id = int(receipt_id)

        logger.info(
            "Processing receipt: image_id=%s, receipt_id=%d", image_id, receipt_id
        )

        # Get environment variables
        table_name = os.environ.get("DYNAMODB_TABLE_NAME")
        chroma_bucket = os.environ.get("CHROMADB_BUCKET")
        google_places_key = os.environ.get("GOOGLE_PLACES_API_KEY")

        if not table_name:
            raise ValueError("DYNAMODB_TABLE_NAME environment variable not set")
        if not chroma_bucket:
            raise ValueError("CHROMADB_BUCKET environment variable not set")

        dynamo_client = DynamoClient(table_name)

        # Step 1: Load lines and words from DynamoDB
        logger.info("Loading lines and words from DynamoDB...")
        lines = dynamo_client.list_receipt_lines_from_receipt(image_id, receipt_id)
        words = dynamo_client.list_receipt_words_from_receipt(image_id, receipt_id)

        logger.info("Loaded %d lines and %d words", len(lines), len(words))

        if not lines and not words:
            logger.warning("No lines or words found for receipt")
            return {
                "status": "skipped",
                "reason": "no_data",
                "image_id": image_id,
                "receipt_id": receipt_id,
            }

        # Step 2: Resolve merchant name
        merchant_name = None
        try:
            places_api = PlacesAPI(api_key=google_places_key) if google_places_key else None
            chroma_http = os.environ.get("CHROMA_HTTP_ENDPOINT") or os.environ.get(
                "CHROMA_HTTP_URL"
            )

            chroma_line_client = None
            if chroma_http:
                try:
                    chroma_line_client = ChromaClient(
                        mode="read", http_url=chroma_http
                    )
                except Exception:
                    chroma_line_client = None

            def _embed_texts(texts):
                if not texts:
                    return []
                if not os.environ.get("OPENAI_API_KEY"):
                    raise RuntimeError("OPENAI_API_KEY is not set")
                from receipt_label.utils import get_client_manager

                openai_client = get_client_manager().openai
                resp = openai_client.embeddings.create(
                    model="text-embedding-3-small", input=list(texts)
                )
                return [d.embedding for d in resp.data]

            resolution = resolve_receipt(
                key=(image_id, receipt_id),
                dynamo=dynamo_client,
                places_api=places_api,
                chroma_line_client=chroma_line_client,
                embed_fn=_embed_texts,
                write_metadata=False,  # Don't write metadata in step function
            )
            best = (resolution.get("decision") or {}).get("best") or {}
            merchant_name = best.get("name")
            logger.info("Resolved merchant: %s", merchant_name)
        except Exception as e:
            logger.warning("Merchant resolution failed: %s", e)
            merchant_name = None

        # Step 3: Create embeddings and deltas
        run_id = str(uuid.uuid4())
        delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
        delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")

        logger.info("Creating ChromaDB deltas...")

        line_client = ChromaClient(
            persist_directory=delta_lines_dir,
            mode="delta",
            metadata_only=True,
        )
        word_client = ChromaClient(
            persist_directory=delta_words_dir,
            mode="delta",
            metadata_only=True,
        )

        # Upsert embeddings
        upsert_embeddings(
            line_client=line_client,
            word_client=word_client,
            line_embed_fn=embed_lines_realtime,
            word_embed_fn=embed_words_realtime,
            ctx={"lines": lines, "words": words},
            merchant_name=merchant_name,
        )

        logger.info("Created embeddings, uploading deltas to S3...")

        # Step 4: Upload deltas to S3
        lines_prefix = f"lines/delta/{run_id}/"
        words_prefix = f"words/delta/{run_id}/"

        # persist_and_upload_delta closes the client and uploads files
        lines_delta_key = line_client.persist_and_upload_delta(
            bucket=chroma_bucket,
            s3_prefix=lines_prefix,
        )

        words_delta_key = word_client.persist_and_upload_delta(
            bucket=chroma_bucket,
            s3_prefix=words_prefix,
        )

        logger.info("Uploaded deltas to S3")

        # Step 5: Create COMPACTION_RUN record
        dynamo_client.add_compaction_run(
            CompactionRun(
                run_id=run_id,
                image_id=image_id,
                receipt_id=receipt_id,
                lines_delta_prefix=lines_delta_key,
                words_delta_prefix=words_delta_key,
            )
        )

        logger.info("Created COMPACTION_RUN record")

        return {
            "status": "success",
            "image_id": image_id,
            "receipt_id": receipt_id,
            "run_id": run_id,
            "lines_count": len(lines),
            "words_count": len(words),
            "merchant_name": merchant_name,
        }

    except Exception as e:
        logger.error("Error processing receipt: %s", str(e), exc_info=True)
        return {
            "status": "error",
            "image_id": event.get("image_id"),
            "receipt_id": event.get("receipt_id"),
            "error": str(e),
        }





