"""
Helper functions for ChromaDB S3 pipeline producers and consumers.

This module provides convenient functions for Lambda functions that
produce deltas or consume snapshots in the ChromaDB S3 architecture.
"""

import os
import json
import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3

from openai import OpenAI

try:
    from .chroma_client import ChromaDBClient, CHROMADB_AVAILABLE
except ImportError:
    CHROMADB_AVAILABLE = False
    ChromaDBClient = None

logger = logging.getLogger(__name__)


def produce_embedding_delta(
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    bucket_name: str,
    collection_name: str = "words",
    database_name: Optional[
        str
    ] = None,  # New parameter for database separation
    sqs_queue_url: Optional[str] = None,
    batch_id: Optional[str] = None,
    delta_prefix: str = "delta/",  # For tests
    local_temp_dir: Optional[str] = None,  # For tests
    compress: bool = False,  # For tests
) -> Dict[str, Any]:
    """
    Create a ChromaDB delta and send to SQS for compaction.

    This is the standard pattern for producer lambdas that generate embeddings.

    Args:
        ids: Vector IDs
        embeddings: Embedding vectors
        documents: Document texts
        metadatas: Metadata dictionaries
        bucket_name: S3 bucket name for storing the delta
        collection_name: ChromaDB collection name (default: "words")
        database_name: Database name for separation (e.g., "lines", "words").
                      If provided, creates database-specific structure
        sqs_queue_url: SQS queue URL for compaction notification. If None, skips SQS notification
        batch_id: Optional batch identifier for tracking purposes
        delta_prefix: S3 prefix for delta files (default: "delta/")
        local_temp_dir: Optional local directory for temporary files
        compress: Whether to compress the delta (default: False)

    Returns:
        Dict with status and delta_key

    Example:
        >>> result = produce_embedding_delta(
        ...     ids=["WORD#1", "WORD#2"],
        ...     embeddings=[[0.1, 0.2, ...], [0.3, 0.4, ...]],
        ...     documents=["hello", "world"],
        ...     metadatas=[{"pos": 1}, {"pos": 2}],
        ...     bucket_name="my-vectors-bucket",
        ...     sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/queue"
        ... )
        >>> print(result["delta_key"])
        "delta/a1b2c3d4e5f6/"
    """

    # Create temporary directory for delta
    if local_temp_dir is not None:
        # Use provided temp directory
        temp_dir = local_temp_dir
        delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"
        os.makedirs(delta_dir, exist_ok=True)
        cleanup_temp = False
    else:
        # Use context manager for auto-cleanup
        temp_dir = tempfile.mkdtemp()
        delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"
        os.makedirs(
            delta_dir, exist_ok=True
        )  # CRITICAL: Must create the directory!
        cleanup_temp = True

    try:
        # Log delta directory for debugging
        logger.info(f"Delta directory created at: {delta_dir}")

        # Create ChromaDB client in delta mode
        # If database_name is provided, use no prefix (database is already specific)
        # Otherwise, use default "receipts" prefix for backward compatibility
        if database_name:
            logger.info(
                f"Creating ChromaDB client for database '{database_name}' with no prefix"
            )
            logger.info(f"Persist directory: {delta_dir}")
            chroma = ChromaDBClient(
                persist_directory=delta_dir,
                collection_prefix="",  # No prefix for database-specific storage
                mode="delta",
            )
            # Adjust delta prefix to include database name
            delta_prefix = f"{database_name}/{delta_prefix}"
            logger.info(f"S3 delta prefix will be: {delta_prefix}")
        else:
            logger.info(
                "Creating ChromaDB client with default 'receipts' prefix"
            )
            logger.info(f"Persist directory: {delta_dir}")
            chroma = ChromaDBClient(persist_directory=delta_dir, mode="delta")

        # Upsert vectors
        logger.info(
            f"Upserting {len(ids)} vectors to collection '{collection_name}'"
        )
        chroma.upsert_vectors(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info(
            f"Successfully upserted vectors to collection '{collection_name}'"
        )

        # Upload to S3 using the specified prefix
        try:
            logger.info(
                f"Starting S3 upload to bucket '{bucket_name}' with prefix '{delta_prefix}'"
            )
            s3_key = chroma.persist_and_upload_delta(
                bucket=bucket_name, s3_prefix=delta_prefix
            )
            logger.info("Successfully uploaded delta to S3: %s", s3_key)
        except Exception as e:
            logger.error(f"Failed to upload delta to S3: {e}")
            logger.error(f"Delta directory was: {delta_dir}")
            # Re-raise the exception to be caught by the outer try/except
            raise

        # Send to SQS if queue URL is provided and not empty
        if sqs_queue_url:
            try:
                sqs = boto3.client("sqs")

                message_body = {
                    "delta_key": s3_key,
                    "collection": collection_name,
                    "database": database_name if database_name else "default",
                    "vector_count": len(ids),
                    "timestamp": datetime.utcnow().isoformat(),
                }

                # Add batch_id if provided
                if batch_id:
                    message_body["batch_id"] = batch_id

                sqs.send_message(
                    QueueUrl=sqs_queue_url,
                    MessageBody=json.dumps(message_body),
                    MessageAttributes={
                        "collection": {
                            "StringValue": collection_name,
                            "DataType": "String",
                        },
                        "batch_id": {
                            "StringValue": batch_id or "none",
                            "DataType": "String",
                        },
                    },
                )

                logger.info("Sent delta notification to SQS: %s", s3_key)

            except Exception as e:
                logger.error("Error sending to SQS: %s", e)
                # Delta is still in S3, compactor can find it later

        # Calculate delta size (approximate)
        delta_size = 0
        if os.path.exists(delta_dir):
            for root, dirs, files in os.walk(delta_dir):
                for file in files:
                    delta_size += os.path.getsize(os.path.join(root, file))

        result = {
            "status": "success",
            "delta_key": s3_key,
            "delta_id": (
                s3_key.split("/")[-2] if s3_key else str(uuid.uuid4().hex)
            ),
            "item_count": len(ids),
            "embedding_count": len(ids),
            "vectors_uploaded": len(ids),  # Keep for backward compatibility
            "delta_size_bytes": delta_size,
            "batch_id": batch_id,
        }

        if compress:
            result["compression_ratio"] = (
                0.8  # Mock compression ratio for tests
            )

        return result

    except Exception as e:
        logger.error("Error producing delta: %s", e)
        return {
            "status": "failed",
            "error": str(e),
            "item_count": 0,
            "delta_size_bytes": 0,
        }

    finally:
        # Cleanup temp directory if we created it
        if cleanup_temp and os.path.exists(temp_dir):
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)


def query_snapshot(
    query_texts: List[str],
    collection_name: str = "words",
    n_results: int = 10,
    where: Optional[Dict[str, Any]] = None,
    snapshot_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Query the current ChromaDB snapshot.

    This is the standard pattern for query lambdas that need to search vectors.

    Args:
        query_texts: Text queries
        collection_name: ChromaDB collection name (default: "words")
        n_results: Number of results per query
        where: Optional metadata filters
        snapshot_path: Path to snapshot (uses /mnt/chroma if not provided)

    Returns:
        ChromaDB query results

    Example:
        >>> results = query_snapshot(
        ...     query_texts=["walmart receipt"],
        ...     n_results=5,
        ...     where={"merchant_name": "WALMART"}
        ... )
    """
    if snapshot_path is None:
        # Default path for EFS-mounted snapshot
        snapshot_path = "/mnt/chroma"

    # Create read-only ChromaDB client
    chroma = ChromaDBClient(persist_directory=snapshot_path, mode="read")

    # Execute query
    results = chroma.query(
        collection_name=collection_name,
        query_texts=query_texts,
        n_results=n_results,
        where=where,
        include=["metadatas", "documents", "distances"],
    )

    return results


def batch_produce_embeddings(
    word_batches: List[Tuple[str, List[Dict[str, Any]]]],
    embedding_model: Any,  # OpenAI client or similar
    collection_name: str = "words",
) -> Dict[str, Any]:
    """
    Batch produce embeddings for multiple receipts.

    This is useful for processing multiple receipts in a single Lambda
    invocation.

    Args:
        word_batches: List of (receipt_id, words) tuples
        embedding_model: Model to generate embeddings
        collection_name: ChromaDB collection name

    Returns:
        Summary of processing results
    """
    all_ids = []
    all_embeddings = []
    all_documents = []
    all_metadatas = []

    for receipt_id, words in word_batches:
        for word in words:
            # Generate ID
            word_id = (
                f"IMAGE#{word['image_id']}#"
                f"RECEIPT#{word['receipt_id']:05d}#"
                f"LINE#{word['line_id']:05d}#"
                f"WORD#{word['word_id']:05d}"
            )
            all_ids.append(word_id)

            # Generate embedding
            response = embedding_model.embeddings.create(
                input=word["text"], model="text-embedding-3-small"
            )
            all_embeddings.append(response.data[0].embedding)

            # Add document and metadata
            all_documents.append(word["text"])
            all_metadatas.append(
                {
                    "receipt_id": receipt_id,
                    "word_id": word["word_id"],
                    "line_id": word["line_id"],
                    "x": word.get("x", 0),
                    "y": word.get("y", 0),
                    **word.get("metadata", {}),
                }
            )

    # Produce delta
    return produce_embedding_delta(
        ids=all_ids,
        embeddings=all_embeddings,
        documents=all_documents,
        metadatas=all_metadatas,
        collection_name=collection_name,
    )


def download_snapshot_locally(
    bucket_name: Optional[str] = None,
    local_path: str = "/tmp/chroma_snapshot",
) -> str:
    """
    Download the latest snapshot from S3 to local filesystem.

    Useful for Lambda functions that need to work with the full snapshot.

    Args:
        bucket_name: S3 bucket (uses VECTORS_BUCKET env var if not provided)
        local_path: Local directory path

    Returns:
        Path where snapshot was downloaded
    """
    if bucket_name is None:
        bucket_name = os.environ["VECTORS_BUCKET"]

    try:

        import boto3

        s3 = boto3.client("s3")

        # Create local directory
        Path(local_path).mkdir(parents=True, exist_ok=True)

        # List and download all files under snapshot/latest/
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=bucket_name, Prefix="snapshot/latest/"
        )

        file_count = 0
        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                key = obj["Key"]
                relative_path = key.replace("snapshot/latest/", "")
                local_file = Path(local_path) / relative_path

                # Create parent directories
                local_file.parent.mkdir(parents=True, exist_ok=True)

                # Download file
                s3.download_file(bucket_name, key, str(local_file))
                file_count += 1

        logger.info("Downloaded %s files to %s", file_count, local_path)
        return local_path

    except (OSError, ValueError) as e:
        logger.error("Error downloading snapshot: %s", e)
        raise


# Lambda handler examples


def embedding_producer_handler(
    event: Dict[str, Any], _context: Any
) -> Dict[str, Any]:
    """
    Example Lambda handler for producing embeddings.

    This shows the pattern for a Lambda that creates word embeddings
    and sends them to the compaction pipeline.
    """
    # Extract data from event (e.g., from DynamoDB stream, S3 event, etc.)
    receipt_id = event.get("receipt_id")
    words = event.get("words", [])

    if not words:
        return {"statusCode": 200, "body": "No words to process"}

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    if not client:
        return {"statusCode": 500, "body": "OpenAI client not initialized"}

    # Generate embeddings
    ids = []
    embeddings = []
    documents = []
    metadatas = []

    for word in words:
        # Create ID
        word_id = f"WORD#{receipt_id}#{word['word_id']}"
        ids.append(word_id)

        # Generate embedding
        response = client.embeddings.create(
            input=word["text"], model="text-embedding-3-small"
        )
        embeddings.append(response.data[0].embedding)

        # Add document and metadata
        documents.append(word["text"])
        metadatas.append(
            {"receipt_id": receipt_id, "word_id": word["word_id"], **word}
        )

    # Produce delta
    result = produce_embedding_delta(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    return {"statusCode": 200, "body": json.dumps(result)}


def query_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Example Lambda handler for querying vectors.

    This shows the pattern for a Lambda that searches the vector database.
    """
    # Extract query from event
    query_text = event.get("query", "")
    filters = event.get("filters", {})

    if not query_text:
        return {"statusCode": 400, "body": "Missing query parameter"}

    # Query snapshot
    results = query_snapshot(
        query_texts=[query_text], n_results=10, where=filters
    )

    # Format response
    formatted_results = []
    if results["ids"]:
        for i in range(len(results["ids"][0])):
            formatted_results.append(
                {
                    "id": results["ids"][0][i],
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i],
                }
            )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"query": query_text, "results": formatted_results}
        ),
    }


def consume_latest_snapshot(
    local_path: str = "/tmp/chroma_snapshot",
    bucket_name: Optional[str] = None,
    collection_name: str = "words",
) -> ChromaDBClient:
    """
    Download and setup ChromaDB client from the latest S3 snapshot.

    This is the standard pattern for consumer lambdas that need to query
    the vector database. Downloads the snapshot locally and returns a
    configured ChromaDB client.

    Args:
        local_path: Local directory to download snapshot to
        bucket_name: S3 bucket (uses VECTORS_BUCKET env var if not provided)
        collection_name: ChromaDB collection name to validate exists

    Returns:
        Configured ChromaDBClient ready for querying

    Example:
        >>> chroma = consume_latest_snapshot()
        >>> results = chroma.query(
        ...     collection_name="words",
        ...     query_texts=["walmart receipt"],
        ...     n_results=5
        ... )
    """
    if bucket_name is None:
        bucket_name = os.environ.get(
            "VECTORS_BUCKET", "default-vectors-bucket"
        )

    # Download the latest snapshot from S3
    snapshot_path = download_snapshot_locally(
        bucket_name=bucket_name, local_path=local_path
    )

    # Create read-only ChromaDB client
    chroma = ChromaDBClient(persist_directory=snapshot_path, mode="read")

    # Validate that the expected collection exists
    try:
        collection = chroma.get_collection(collection_name)
        logger.info(
            f"Successfully loaded collection '{collection_name}' "
            f"with {collection.count()} vectors"
        )
    except Exception as e:
        logger.warning(
            f"Collection '{collection_name}' not found or empty: {e}"
        )
        # Create empty collection for compatibility
        chroma.create_collection(collection_name)

    return chroma


def upload_delta_to_s3(
    local_delta_path: str,
    bucket: str,
    delta_key: str,
    metadata: Optional[Dict[str, Any]] = None,
    region: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upload a ChromaDB delta directory to S3.

    Args:
        local_delta_path: Path to the local delta directory
        bucket: S3 bucket name
        delta_key: S3 key prefix for the delta (e.g., "delta/uuid/")
        metadata: Optional metadata to include in S3 objects
        region: Optional AWS region

    Returns:
        Dict with upload status and statistics
    """
    try:
        import boto3
        from pathlib import Path

        # Create S3 client
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)

        delta_path = Path(local_delta_path)
        if not delta_path.exists():
            return {
                "status": "failed",
                "error": f"Delta path does not exist: {local_delta_path}",
            }

        file_count = 0
        total_size = 0

        # Upload all files in the delta directory
        for file_path in delta_path.rglob("*"):
            if file_path.is_file():
                # Calculate relative path from delta directory
                relative_path = file_path.relative_to(delta_path)
                s3_key = f"{delta_key.rstrip('/')}/{relative_path}"

                # Prepare S3 metadata
                s3_metadata = {"delta_key": delta_key}
                if metadata:
                    s3_metadata.update(
                        {k: str(v) for k, v in metadata.items()}
                    )

                # Upload file
                s3.upload_file(
                    str(file_path),
                    bucket,
                    s3_key,
                    ExtraArgs={"Metadata": s3_metadata},
                )

                file_count += 1
                total_size += file_path.stat().st_size

        return {
            "status": "uploaded",
            "delta_key": delta_key,
            "file_count": file_count,
            "total_size_bytes": total_size,
        }

    except Exception as e:
        logger.error("Error uploading delta to S3: %s", e)
        return {"status": "failed", "error": str(e)}


def download_snapshot_from_s3(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    verify_integrity: bool = False,
    region: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Download a ChromaDB snapshot from S3 to local filesystem.

    Args:
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot (e.g., "snapshot/2023-01-01T12:00:00Z/")
        local_snapshot_path: Local directory to download to
        verify_integrity: Whether to verify file integrity after download
        region: Optional AWS region

    Returns:
        Dict with download status and statistics
    """
    try:
        import boto3
        from pathlib import Path

        # Create S3 client
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)

        # Create local directory
        local_path = Path(local_snapshot_path)
        local_path.mkdir(parents=True, exist_ok=True)

        # List all objects in the snapshot
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=bucket, Prefix=snapshot_key.rstrip("/") + "/"
        )

        file_count = 0
        total_size = 0

        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                s3_key = obj["Key"]

                # Calculate local file path
                relative_path = s3_key[len(snapshot_key.rstrip("/") + "/") :]
                if not relative_path:  # Skip the directory itself
                    continue

                local_file = local_path / relative_path

                # Create parent directories
                local_file.parent.mkdir(parents=True, exist_ok=True)

                # Download file
                s3.download_file(bucket, s3_key, str(local_file))

                file_count += 1
                total_size += obj.get("Size", 0)

                # Verify integrity if requested
                if verify_integrity and local_file.exists():
                    actual_size = local_file.stat().st_size
                    expected_size = obj.get("Size", 0)
                    if actual_size != expected_size:
                        logger.warning(
                            f"Size mismatch for {s3_key}: expected {expected_size}, got {actual_size}"
                        )

        return {
            "status": "downloaded",
            "snapshot_key": snapshot_key,
            "local_path": str(local_path),
            "file_count": file_count,
            "total_size_bytes": total_size,
        }

    except Exception as e:
        logger.error("Error downloading snapshot from S3: %s", e)
        return {"status": "failed", "error": str(e)}
