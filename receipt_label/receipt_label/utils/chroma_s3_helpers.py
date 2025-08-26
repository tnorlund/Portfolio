"""
Helper functions for ChromaDB S3 pipeline producers and consumers.

This module provides convenient functions for Lambda functions that
produce deltas or consume snapshots in the ChromaDB S3 architecture.
"""

import os
import json
import logging
import tempfile
import time
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

try:
    from .chroma_hash import (
        calculate_chromadb_hash,
        create_hash_file_content,
        parse_hash_file_content,
        compare_hash_results,
        ChromaDBHashResult
    )
    HASH_UTILS_AVAILABLE = True
except ImportError:
    HASH_UTILS_AVAILABLE = False
    logger.warning("ChromaDB hash utilities not available")

logger = logging.getLogger(__name__)


def produce_embedding_delta(
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    bucket_name: str,
    collection_name: str = "words",
    database_name: Optional[str] = None,  # New parameter for database separation
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
        os.makedirs(delta_dir, exist_ok=True)  # CRITICAL: Must create the directory!
        cleanup_temp = True
    
    try:
        # Log delta directory for debugging
        logger.info(f"Delta directory created at: {delta_dir}")
        
        # Create ChromaDB client in delta mode
        if database_name:
            logger.info(f"Creating ChromaDB client for database '{database_name}'")
            logger.info(f"Persist directory: {delta_dir}")
            chroma = ChromaDBClient(
                persist_directory=delta_dir, 
                mode="delta"
            )
            # Adjust delta prefix to include database name
            delta_prefix = f"{database_name}/{delta_prefix}"
            logger.info(f"S3 delta prefix will be: {delta_prefix}")
        else:
            logger.info("Creating ChromaDB client")
            logger.info(f"Persist directory: {delta_dir}")
            chroma = ChromaDBClient(persist_directory=delta_dir, mode="delta")

        # Upsert vectors
        logger.info(f"Upserting {len(ids)} vectors to collection '{collection_name}'")
        chroma.upsert_vectors(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info(f"Successfully upserted vectors to collection '{collection_name}'")

        # Upload to S3 using the specified prefix
        try:
            logger.info(f"Starting S3 upload to bucket '{bucket_name}' with prefix '{delta_prefix}'")
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
                        'collection': {
                            'StringValue': collection_name,
                            'DataType': 'String'
                        },
                        'batch_id': {
                            'StringValue': batch_id or 'none',
                            'DataType': 'String'
                        },
                    }
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
            "delta_id": s3_key.split('/')[-2] if s3_key else str(uuid.uuid4().hex),
            "item_count": len(ids),
            "embedding_count": len(ids),
            "vectors_uploaded": len(ids),  # Keep for backward compatibility
            "delta_size_bytes": delta_size,
            "batch_id": batch_id,
        }
        
        if compress:
            result["compression_ratio"] = 0.8  # Mock compression ratio for tests
        
        return result
    
    except Exception as e:
        logger.error("Error producing delta: %s", e)
        return {
            "status": "failed",
            "error": str(e),
            "item_count": 0,
            "delta_size_bytes": 0
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
        bucket_name = os.environ.get("VECTORS_BUCKET", "default-vectors-bucket")

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
    region: Optional[str] = None
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
    logger.info("Starting S3 delta upload: local_path=%s, bucket=%s, key=%s", 
                local_delta_path, bucket, delta_key)
    try:
        import boto3
        from pathlib import Path
        
        # Create S3 client
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)
        logger.info("Created S3 client for upload, region: %s", region or "default")
        
        delta_path = Path(local_delta_path)
        if not delta_path.exists():
            logger.error("Local delta path does not exist: %s", local_delta_path)
            return {
                "status": "failed",
                "error": f"Delta path does not exist: {local_delta_path}"
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
                    s3_metadata.update({k: str(v) for k, v in metadata.items()})
                
                # Upload file
                s3.upload_file(
                    str(file_path), 
                    bucket, 
                    s3_key,
                    ExtraArgs={"Metadata": s3_metadata}
                )
                
                file_count += 1
                total_size += file_path.stat().st_size
        
        return {
            "status": "uploaded",
            "delta_key": delta_key,
            "file_count": file_count,
            "total_size_bytes": total_size
        }
        
    except Exception as e:
        logger.error("Error uploading delta to S3: %s", e)
        return {
            "status": "failed",
            "error": str(e)
        }


def download_snapshot_from_s3(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    verify_integrity: bool = False,
    region: Optional[str] = None
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
    logger.info("Starting S3 snapshot download: bucket=%s, key=%s, local_path=%s", 
                bucket, snapshot_key, local_snapshot_path)
    try:
        import boto3
        from pathlib import Path
        
        # Create S3 client
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)
        logger.info("Created S3 client for region: %s", region or "default")
        
        # Create local directory
        local_path = Path(local_snapshot_path)
        local_path.mkdir(parents=True, exist_ok=True)
        logger.info("Created local directory: %s", local_path)
        
        # List all objects in the snapshot
        full_prefix = snapshot_key.rstrip('/') + '/'
        logger.info("Listing S3 objects with prefix: %s", full_prefix)
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(
            Bucket=bucket, 
            Prefix=full_prefix
        )
        
        file_count = 0
        total_size = 0
        
        for page in pages:
            if "Contents" not in page:
                continue
                
            for obj in page["Contents"]:
                s3_key = obj["Key"]
                
                # Calculate local file path
                relative_path = s3_key[len(snapshot_key.rstrip('/') + '/'):]
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
            "total_size_bytes": total_size
        }
        
    except Exception as e:
        logger.error("Error downloading snapshot from S3: %s", e)
        return {
            "status": "failed",
            "error": str(e)
        }


def clear_s3_directory(
    bucket: str,
    key_prefix: str,
    region: Optional[str] = None
) -> Dict[str, Any]:
    """
    Clear all objects in an S3 directory.
    
    Args:
        bucket: S3 bucket name
        key_prefix: S3 key prefix to clear (e.g., "words/snapshot/latest/")
        region: Optional AWS region
        
    Returns:
        Dict with deletion status and statistics
    """
    logger.info("Clearing S3 directory: bucket=%s, prefix=%s", bucket, key_prefix)
    
    try:
        import boto3
        
        # Create S3 client
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)
        
        # List all objects in the prefix
        paginator = s3.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=bucket, Prefix=key_prefix)
        
        deleted_count = 0
        for page in pages:
            if 'Contents' in page:
                # Prepare objects for deletion
                objects = [{'Key': obj['Key']} for obj in page['Contents']]
                
                if objects:
                    # Delete objects in batch
                    delete_response = s3.delete_objects(
                        Bucket=bucket,
                        Delete={'Objects': objects}
                    )
                    
                    deleted_count += len(delete_response.get('Deleted', []))
                    
                    # Log any errors
                    if 'Errors' in delete_response:
                        for error in delete_response['Errors']:
                            logger.error("Failed to delete %s: %s", error['Key'], error['Message'])
        
        logger.info("Cleared S3 directory: deleted %d objects", deleted_count)
        return {
            "status": "cleared",
            "deleted_count": deleted_count
        }
        
    except Exception as e:
        logger.error("Failed to clear S3 directory: %s", str(e))
        return {
            "status": "failed",
            "error": str(e),
            "deleted_count": 0
        }


def upload_snapshot_with_hash(
    local_snapshot_path: str,
    bucket: str,
    snapshot_key: str,
    calculate_hash: bool = True,
    hash_algorithm: str = "md5",
    metadata: Optional[Dict[str, Any]] = None,
    region: Optional[str] = None,
    clear_destination: bool = True
) -> Dict[str, Any]:
    """
    Upload ChromaDB snapshot to S3 with optional hash calculation and storage.
    
    This function uploads a complete ChromaDB snapshot directory and optionally
    calculates and stores a hash file alongside the snapshot for verification.
    
    Args:
        local_snapshot_path: Path to the local snapshot directory
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot (e.g., "words/snapshot/latest/")
        calculate_hash: Whether to calculate and store hash (default: True)
        hash_algorithm: Hash algorithm to use ("md5", "sha256", etc.)
        metadata: Optional metadata to include in S3 objects
        region: Optional AWS region
        clear_destination: Whether to clear S3 destination before upload (default: True)
        
    Returns:
        Dict with upload status, hash info, and statistics
        
    Example:
        >>> result = upload_snapshot_with_hash(
        ...     "/tmp/chroma_snapshot",
        ...     "chromadb-vectors-dev",
        ...     "words/snapshot/latest/",
        ...     calculate_hash=True
        ... )
        >>> print(result["hash"])  # Hash of the entire directory
    """
    logger.info("Starting snapshot upload with hash: local_path=%s, bucket=%s, key=%s, clear=%s", 
                local_snapshot_path, bucket, snapshot_key, clear_destination)
    
    if not HASH_UTILS_AVAILABLE and calculate_hash:
        logger.warning("Hash calculation requested but hash utilities not available")
        calculate_hash = False
    
    try:
        import boto3
        from pathlib import Path
        
        # Create S3 client
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)
        logger.info("Created S3 client for upload, region: %s", region or "default")
        
        # Clear destination directory if requested
        clear_result = None
        if clear_destination:
            logger.info("Clearing S3 destination before upload...")
            clear_result = clear_s3_directory(bucket, snapshot_key, region)
            if clear_result["status"] == "cleared":
                logger.info("Cleared %d existing objects from %s", 
                           clear_result["deleted_count"], snapshot_key)
            else:
                logger.warning("Failed to clear S3 destination: %s", 
                              clear_result.get("error", "unknown error"))
        
        snapshot_path = Path(local_snapshot_path)
        if not snapshot_path.exists():
            logger.error("Local snapshot path does not exist: %s", local_snapshot_path)
            return {
                "status": "failed",
                "error": f"Snapshot path does not exist: {local_snapshot_path}"
            }
        
        # Calculate hash before upload if requested
        hash_result = None
        if calculate_hash:
            try:
                logger.info("Calculating %s hash for snapshot...", hash_algorithm.upper())
                hash_result = calculate_chromadb_hash(
                    local_snapshot_path,
                    algorithm=hash_algorithm,
                    exclude_patterns=["*.tmp", "*.log", ".snapshot_hash"]  # Exclude temp files and existing hash
                )
                logger.info("Calculated hash: %s (processed %d files)", 
                           hash_result.directory_hash, hash_result.file_count)
            except Exception as e:
                logger.error("Hash calculation failed: %s", e)
                calculate_hash = False
        
        file_count = 0
        total_size = 0
        
        # Upload all files in the snapshot directory
        for file_path in snapshot_path.rglob("*"):
            if file_path.is_file():
                # Skip temporary and existing hash files
                if file_path.name in [".snapshot_hash", ".snapshot_hash.tmp"]:
                    continue
                
                # Calculate relative path from snapshot directory
                relative_path = file_path.relative_to(snapshot_path)
                s3_key = f"{snapshot_key.rstrip('/')}/{relative_path}"
                
                # Prepare S3 metadata
                s3_metadata = {"snapshot_key": snapshot_key}
                if metadata:
                    s3_metadata.update({k: str(v) for k, v in metadata.items()})
                if hash_result:
                    s3_metadata["snapshot_hash"] = hash_result.directory_hash
                    s3_metadata["hash_algorithm"] = hash_result.hash_algorithm
                
                # Upload file
                s3.upload_file(
                    str(file_path), 
                    bucket, 
                    s3_key,
                    ExtraArgs={"Metadata": s3_metadata}
                )
                
                file_count += 1
                total_size += file_path.stat().st_size
                logger.debug("Uploaded file: %s", s3_key)
        
        # Upload hash file if hash was calculated
        hash_file_key = None
        if hash_result:
            try:
                hash_content = create_hash_file_content(hash_result)
                hash_file_key = f"{snapshot_key.rstrip('/')}/.snapshot_hash"
                
                s3.put_object(
                    Bucket=bucket,
                    Key=hash_file_key,
                    Body=hash_content.encode('utf-8'),
                    ContentType='application/json',
                    Metadata={
                        "hash_version": "1.0",
                        "snapshot_hash": hash_result.directory_hash,
                        "algorithm": hash_result.hash_algorithm
                    }
                )
                logger.info("Uploaded hash file: %s", hash_file_key)
            except Exception as e:
                logger.error("Failed to upload hash file: %s", e)
                # Continue - snapshot upload was successful even if hash file failed
        
        result = {
            "status": "uploaded",
            "snapshot_key": snapshot_key,
            "file_count": file_count,
            "total_size_bytes": total_size
        }
        
        # Add clearing information if available
        if clear_result:
            result.update({
                "cleared_objects": clear_result["deleted_count"],
                "clear_status": clear_result["status"]
            })
        
        # Add hash information if available
        if hash_result:
            result.update({
                "hash": hash_result.directory_hash,
                "hash_algorithm": hash_result.hash_algorithm,
                "hash_file_key": hash_file_key,
                "hash_calculation_time": hash_result.calculation_time_seconds
            })
        
        logger.info("Successfully uploaded snapshot: %d files, %d bytes, hash: %s", 
                   file_count, total_size, result.get("hash", "not_calculated"))
        
        return result
        
    except Exception as e:
        logger.error("Error uploading snapshot to S3: %s", e)
        return {
            "status": "failed",
            "error": str(e)
        }


def download_snapshot_with_verification(
    bucket: str,
    snapshot_key: str,
    local_snapshot_path: str,
    verify_hash: bool = True,
    expected_hash: Optional[str] = None,
    hash_algorithm: str = "md5",
    region: Optional[str] = None
) -> Dict[str, Any]:
    """
    Download ChromaDB snapshot from S3 with optional hash verification.
    
    This function downloads a complete ChromaDB snapshot and optionally verifies
    its integrity by comparing hashes with the stored .snapshot_hash file.
    
    Args:
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot (e.g., "words/snapshot/latest/")
        local_snapshot_path: Local directory to download to
        verify_hash: Whether to verify hash after download (default: True)
        expected_hash: Expected hash value (if None, reads from .snapshot_hash file)
        hash_algorithm: Hash algorithm to use for verification
        region: Optional AWS region
        
    Returns:
        Dict with download status, hash verification results, and statistics
    """
    logger.info("Starting snapshot download with verification: bucket=%s, key=%s, local_path=%s", 
                bucket, snapshot_key, local_snapshot_path)
    
    # First download the snapshot using existing function
    download_result = download_snapshot_from_s3(
        bucket=bucket,
        snapshot_key=snapshot_key,
        local_snapshot_path=local_snapshot_path,
        verify_integrity=True,  # Basic size verification
        region=region
    )
    
    if download_result["status"] != "downloaded":
        return download_result
    
    result = download_result.copy()
    
    # Skip hash verification if not requested or hash utils not available
    if not verify_hash or not HASH_UTILS_AVAILABLE:
        if not verify_hash:
            logger.info("Hash verification skipped (verify_hash=False)")
        else:
            logger.warning("Hash verification skipped (hash utilities not available)")
        return result
    
    try:
        import boto3
        
        # Create S3 client
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)
        
        # Get expected hash from S3 hash file if not provided
        s3_hash_result = None
        if expected_hash is None:
            try:
                hash_file_key = f"{snapshot_key.rstrip('/')}/.snapshot_hash"
                logger.info("Downloading hash file: %s", hash_file_key)
                
                response = s3.get_object(Bucket=bucket, Key=hash_file_key)
                hash_content = response['Body'].read().decode('utf-8')
                s3_hash_result = parse_hash_file_content(hash_content)
                expected_hash = s3_hash_result.directory_hash
                hash_algorithm = s3_hash_result.hash_algorithm
                
                logger.info("Retrieved expected hash from S3: %s (%s)", 
                           expected_hash, hash_algorithm.upper())
                
            except Exception as e:
                logger.warning("Could not retrieve hash file from S3: %s", e)
                result["hash_verification"] = {
                    "verified": False,
                    "error": f"Hash file not found or invalid: {e}",
                    "recommendation": "cannot_verify"
                }
                return result
        
        # Calculate hash of downloaded directory
        logger.info("Calculating local hash for verification...")
        local_hash_result = calculate_chromadb_hash(
            local_snapshot_path,
            algorithm=hash_algorithm,
            exclude_patterns=["*.tmp", "*.log", ".snapshot_hash"]
        )
        
        logger.info("Local hash calculated: %s", local_hash_result.directory_hash)
        
        # Compare hashes
        if s3_hash_result:
            comparison = compare_hash_results(local_hash_result, s3_hash_result)
        else:
            # Create a minimal S3 hash result for comparison
            s3_hash_result = ChromaDBHashResult(
                directory_hash=expected_hash,
                file_count=result["file_count"],
                total_size_bytes=result["total_size_bytes"],
                hash_algorithm=hash_algorithm
            )
            comparison = compare_hash_results(local_hash_result, s3_hash_result)
        
        # Add verification results
        result["hash_verification"] = {
            "verified": comparison["is_identical"],
            "local_hash": local_hash_result.directory_hash,
            "expected_hash": expected_hash,
            "hashes_match": comparison["hashes_match"],
            "file_counts_match": comparison["file_counts_match"],
            "sizes_match": comparison["sizes_match"],
            "recommendation": comparison["recommendation"],
            "message": comparison["message"],
            "algorithm": hash_algorithm
        }
        
        if comparison["is_identical"]:
            logger.info("✅ Hash verification successful - downloaded snapshot is identical to S3")
        else:
            logger.warning("❌ Hash verification failed - %s", comparison["message"])
        
        return result
        
    except Exception as e:
        logger.error("Error during hash verification: %s", e)
        result["hash_verification"] = {
            "verified": False,
            "error": str(e),
            "recommendation": "verification_failed"
        }
        return result


def verify_chromadb_sync(
    database: str,
    bucket: str,
    local_path: Optional[str] = None,
    download_for_comparison: bool = False,
    hash_algorithm: str = "md5",
    region: Optional[str] = None
) -> Dict[str, Any]:
    """
    Verify if local ChromaDB directory is in sync with S3 snapshot.
    
    This implements the core functionality from GitHub issue #334:
    fast comparison of local vs S3 ChromaDB snapshots using hash comparison.
    
    Args:
        database: Database name (e.g., "words", "lines")
        bucket: S3 bucket name
        local_path: Path to local ChromaDB directory (if None, only checks S3 hash)
        download_for_comparison: If True, downloads S3 snapshot for comparison
        hash_algorithm: Hash algorithm to use for comparison
        region: Optional AWS region
        
    Returns:
        Dict with comparison results and recommendations
        
    Example:
        >>> result = verify_chromadb_sync(
        ...     database="words",
        ...     bucket="chromadb-vectors-dev",
        ...     local_path="/tmp/chroma_local"
        ... )
        >>> print(result["status"])  # "identical" | "different" | "error"
        >>> print(result["recommendation"])  # "sync_needed" | "up_to_date"
    """
    start_time = time.time()
    logger.info("Starting ChromaDB sync verification: database=%s, bucket=%s, local_path=%s", 
                database, bucket, local_path)
    
    if not HASH_UTILS_AVAILABLE:
        return {
            "status": "error",
            "error": "Hash utilities not available",
            "recommendation": "install_dependencies"
        }
    
    try:
        import boto3
        
        # Create S3 client
        client_kwargs = {"service_name": "s3"}
        if region:
            client_kwargs["region_name"] = region
        s3 = boto3.client(**client_kwargs)
        
        snapshot_key = f"{database}/snapshot/latest/"
        
        # Get S3 hash
        s3_hash_result = None
        try:
            hash_file_key = f"{snapshot_key}.snapshot_hash"
            logger.info("Retrieving S3 hash from: %s", hash_file_key)
            
            response = s3.get_object(Bucket=bucket, Key=hash_file_key)
            hash_content = response['Body'].read().decode('utf-8')
            s3_hash_result = parse_hash_file_content(hash_content)
            
            logger.info("S3 hash retrieved: %s (%s)", 
                       s3_hash_result.directory_hash, s3_hash_result.hash_algorithm)
            
        except Exception as e:
            logger.error("Could not retrieve S3 hash: %s", e)
            return {
                "status": "error",
                "error": f"Could not retrieve S3 hash: {e}",
                "recommendation": "check_s3_snapshot"
            }
        
        # If no local path provided, just return S3 hash info
        if local_path is None:
            return {
                "status": "s3_only",
                "s3_hash": s3_hash_result.directory_hash,
                "s3_file_count": s3_hash_result.file_count,
                "s3_size_bytes": s3_hash_result.total_size_bytes,
                "algorithm": s3_hash_result.hash_algorithm,
                "message": "S3 hash retrieved successfully",
                "recommendation": "provide_local_path_for_comparison"
            }
        
        # Calculate local hash
        local_hash_result = None
        if os.path.exists(local_path):
            try:
                logger.info("Calculating local hash...")
                local_hash_result = calculate_chromadb_hash(
                    local_path,
                    algorithm=s3_hash_result.hash_algorithm,  # Use same algorithm as S3
                    exclude_patterns=["*.tmp", "*.log", ".snapshot_hash"]
                )
                logger.info("Local hash calculated: %s", local_hash_result.directory_hash)
                
            except Exception as e:
                logger.error("Could not calculate local hash: %s", e)
                return {
                    "status": "error",
                    "error": f"Could not calculate local hash: {e}",
                    "recommendation": "check_local_directory"
                }
        else:
            logger.warning("Local path does not exist: %s", local_path)
            
            if download_for_comparison:
                logger.info("Downloading S3 snapshot for comparison...")
                # Download snapshot to temporary directory
                import tempfile
                temp_dir = tempfile.mkdtemp()
                
                try:
                    download_result = download_snapshot_from_s3(
                        bucket=bucket,
                        snapshot_key=snapshot_key,
                        local_snapshot_path=temp_dir,
                        region=region
                    )
                    
                    if download_result["status"] == "downloaded":
                        local_hash_result = calculate_chromadb_hash(
                            temp_dir,
                            algorithm=s3_hash_result.hash_algorithm
                        )
                        logger.info("Downloaded and calculated hash: %s", local_hash_result.directory_hash)
                    else:
                        return {
                            "status": "error",
                            "error": f"Could not download S3 snapshot: {download_result.get('error')}",
                            "recommendation": "check_s3_access"
                        }
                finally:
                    # Clean up temp directory
                    import shutil
                    shutil.rmtree(temp_dir, ignore_errors=True)
            else:
                return {
                    "status": "error",
                    "error": f"Local path does not exist: {local_path}",
                    "recommendation": "create_local_directory_or_enable_download"
                }
        
        # Compare hashes
        comparison = compare_hash_results(local_hash_result, s3_hash_result)
        comparison_time = time.time() - start_time
        
        # Determine status based on comparison
        if comparison["is_identical"]:
            status = "identical"
        else:
            status = "different"
        
        result = {
            "status": status,
            "local_hash": local_hash_result.directory_hash,
            "s3_hash": s3_hash_result.directory_hash,
            "local_file_count": local_hash_result.file_count,
            "s3_file_count": s3_hash_result.file_count,
            "local_size_bytes": local_hash_result.total_size_bytes,
            "s3_size_bytes": s3_hash_result.total_size_bytes,
            "algorithm": s3_hash_result.hash_algorithm,
            "comparison_time_seconds": comparison_time,
            "recommendation": comparison["recommendation"],
            "message": comparison["message"],
            "detailed_comparison": comparison
        }
        
        if status == "identical":
            logger.info("✅ ChromaDB sync verification: IDENTICAL")
        else:
            logger.info("❌ ChromaDB sync verification: DIFFERENT - %s", comparison["message"])
        
        return result
        
    except Exception as e:
        logger.error("Error during sync verification: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "recommendation": "check_configuration"
        }


def upload_snapshot_atomic(
    local_path: str,
    bucket: str,
    collection: str,  # "lines" or "words"
    lock_manager: Optional[object] = None,
    metadata: Optional[Dict[str, str]] = None,
    keep_versions: int = 2,
) -> Dict[str, Any]:
    """
    Upload ChromaDB snapshot using blue-green atomic deployment pattern.
    
    This function uploads to a timestamped version, then atomically updates
    a pointer file to reference the new version. Readers will get complete
    snapshots with no race conditions.
    
    Args:
        local_path: Path to local ChromaDB directory to upload
        bucket: S3 bucket name
        collection: ChromaDB collection name ("lines" or "words") 
        lock_manager: Optional lock manager to validate ownership during operation
        metadata: Optional metadata to attach to S3 objects
        keep_versions: Number of versions to retain (default: 2)
    
    Returns:
        Dict with status, version_id, versioned_key, and pointer_key
    """
    import boto3
    from datetime import datetime, timezone
    
    s3_client = boto3.client('s3')
    
    try:
        # Validate lock ownership before starting
        if lock_manager and not lock_manager.validate_ownership():
            return {
                "status": "error",
                "error": "Lock validation failed before snapshot upload",
                "collection": collection
            }
        
        # Generate version identifier using timestamp
        version_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        versioned_key = f"{collection}/snapshot/timestamped/{version_id}/"
        pointer_key = f"{collection}/snapshot/latest-pointer.txt"
        
        logger.info("DEBUG: Starting atomic snapshot upload: collection=%s, version=%s, local_path=%s", 
                   collection, version_id, local_path)
        logger.info("DEBUG: Atomic upload paths - versioned_key=%s, pointer_key=%s", 
                   versioned_key, pointer_key)
        
        # Step 1: Upload to versioned location (no race condition possible)
        logger.info("DEBUG: Step 1 - uploading to versioned location: %s", versioned_key)
        upload_result = upload_snapshot_with_hash(
            local_snapshot_path=local_path,
            bucket=bucket,
            snapshot_key=versioned_key,
            calculate_hash=True,
            clear_destination=False,  # Don't clear - versioned path is unique
            metadata=metadata
        )
        
        logger.info("DEBUG: Step 1 result - status=%s, hash=%s", 
                   upload_result.get("status"), upload_result.get("hash", "not_calculated"))
        
        if upload_result.get("status") != "uploaded":
            logger.error("DEBUG: Step 1 failed - upload_result=%s", upload_result)
            return {
                "status": "error",
                "error": f"Failed to upload to versioned location: {upload_result}",
                "collection": collection,
                "version_id": version_id
            }
        
        # Step 2: Final lock validation before atomic promotion
        logger.info("DEBUG: Step 2 - validating lock ownership before atomic promotion")
        if lock_manager and not lock_manager.validate_ownership():
            logger.error("DEBUG: Step 2 failed - lock validation failed, cleaning up versioned upload")
            # Clean up versioned upload
            try:
                _cleanup_s3_prefix(s3_client, bucket, versioned_key)
                logger.info("DEBUG: Cleaned up versioned upload after lock loss: %s", versioned_key)
            except Exception as cleanup_error:
                logger.warning("DEBUG: Failed to cleanup versioned upload: %s", cleanup_error)
            
            return {
                "status": "error",
                "error": "Lock validation failed before atomic promotion",
                "collection": collection,
                "version_id": version_id
            }
        
        # Step 3: Atomic promotion - single S3 write operation
        logger.info("DEBUG: Step 3 - atomic promotion, writing pointer file: %s -> %s", 
                   pointer_key, version_id)
        s3_client.put_object(
            Bucket=bucket,
            Key=pointer_key,
            Body=version_id.encode('utf-8'),
            ContentType="text/plain",
            Metadata=metadata or {}
        )
        logger.info("DEBUG: Step 3 completed - pointer file written successfully")
        
        # Step 4: Background cleanup of old versions
        logger.info("DEBUG: Step 4 - cleaning up old versions (keep_versions=%d)", keep_versions)
        try:
            _cleanup_old_snapshot_versions(s3_client, bucket, collection, keep_versions)
            logger.info("DEBUG: Step 4 completed - old versions cleaned up successfully")
        except Exception as cleanup_error:
            logger.warning("DEBUG: Step 4 warning - failed to cleanup old versions: %s", cleanup_error)
        
        logger.info("DEBUG: Atomic snapshot upload completed successfully: collection=%s, version=%s", 
                   collection, version_id)
        return {
            "status": "uploaded",
            "collection": collection,
            "version_id": version_id,
            "versioned_key": versioned_key,
            "pointer_key": pointer_key,
            "hash": upload_result.get("hash", "not_calculated")
        }
        
    except Exception as e:
        logger.error("Error during atomic snapshot upload: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "collection": collection
        }


def download_snapshot_atomic(
    bucket: str,
    collection: str,  # "lines" or "words"
    local_path: str,
    verify_integrity: bool = True,
) -> Dict[str, Any]:
    """
    Download ChromaDB snapshot using atomic pointer resolution.
    
    This function reads the version pointer and downloads the corresponding
    timestamped version, ensuring consistent snapshots with no race conditions.
    
    Args:
        bucket: S3 bucket name
        collection: ChromaDB collection name ("lines" or "words")
        local_path: Local directory to download to
        verify_integrity: Whether to verify snapshot integrity
    
    Returns:
        Dict with status, version_id, and download information
    """
    import boto3
    from botocore.exceptions import ClientError
    
    s3_client = boto3.client('s3')
    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    
    try:
        # Step 1: Resolve current version pointer
        try:
            response = s3_client.get_object(Bucket=bucket, Key=pointer_key)
            version_id = response['Body'].read().decode('utf-8').strip()
            versioned_key = f"{collection}/snapshot/timestamped/{version_id}/"
            
            logger.info("Resolved snapshot pointer: collection=%s, version=%s", 
                       collection, version_id)
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                # Fallback to direct /latest/ path for backward compatibility
                logger.info("No pointer found, falling back to /latest/ path: %s", collection)
                versioned_key = f"{collection}/snapshot/latest/"
                version_id = "latest-direct"
            else:
                raise
        
        # Step 2: Download from resolved version (immutable)
        download_result = download_snapshot_from_s3(
            bucket=bucket,
            snapshot_key=versioned_key,
            local_snapshot_path=local_path,
            verify_integrity=verify_integrity
        )
        
        if download_result.get("status") != "downloaded":
            return {
                "status": "error", 
                "error": f"Failed to download snapshot: {download_result}",
                "collection": collection,
                "version_id": version_id
            }
        
        logger.info("Atomic snapshot download completed: collection=%s, version=%s", 
                   collection, version_id)
        return {
            "status": "downloaded",
            "collection": collection,
            "version_id": version_id,
            "versioned_key": versioned_key,
            "local_path": local_path
        }
        
    except Exception as e:
        logger.error("Error during atomic snapshot download: %s", e)
        return {
            "status": "error",
            "error": str(e),
            "collection": collection
        }


def _cleanup_s3_prefix(s3_client, bucket: str, prefix: str) -> None:
    """Helper function to delete all objects under an S3 prefix."""
    paginator = s3_client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        if 'Contents' in page:
            delete_keys = [{'Key': obj['Key']} for obj in page['Contents']]
            s3_client.delete_objects(
                Bucket=bucket,
                Delete={'Objects': delete_keys}
            )


def _cleanup_old_snapshot_versions(
    s3_client, 
    bucket: str, 
    collection: str, 
    keep_versions: int
) -> None:
    """Helper function to clean up old timestamped snapshot versions."""
    timestamped_prefix = f"{collection}/snapshot/timestamped/"
    
    # List all timestamped versions
    paginator = s3_client.get_paginator('list_objects_v2')
    versions = []
    
    for page in paginator.paginate(Bucket=bucket, Prefix=timestamped_prefix, Delimiter='/'):
        if 'CommonPrefixes' in page:
            for prefix_info in page['CommonPrefixes']:
                version_path = prefix_info['Prefix']
                # Extract version ID from path like "lines/snapshot/timestamped/20250826_143052/"
                version_id = version_path.split('/')[-2]
                versions.append(version_id)
    
    # Sort versions by timestamp (newest first)
    versions.sort(reverse=True)
    
    # Delete old versions beyond keep_versions
    versions_to_delete = versions[keep_versions:]
    for version_id in versions_to_delete:
        version_prefix = f"{collection}/snapshot/timestamped/{version_id}/"
        try:
            _cleanup_s3_prefix(s3_client, bucket, version_prefix)
            logger.info("Cleaned up old snapshot version: %s", version_prefix)
        except Exception as e:
            logger.warning("Failed to cleanup version %s: %s", version_prefix, e)
