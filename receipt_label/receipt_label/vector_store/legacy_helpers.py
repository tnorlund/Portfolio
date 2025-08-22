"""
Legacy helper functions for vector store operations.

This module provides backward compatibility functions that match the API
of the old chroma_s3_helpers module but use the new vector_store architecture.
"""

import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3

from .client.factory import VectorClient
from .storage.s3_operations import S3Operations
from .storage.hash_calculator import HashCalculator

logger = logging.getLogger(__name__)


def produce_embedding_delta(
    ids: List[str],
    embeddings: List[List[float]],
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    bucket_name: str,
    collection_name: str = "words",
    database_name: Optional[str] = None,
    sqs_queue_url: Optional[str] = None,
    batch_id: Optional[str] = None,
    delta_prefix: str = "delta/",
    local_temp_dir: Optional[str] = None,
    compress: bool = False,
) -> Dict[str, Any]:
    """
    Create a ChromaDB delta and send to SQS for compaction.
    
    This function provides backward compatibility with the old chroma_s3_helpers
    module while using the new vector_store architecture.
    
    Args:
        ids: Vector IDs
        embeddings: Embedding vectors
        documents: Document texts
        metadatas: Metadata dictionaries
        bucket_name: S3 bucket name for storing the delta
        collection_name: ChromaDB collection name (default: "words")
        database_name: Database name for separation (e.g., "lines", "words")
        sqs_queue_url: SQS queue URL for compaction notification. If None, skips SQS
        batch_id: Optional batch identifier for tracking purposes
        delta_prefix: S3 prefix for delta files (default: "delta/")
        local_temp_dir: Optional local directory for temporary files
        compress: Whether to compress the delta (default: False, ignored)
        
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
        temp_dir = local_temp_dir
        delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"
        os.makedirs(delta_dir, exist_ok=True)
        cleanup_temp = False
    else:
        temp_dir = tempfile.mkdtemp()
        delta_dir = f"{temp_dir}/chroma_delta_{uuid.uuid4().hex}"
        os.makedirs(delta_dir, exist_ok=True)
        cleanup_temp = True
    
    try:
        logger.info("Delta directory created at: %s", delta_dir)
        
        # Create ChromaDB client using new vector_store module
        client = VectorClient.create_chromadb_client(
            persist_directory=delta_dir,
            mode="delta"
        )
        
        # Upsert vectors using the new interface
        logger.info("Upserting %d vectors to collection '%s'", len(ids), collection_name)
        client.upsert_vectors(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info("Successfully upserted vectors to collection '%s'", collection_name)

        # Upload to S3 using new S3Operations
        s3_ops = S3Operations(bucket_name)
        
        # Adjust delta prefix to include database name if provided
        if database_name:
            full_delta_prefix = f"{database_name}/{delta_prefix}"
            logger.info("S3 delta prefix will be: %s", full_delta_prefix)
        else:
            full_delta_prefix = delta_prefix
            
        try:
            logger.info("Starting S3 upload to bucket '%s' with prefix '%s'", 
                       bucket_name, full_delta_prefix)
            s3_key = s3_ops.upload_delta(
                local_directory=delta_dir,
                delta_prefix=full_delta_prefix,
                collection_name=collection_name,
                database_name=database_name
            )
            logger.info("Successfully uploaded delta to S3: %s", s3_key)
        except Exception as e:
            logger.error("Failed to upload delta to S3: %s", e)
            logger.error("Delta directory was: %s", delta_dir)
            raise

        # Send to SQS if queue URL is provided
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
                    file_path = os.path.join(root, file)
                    if os.path.isfile(file_path):
                        delta_size += os.path.getsize(file_path)

        logger.info("Delta creation completed. Size: %d bytes", delta_size)

        return {
            "status": "success",
            "delta_key": s3_key,
            "delta_id": s3_key.split('/')[-2] if '/' in s3_key else s3_key,
            "embedding_count": len(ids),
            "collection": collection_name,
            "database": database_name,
            "delta_size_bytes": delta_size,
        }

    finally:
        # Clean up temporary directory if we created it
        if cleanup_temp and os.path.exists(temp_dir):
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


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
    
    This function provides backward compatibility with the old chroma_s3_helpers
    module while using the new vector_store architecture.
    
    Args:
        local_snapshot_path: Path to the local snapshot directory
        bucket: S3 bucket name
        snapshot_key: S3 key prefix for the snapshot (e.g., "words/snapshot/latest/")
        calculate_hash: Whether to calculate and store hash (default: True)
        hash_algorithm: Hash algorithm to use ("md5", "sha256", etc.)
        metadata: Optional metadata to include in S3 objects
        region: Optional AWS region (ignored for compatibility)
        clear_destination: Whether to clear S3 destination before upload (default: True)
        
    Returns:
        Dict with upload status, hash info, and statistics
        
    Example:
        >>> result = upload_snapshot_with_hash(
        ...     local_snapshot_path="/tmp/chroma_snapshot",
        ...     bucket="my-vectors-bucket",
        ...     snapshot_key="words/snapshot/latest/"
        ... )
        >>> print(result["status"])
        "uploaded"
    """
    if not os.path.exists(local_snapshot_path):
        raise RuntimeError(f"Local snapshot path does not exist: {local_snapshot_path}")

    if not os.path.isdir(local_snapshot_path):
        raise RuntimeError(f"Path is not a directory: {local_snapshot_path}")

    logger.info("Uploading snapshot from %s to s3://%s/%s", 
               local_snapshot_path, bucket, snapshot_key)

    # Use S3Operations from the new module
    s3_ops = S3Operations(bucket)
    
    # Calculate hash if requested
    hash_result = None
    if calculate_hash:
        logger.info("Calculating %s hash for snapshot", hash_algorithm.upper())
        hash_result = HashCalculator.calculate_directory_hash(
            local_snapshot_path, 
            algorithm=hash_algorithm,
            include_file_list=False
        )
        logger.info("Calculated %s hash: %s", hash_algorithm.upper(), hash_result.directory_hash)

    # Clear destination if requested (optional feature in new module)
    if clear_destination:
        try:
            # List objects with the snapshot key prefix and delete them
            s3_client = s3_ops.s3_client
            response = s3_client.list_objects_v2(Bucket=bucket, Prefix=snapshot_key)
            
            if 'Contents' in response:
                objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]
                if objects_to_delete:
                    s3_client.delete_objects(
                        Bucket=bucket,
                        Delete={'Objects': objects_to_delete}
                    )
                    logger.info("Cleared %d existing objects at %s", len(objects_to_delete), snapshot_key)
        except Exception as e:
            logger.warning("Could not clear destination: %s", e)

    # Upload the snapshot directory
    try:
        from pathlib import Path
        
        # Find all files to upload
        local_path = Path(local_snapshot_path)
        files_to_upload = list(local_path.rglob("*"))
        files_to_upload = [f for f in files_to_upload if f.is_file()]

        if not files_to_upload:
            raise RuntimeError(f"No files found to upload in {local_snapshot_path}")

        # Upload all files
        uploaded_count = 0
        total_size = 0
        
        for file_path in files_to_upload:
            try:
                relative_path = file_path.relative_to(local_path)
                s3_key = f"{snapshot_key.rstrip('/')}/{relative_path}"
                
                logger.debug("Uploading %s to s3://%s/%s", file_path, bucket, s3_key)
                
                # Add metadata if provided
                extra_args = {}
                if metadata:
                    extra_args['Metadata'] = {k: str(v) for k, v in metadata.items()}
                
                s3_ops.s3_client.upload_file(str(file_path), bucket, s3_key, ExtraArgs=extra_args)
                uploaded_count += 1
                total_size += file_path.stat().st_size
                
            except Exception as e:
                logger.error("Failed to upload %s to S3: %s", file_path, e)
                raise RuntimeError(f"S3 upload failed for {file_path}: {e}")

        # Upload hash file if calculated
        if hash_result:
            hash_metadata = HashCalculator.create_hash_metadata(hash_result)
            hash_key = f"{snapshot_key.rstrip('/')}/.snapshot_hash"
            
            s3_ops.s3_client.put_object(
                Bucket=bucket,
                Key=hash_key,
                Body=hash_metadata.encode('utf-8'),
                ContentType='application/json',
                Metadata=metadata or {}
            )
            logger.info("Uploaded hash metadata to %s", hash_key)
            uploaded_count += 1

        logger.info("Successfully uploaded %d files (%d bytes) to S3 at %s", 
                   uploaded_count, total_size, snapshot_key)

        return {
            "status": "uploaded",
            "snapshot_key": snapshot_key,
            "file_count": len(files_to_upload),
            "total_size_bytes": total_size,
            "hash": hash_result.directory_hash if hash_result else None,
            "hash_algorithm": hash_algorithm if hash_result else None,
            "uploaded_files": uploaded_count,
        }

    except Exception as e:
        logger.error("Failed to upload snapshot: %s", e)
        raise RuntimeError(f"Snapshot upload failed: {e}")