"""
Harmonize Labels Handler (Container Lambda)

Downloads ChromaDB snapshot, processes merchant group labels,
and identifies semantic outliers using LLM-based analysis.

Dependencies (via pyproject.toml):
- receipt_agent: LabelHarmonizer, LabelRecord, MerchantLabelGroup, create_all_clients
- receipt_dynamo: DynamoClient
- receipt_chroma: ChromaClient

Environment Variables (set by infrastructure.py):
- Lambda-specific:
  - BATCH_BUCKET: S3 bucket for batch files
  - CHROMADB_BUCKET: S3 bucket with ChromaDB snapshots
- receipt_agent Settings (RECEIPT_AGENT_* prefix):
  - RECEIPT_AGENT_DYNAMO_TABLE_NAME: DynamoDB table name
  - RECEIPT_AGENT_OPENAI_API_KEY: OpenAI API key for embeddings
  - RECEIPT_AGENT_OLLAMA_API_KEY: Ollama API key for LLM
  - RECEIPT_AGENT_OLLAMA_BASE_URL: Ollama API base URL
  - RECEIPT_AGENT_OLLAMA_MODEL: Ollama model name
  - RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY: ChromaDB local path
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def download_chromadb_snapshot(
    bucket: str, collection: str, cache_path: str
) -> str:
    """
    Download ChromaDB snapshot from S3 if not cached.

    Uses atomic pointer pattern for consistent reads.
    """
    # Check if already cached
    chroma_db_file = os.path.join(cache_path, "chroma.sqlite3")
    if os.path.exists(chroma_db_file):
        logger.info(f"ChromaDB already cached at {cache_path}")
        return cache_path

    logger.info(f"Downloading ChromaDB snapshot from s3://{bucket}/{collection}/")

    # Get latest pointer
    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    try:
        response = s3.get_object(Bucket=bucket, Key=pointer_key)
        timestamp = response["Body"].read().decode().strip()
        logger.info(f"Latest snapshot timestamp: {timestamp}")
    except Exception as e:
        logger.error(f"Failed to get pointer: {e}")
        raise

    # Download snapshot files
    prefix = f"{collection}/snapshot/timestamped/{timestamp}/"
    paginator = s3.get_paginator("list_objects_v2")

    os.makedirs(cache_path, exist_ok=True)
    downloaded_files = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relative_path = key[len(prefix) :]
            if not relative_path or key.endswith(".snapshot_hash"):
                continue

            local_path = os.path.join(cache_path, relative_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3.download_file(bucket, key, local_path)
            downloaded_files += 1

    logger.info(f"Downloaded {downloaded_files} files to {cache_path}")
    return cache_path


async def process_merchant_group(
    labels: list,
    merchant_name: str,
    label_type: str,
    chroma_client: Any,
    dynamo_client: Any,
    embed_fn: Any,
    llm: Any,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Process a single merchant group using the LabelHarmonizer."""
    # Import here to avoid cold start overhead if not needed
    from receipt_agent.tools.label_harmonizer import (
        LabelHarmonizer,
        LabelRecord,
        MerchantLabelGroup,
    )

    # Create label records
    label_records = [
        LabelRecord(
            image_id=l["image_id"],
            receipt_id=l["receipt_id"],
            line_id=l["line_id"],
            word_id=l["word_id"],
            label=l["label"],
            validation_status=l.get("validation_status"),
            merchant_name=l.get("merchant_name"),
            word_text=l.get("word_text"),
        )
        for l in labels
    ]

    # Create merchant group
    group = MerchantLabelGroup(
        merchant_name=merchant_name,
        label_type=label_type,
        labels=label_records,
    )

    # Create harmonizer
    harmonizer = LabelHarmonizer(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        llm=llm,
    )

    # Run analysis
    logger.info(
        f"Analyzing {len(labels)} labels for {merchant_name} ({label_type})"
    )
    # analyze_group returns list[HarmonizerResult], not an object with .outliers
    # Disable similarity search to avoid ChromaDB InternalError when IDs don't exist
    # The harmonizer will still compute consensus based on labels alone
    results = await harmonizer.analyze_group(
        group, use_similarity=False
    )

    # Extract outliers (results where needs_update=True and has "OUTLIER" in changes_needed)
    outliers = [
        r for r in results
        if r.needs_update and any("OUTLIER" in change for change in r.changes_needed)
    ]

    # Get consensus label from first result (all results in a group have same consensus)
    consensus_label = results[0].consensus_label if results else None

    # Create a lookup map for label records by IDs
    label_lookup = {
        (lr.image_id, lr.receipt_id, lr.line_id, lr.word_id): lr
        for lr in label_records
    }

    # Prepare outlier details
    outlier_details = []
    for o in outliers:
        key = (o.image_id, o.receipt_id, o.line_id, o.word_id)
        label_record = label_lookup.get(key)
        outlier_details.append({
            "word_text": label_record.word_text if label_record else None,
            "validation_status": label_record.validation_status if label_record else None,
            "image_id": o.image_id,
            "receipt_id": o.receipt_id,
            "line_id": o.line_id,
            "word_id": o.word_id,
            "current_label": o.current_label,
            "consensus_label": o.consensus_label,
            "changes_needed": o.changes_needed,
        })

    return {
        "merchant_name": merchant_name,
        "label_type": label_type,
        "labels_processed": len(labels),
        "outliers_found": len(outliers),
        "outlier_details": outlier_details,
        "consensus": consensus_label,
    }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Harmonize labels for a single merchant group.

    1. Download ChromaDB snapshot (cached in /tmp)
    2. Stream labels from S3 NDJSON file
    3. Run harmonization with LLM-based outlier detection
    4. Upload results to S3

    Input (two formats supported):
    1. Direct merchant_group object (legacy):
    {
        "merchant_group": {
            "merchant_name": "Sprouts",
            "batch_file": "batches/.../sprouts.ndjson",
            "label_type": "GRAND_TOTAL"
        },
        "execution_id": "abc123",
        "dry_run": true
    }

    2. Index-based (best practice for large payloads):
    {
        "index": 0,
        "work_items_manifest_s3_key": "batches/.../combined_manifest.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "dry_run": true
    }
    """
    execution_id = event.get("execution_id", "unknown")
    dry_run = event.get("dry_run", True)

    # Lambda-specific env vars
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET", "")
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET", "")

    # Support both direct merchant_group and index-based patterns
    if "index" in event and "work_items_manifest_s3_key" in event:
        # Index-based: read work item from S3 manifest
        index = event["index"]
        manifest_key = event["work_items_manifest_s3_key"]

        logger.info(
            "Loading work item %d from manifest s3://%s/%s",
            index,
            batch_bucket,
            manifest_key,
        )

        response = s3.get_object(Bucket=batch_bucket, Key=manifest_key)
        manifest_content = response["Body"].read().decode("utf-8")
        work_items = json.loads(manifest_content)

        if index >= len(work_items):
            raise ValueError(f"Index {index} out of range for manifest with {len(work_items)} items")

        merchant_group = work_items[index]
    else:
        # Legacy: direct merchant_group object
        merchant_group = event["merchant_group"]

    merchant_name = merchant_group["merchant_name"]
    label_type = merchant_group["label_type"]
    batch_file = merchant_group["batch_file"]

    logger.info(
        f"Processing {merchant_name} - {label_type} "
        f"(execution_id={execution_id}, dry_run={dry_run})"
    )

    try:
        # Setup ChromaDB (cached in /tmp)
        chroma_path = os.environ.get(
            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY", "/tmp/chromadb"
        )
        download_chromadb_snapshot(chromadb_bucket, "words", chroma_path)

        # Update environment for receipt_agent to find ChromaDB
        # The factory uses RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY
        os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = chroma_path

        # Create clients using the receipt_agent factory
        # This ensures all environment variables are properly used
        from receipt_agent.clients.factory import (
            create_dynamo_client,
            create_chroma_client,
            create_embed_fn,
        )
        from receipt_agent.config.settings import get_settings

        settings = get_settings()
        logger.info(f"Settings loaded: table={settings.dynamo_table_name}")

        dynamo = create_dynamo_client(settings=settings)
        chroma = create_chroma_client(settings=settings)
        embed_fn = create_embed_fn(settings=settings)

        # Create LLM using settings (matching CLI pattern)
        from langchain_ollama import ChatOllama

        ollama_api_key = settings.ollama_api_key.get_secret_value() if settings.ollama_api_key else None
        if not ollama_api_key:
            raise ValueError("RECEIPT_AGENT_OLLAMA_API_KEY not set")

        llm = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            client_kwargs={
                "headers": {"Authorization": f"Bearer {ollama_api_key}"},
                "timeout": 120,  # Longer timeout for agent reasoning
            },
            temperature=0.3,
        )
        logger.info(
            f"LLM created: model={settings.ollama_model}, "
            f"base_url={settings.ollama_base_url}"
        )

        # Download labels from S3
        logger.info(f"Downloading labels from s3://{batch_bucket}/{batch_file}")
        response = s3.get_object(Bucket=batch_bucket, Key=batch_file)
        ndjson_content = response["Body"].read().decode("utf-8")

        # Parse NDJSON
        labels = []
        for line in ndjson_content.strip().split("\n"):
            if line:
                labels.append(json.loads(line))

        logger.info(f"Loaded {len(labels)} labels from S3")

        # Run harmonization
        result = asyncio.run(
            process_merchant_group(
                labels=labels,
                merchant_name=merchant_name,
                label_type=label_type,
                chroma_client=chroma,
                dynamo_client=dynamo,
                embed_fn=embed_fn,
                llm=llm,
                dry_run=dry_run,
            )
        )

        # Upload results
        safe_name = (
            merchant_name.lower()
            .replace(" ", "-")
            .replace("/", "-")
            .replace("'", "")
        )
        results_key = f"results/{execution_id}/{label_type}/{safe_name}.json"

        s3.put_object(
            Bucket=batch_bucket,
            Key=results_key,
            Body=json.dumps(result, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )

        logger.info(
            f"Results uploaded to s3://{batch_bucket}/{results_key}: "
            f"{result['outliers_found']} outliers found"
        )

        return {
            "status": "completed",
            "results_path": f"s3://{batch_bucket}/{results_key}",
            **result,
        }

    except Exception as e:
        logger.error(f"Error processing {merchant_name}: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "merchant_name": merchant_name,
            "label_type": label_type,
        }
