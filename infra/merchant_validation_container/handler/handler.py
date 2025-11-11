"""
Container-based Lambda handler for merchant validation with NDJSON embedding trigger.

This handler:
1. Validates merchant data using direct EFS-backed ChromaDB access
2. Creates ReceiptMetadata with merchant information
3. Triggers NDJSON embedding process automatically
"""

import os
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict
from io import StringIO

import boto3
from chromadb import PersistentClient
from chromadb.config import Settings

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_label.data.places_api import PlacesAPI
from receipt_label.merchant_resolution.resolver import resolve_receipt


# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DirectChromaAdapter:
    """Adapter for direct ChromaDB access via EFS."""

    def __init__(self, chroma_root: str):
        logger.info(f"Initializing ChromaDB client at {chroma_root}")
        self.client = PersistentClient(
            path=chroma_root,
            settings=Settings(
                anonymized_telemetry=False,
                allow_reset=False,
            )
        )
        self.lines_collection = self.client.get_collection("lines")
        logger.info("ChromaDB client initialized successfully")

    def query(
        self,
        collection_name: str,
        query_embeddings: list[list[float]] | None = None,
        n_results: int = 10,
        where: Dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> Dict[str, Any]:
        """Query ChromaDB directly from EFS."""
        return self.lines_collection.query(
            query_embeddings=query_embeddings,
            n_results=n_results,
            where=where,
            include=include or ["metadatas", "documents", "distances"],
        )


def _embed_fn_from_openai_texts(texts):
    """Generate embeddings using OpenAI API."""
    if not texts:
        return []
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY not set, returning zero vectors")
        return [[0.0] * 1536 for _ in texts]

    from receipt_label.utils import get_client_manager

    openai_client = get_client_manager().openai
    embed_response = openai_client.embeddings.create(
        model="text-embedding-3-small", input=list(texts)
    )
    return [d.embedding for d in embed_response.data]


def export_receipt_ndjson(
    dynamo: DynamoClient,
    s3_client,
    bucket: str,
    image_id: str,
    receipt_id: int,
    run_id: str
) -> Dict[str, str]:
    """
    Export receipt lines and words to NDJSON files in S3.

    Returns:
        Dict with S3 paths: {"lines_path": "s3://...", "words_path": "s3://..."}
    """
    logger.info(f"Exporting NDJSON for receipt {image_id}/{receipt_id}")

    # Query lines and words from DynamoDB
    lines, _ = dynamo.list_receipt_lines(image_id, receipt_id, limit=1000)
    words, _ = dynamo.list_receipt_words(image_id, receipt_id, limit=10000)

    logger.info(f"Found {len(lines)} lines and {len(words)} words")

    # Create NDJSON content
    lines_ndjson = StringIO()
    for line in lines:
        lines_ndjson.write(json.dumps(line.to_dict()) + "\n")

    words_ndjson = StringIO()
    for word in words:
        words_ndjson.write(json.dumps(word.to_dict()) + "\n")

    # Upload to S3
    base_path = f"receipts/{image_id}/receipt-{receipt_id:05d}"
    lines_key = f"{base_path}/lines.ndjson"
    words_key = f"{base_path}/words.ndjson"

    s3_client.put_object(
        Bucket=bucket,
        Key=lines_key,
        Body=lines_ndjson.getvalue(),
        ContentType="application/x-ndjson"
    )

    s3_client.put_object(
        Bucket=bucket,
        Key=words_key,
        Body=words_ndjson.getvalue(),
        ContentType="application/x-ndjson"
    )

    logger.info(f"Uploaded NDJSON files to S3: {lines_key}, {words_key}")

    return {
        "lines_path": f"s3://{bucket}/{lines_key}",
        "words_path": f"s3://{bucket}/{words_key}"
    }


def create_or_update_compaction_run(
    dynamo: DynamoClient,
    image_id: str,
    receipt_id: int,
    state: str = "PROCESSING"
) -> str:
    """
    Create or update COMPACTION_RUN record in DynamoDB.

    Returns:
        run_id: UUID for the compaction run
    """
    logger.info(f"Creating/updating COMPACTION_RUN for {image_id}/{receipt_id}")

    # Check if COMPACTION_RUN already exists
    try:
        existing_runs = dynamo.query_compaction_runs(image_id, receipt_id)
    except Exception as e:
        logger.warning(f"Error querying existing runs: {e}, creating new one")
        existing_runs = []

    if existing_runs:
        # Update existing run
        run = existing_runs[0]
        run.lines_state = state
        run.words_state = state
        run.timestamp = datetime.now(timezone.utc)
        logger.info(f"Updating existing COMPACTION_RUN: {run.run_id}")
    else:
        # Create new run
        run = CompactionRun(
            run_id=str(uuid.uuid4()),
            image_id=image_id,
            receipt_id=receipt_id,
            lines_state=state,
            words_state=state,
            timestamp=datetime.now(timezone.utc)
        )
        logger.info(f"Creating new COMPACTION_RUN: {run.run_id}")

    dynamo.put_compaction_run(run)
    return run.run_id


def queue_embedding_job(
    sqs_client,
    queue_url: str,
    run_id: str,
    image_id: str,
    receipt_id: int,
    s3_paths: Dict[str, str],
    merchant_name: str
) -> None:
    """
    Queue embedding job to embed-ndjson-queue.

    The embed-from-ndjson Lambda will process this message.
    """
    logger.info(f"Queuing embedding job for run {run_id}")

    message = {
        "run_id": run_id,
        "image_id": image_id,
        "receipt_id": receipt_id,
        "lines_ndjson_path": s3_paths["lines_path"],
        "words_ndjson_path": s3_paths["words_path"],
        "merchant_name": merchant_name,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    sqs_client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message)
    )

    logger.info(f"Successfully queued embedding job to {queue_url}")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Validate receipt merchant and trigger NDJSON embedding process.

    Event format:
        {
            "image_id": "uuid",
            "receipt_id": 1
        }
    """
    logger.info(f"Starting merchant validation handler with event: {json.dumps(event)}")

    image_id = event["image_id"]
    receipt_id = int(event["receipt_id"])

    # Initialize clients
    dynamo = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])
    places_api = PlacesAPI(api_key=os.environ.get("GOOGLE_PLACES_API_KEY"))
    chroma_client = DirectChromaAdapter(os.environ.get("CHROMA_ROOT", "/mnt/chroma"))
    s3_client = boto3.client("s3")
    sqs_client = boto3.client("sqs")

    # 1. Resolve merchant (existing logic)
    logger.info("Step 1: Resolving merchant")
    resolution = resolve_receipt(
        key=(image_id, receipt_id),
        dynamo=dynamo,
        places_api=places_api,
        chroma_line_client=chroma_client,
        embed_fn=_embed_fn_from_openai_texts,
        write_metadata=True,
    )

    decision = resolution.get("decision") or {}
    best = decision.get("best") or {}
    merchant_name = best.get("merchant_name", "")

    logger.info(f"Merchant resolved: {merchant_name}")

    # 2. Trigger NDJSON embedding process
    embedding_triggered = False
    run_id = None
    error_message = None

    try:
        logger.info("Step 2: Triggering NDJSON embedding process")

        # Create/update COMPACTION_RUN
        run_id = create_or_update_compaction_run(
            dynamo, image_id, receipt_id, state="PROCESSING"
        )

        # Export NDJSON to S3
        s3_paths = export_receipt_ndjson(
            dynamo=dynamo,
            s3_client=s3_client,
            bucket=os.environ["CHROMADB_BUCKET"],
            image_id=image_id,
            receipt_id=receipt_id,
            run_id=run_id
        )

        # Queue embedding job
        queue_embedding_job(
            sqs_client=sqs_client,
            queue_url=os.environ["EMBED_NDJSON_QUEUE_URL"],
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            s3_paths=s3_paths,
            merchant_name=merchant_name
        )

        embedding_triggered = True
        logger.info("Successfully triggered NDJSON embedding process")

    except Exception as e:
        error_message = str(e)
        logger.error(f"Failed to trigger embedding: {e}", exc_info=True)

    result = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "wrote_metadata": bool(resolution.get("wrote_metadata")),
        "best_source": best.get("source"),
        "best_score": best.get("score"),
        "best_place_id": best.get("place_id"),
        "merchant_name": merchant_name,
        "embedding_triggered": embedding_triggered,
        "run_id": run_id,
    }

    if error_message:
        result["embedding_error"] = error_message

    logger.info(f"Handler completed: {json.dumps(result)}")
    return result


# For local testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(f"Usage: python {__file__} <image_id> <receipt_id>")
        sys.exit(1)

    test_event = {"image_id": sys.argv[1], "receipt_id": int(sys.argv[2])}

    cli_resp = lambda_handler(test_event, None)
    print(f"Result: {json.dumps(cli_resp, indent=2)}")

