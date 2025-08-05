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

from .chroma_client import ChromaDBClient

logger = logging.getLogger(__name__)


def produce_embedding_delta(
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    collection_name: str = "words",
    bucket_name: Optional[str] = None,
    sqs_queue_url: Optional[str] = None,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a ChromaDB delta and send to SQS for compaction.

    This is the standard pattern for producer lambdas that generate embeddings.

    Args:
        ids: Vector IDs
        embeddings: Embedding vectors
        documents: Document texts
        metadatas: Metadata dictionaries
        collection_name: ChromaDB collection name (default: "words")
        bucket_name: S3 bucket (uses VECTORS_BUCKET env var if not provided)
        sqs_queue_url: SQS queue URL (uses COMPACTION_QUEUE_URL or 
            DELTA_QUEUE_URL env var if not provided)
        batch_id: Optional batch identifier for tracking purposes

    Returns:
        Dict with status and delta_key

    Example:
        >>> result = produce_embedding_delta(
        ...     ids=["WORD#1", "WORD#2"],
        ...     embeddings=[[0.1, 0.2, ...], [0.3, 0.4, ...]],
        ...     documents=["hello", "world"],
        ...     metadatas=[{"pos": 1}, {"pos": 2}]
        ... )
        >>> print(result["delta_key"])
        "delta/a1b2c3d4e5f6/"
    """
    if bucket_name is None:
        bucket_name = os.environ["VECTORS_BUCKET"]

    if sqs_queue_url is None:
        # Try COMPACTION_QUEUE_URL first (preferred), then DELTA_QUEUE_URL (legacy)
        sqs_queue_url = os.environ.get("COMPACTION_QUEUE_URL") or os.environ.get("DELTA_QUEUE_URL")

    # Create temporary directory for delta
    with tempfile.TemporaryDirectory() as temp_dir:
        delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"

        # Create ChromaDB client in delta mode
        chroma = ChromaDBClient(persist_directory=delta_dir, mode="delta")

        # Upsert vectors
        chroma.upsert_vectors(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        # Upload to S3
        s3_key = chroma.persist_and_upload_delta(
            bucket=bucket_name, s3_prefix="delta/"
        )

        logger.info("Uploaded delta to S3: %s", s3_key)

    # Send to SQS if queue URL is provided and not empty
    if sqs_queue_url:
        try:
            sqs = boto3.client("sqs")

            message_body = {
                "delta_key": s3_key,
                "collection": collection_name,
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

    return {
        "status": "success",
        "delta_key": s3_key,
        "delta_id": s3_key.split('/')[-2],  # Extract delta ID from path
        "embedding_count": len(ids),
        "vectors_uploaded": len(ids),  # Keep for backward compatibility
        "batch_id": batch_id,
    }


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
