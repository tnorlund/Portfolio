"""
Refactored ChromaDB compaction handler with improved modularity and best practices.

This module demonstrates how the handler could be restructured for better
maintainability while keeping the same functionality.
"""

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from logging import INFO, Formatter, StreamHandler, getLogger
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import boto3
import chromadb
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.compaction_lock import CompactionLock

# Constants
CHUNK_SIZE = 10
HEARTBEAT_INTERVAL = 60  # seconds
LOCK_DURATION = 5  # minutes
DEFAULT_COLLECTION_NAME = "receipt_words"

# Configure logging
logger = getLogger()
logger.setLevel(INFO)

if not logger.handlers:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)


@dataclass
class CompactionConfig:
    """Configuration for compaction operations."""
    bucket_name: str
    table_name: str
    collection_name: str = DEFAULT_COLLECTION_NAME
    chunk_size: int = CHUNK_SIZE
    delete_processed_deltas: bool = False
    delete_intermediate_chunks: bool = True


class LockManager:
    """Manages distributed locks with heartbeat support."""
    
    def __init__(self, dynamo_client: DynamoClient):
        self.dynamo_client = dynamo_client
        self.lock_id: Optional[str] = None
        self.lock_owner: Optional[str] = None
        self.heartbeat_thread: Optional[threading.Thread] = None
        self.stop_heartbeat = threading.Event()
    
    def acquire(self, lock_id: str = "chromadb_compaction_lock") -> bool:
        """Acquire a distributed lock."""
        owner = str(uuid.uuid4())
        
        try:
            lock = CompactionLock(
                lock_id=lock_id,
                owner=owner,
                expires=datetime.utcnow() + timedelta(minutes=LOCK_DURATION),
                heartbeat=datetime.utcnow(),
            )
            
            self.dynamo_client.add_compaction_lock(lock)
            logger.info("Acquired lock: %s with owner %s", lock_id, owner)
            
            self.lock_id = lock_id
            self.lock_owner = owner
            return True
            
        except Exception as e:
            logger.info("Failed to acquire lock: %s - %s", lock_id, str(e))
            return False
    
    def release(self) -> None:
        """Release the distributed lock."""
        if not self.lock_id or not self.lock_owner:
            logger.warning("No lock to release")
            return
        
        try:
            self.dynamo_client.delete_compaction_lock(self.lock_id, self.lock_owner)
            logger.info("Released lock: %s", self.lock_id)
        except Exception as e:
            logger.error("Error releasing lock: %s", str(e))
        finally:
            self.lock_id = None
            self.lock_owner = None
    
    def start_heartbeat(self) -> None:
        """Start heartbeat thread to keep lock alive."""
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            logger.warning("Heartbeat thread already running")
            return
        
        self.stop_heartbeat.clear()
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_worker, 
            daemon=True
        )
        self.heartbeat_thread.start()
        logger.info("Started heartbeat thread")
    
    def stop_heartbeat(self) -> None:
        """Stop the heartbeat thread."""
        if not self.heartbeat_thread:
            return
        
        logger.info("Stopping heartbeat thread")
        self.stop_heartbeat.set()
        self.heartbeat_thread.join(timeout=2)
        
        if self.heartbeat_thread.is_alive():
            logger.warning("Heartbeat thread did not stop gracefully")
        else:
            logger.info("Heartbeat thread stopped successfully")
        
        self.heartbeat_thread = None
    
    def _heartbeat_worker(self) -> None:
        """Worker thread that updates lock heartbeat periodically."""
        logger.info("Starting heartbeat worker")
        
        while not self.stop_heartbeat.is_set():
            try:
                if self.lock_id and self.lock_owner:
                    updated_lock = CompactionLock(
                        lock_id=self.lock_id,
                        owner=self.lock_owner,
                        expires=datetime.utcnow() + timedelta(minutes=LOCK_DURATION),
                        heartbeat=datetime.utcnow(),
                    )
                    self.dynamo_client.update_compaction_lock(updated_lock)
                    logger.info("Updated heartbeat for lock %s", self.lock_id)
            except Exception as e:
                logger.error("Failed to update heartbeat: %s", str(e))
            
            self.stop_heartbeat.wait(HEARTBEAT_INTERVAL)
        
        logger.info("Heartbeat worker stopped")


class S3Helper:
    """Helper class for S3 operations."""
    
    def __init__(self, bucket_name: str):
        self.bucket_name = bucket_name
        self.s3_client = boto3.client("s3")
    
    def download_prefix(self, prefix: str, local_dir: Path) -> bool:
        """Download all objects with given prefix to local directory."""
        local_dir.mkdir(parents=True, exist_ok=True)
        
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
        
        has_data = False
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                local_path = local_dir / os.path.relpath(key, prefix)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                
                self.s3_client.download_file(
                    self.bucket_name, 
                    key, 
                    str(local_path)
                )
                has_data = True
        
        return has_data
    
    def upload_directory(self, local_dir: Path, prefix: str) -> None:
        """Upload all files from local directory to S3 prefix."""
        for file_path in local_dir.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(local_dir)
                s3_key = f"{prefix}{relative_path}"
                
                self.s3_client.upload_file(
                    str(file_path), 
                    self.bucket_name, 
                    s3_key
                )
    
    def delete_prefix(self, prefix: str) -> None:
        """Delete all objects with given prefix."""
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
        
        for page in pages:
            if "Contents" in page:
                objects = [{"Key": obj["Key"]} for obj in page["Contents"]]
                self.s3_client.delete_objects(
                    Bucket=self.bucket_name, 
                    Delete={"Objects": objects}
                )
                logger.info("Deleted objects with prefix: %s", prefix)


class ChromaDBCompactor:
    """Handles ChromaDB compaction operations."""
    
    def __init__(self, config: CompactionConfig, s3_helper: S3Helper):
        self.config = config
        self.s3_helper = s3_helper
    
    def compact_deltas_to_collection(
        self, 
        delta_keys: List[str], 
        target_collection: chromadb.Collection,
        temp_dir: Path
    ) -> int:
        """Compact multiple deltas into a target collection."""
        total_embeddings = 0
        
        for i, delta_key in enumerate(delta_keys):
            logger.info("Processing delta %d/%d: %s", i+1, len(delta_keys), delta_key)
            
            delta_dir = temp_dir / "deltas" / Path(delta_key).name
            
            if not self.s3_helper.download_prefix(delta_key, delta_dir):
                logger.warning("No data found for delta: %s", delta_key)
                continue
            
            # Load delta ChromaDB
            delta_client = chromadb.PersistentClient(path=str(delta_dir))
            delta_collection = delta_client.get_collection(self.config.collection_name)
            
            # Get all data from delta
            delta_data = delta_collection.get(
                include=["embeddings", "documents", "metadatas"]
            )
            
            if delta_data["ids"]:
                count = len(delta_data["ids"])
                target_collection.add(
                    ids=delta_data["ids"],
                    embeddings=delta_data["embeddings"],
                    documents=delta_data["documents"],
                    metadatas=delta_data["metadatas"],
                )
                total_embeddings += count
                logger.info("Added %d embeddings from delta", count)
        
        return total_embeddings


# Handler functions using the refactored classes

def compact_handler(event: Dict, context) -> Dict:
    """Main handler routing to appropriate operation."""
    logger.info("Starting ChromaDB compaction handler")
    logger.info("Event: %s", json.dumps(event))
    
    operation = event.get("operation")
    
    if operation == "process_chunk":
        return process_chunk_handler(event)
    elif operation == "final_merge":
        return final_merge_handler(event)
    else:
        return legacy_compact_handler(event)


def process_chunk_handler(event: Dict) -> Dict:
    """Process a chunk of deltas without acquiring locks."""
    batch_id = event.get("batch_id")
    chunk_index = event.get("chunk_index", 0)
    delta_results = event.get("delta_results", [])
    
    if not batch_id:
        return {"statusCode": 400, "error": "batch_id is required"}
    
    # Setup configuration
    config = CompactionConfig(
        bucket_name=os.environ["CHROMADB_BUCKET"],
        table_name=os.environ["DYNAMODB_TABLE_NAME"],
    )
    s3_helper = S3Helper(config.bucket_name)
    compactor = ChromaDBCompactor(config, s3_helper)
    
    # Process chunk
    chunk_deltas = delta_results[:config.chunk_size]
    remaining_deltas = delta_results[config.chunk_size:]
    
    if not chunk_deltas:
        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "chunk_index": chunk_index,
            "embeddings_processed": 0,
            "has_more_chunks": False,
        }
    
    start_time = time.time()
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create chunk collection
        chunk_client = chromadb.PersistentClient(
            path=str(temp_path / "chunk")
        )
        chunk_collection = chunk_client.get_or_create_collection(
            name=config.collection_name,
            metadata={
                "batch_id": batch_id,
                "chunk_index": chunk_index,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        
        # Compact deltas into chunk
        delta_keys = [r["delta_key"] for r in chunk_deltas]
        total_embeddings = compactor.compact_deltas_to_collection(
            delta_keys, chunk_collection, temp_path
        )
        
        # Upload chunk to intermediate location
        intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
        s3_helper.upload_directory(
            temp_path / "chunk", 
            intermediate_key
        )
    
    elapsed_time = time.time() - start_time
    
    return {
        "statusCode": 200,
        "batch_id": batch_id,
        "chunk_index": chunk_index,
        "intermediate_key": intermediate_key,
        "embeddings_processed": total_embeddings,
        "processing_time_seconds": elapsed_time,
        "has_more_chunks": len(remaining_deltas) > 0,
        "next_chunk_index": chunk_index + 1 if remaining_deltas else None,
        "remaining_deltas": remaining_deltas,
    }


def final_merge_handler(event: Dict) -> Dict:
    """Final merge with lock protection."""
    batch_id = event.get("batch_id")
    total_chunks = event.get("total_chunks", 0)
    
    if not batch_id:
        return {"statusCode": 400, "error": "batch_id is required"}
    
    # Setup
    config = CompactionConfig(
        bucket_name=os.environ["CHROMADB_BUCKET"],
        table_name=os.environ["DYNAMODB_TABLE_NAME"],
        delete_intermediate_chunks=os.environ.get(
            "DELETE_INTERMEDIATE_CHUNKS", "true"
        ).lower() == "true",
    )
    
    dynamo_client = DynamoClient(config.table_name)
    lock_manager = LockManager(dynamo_client)
    s3_helper = S3Helper(config.bucket_name)
    
    # Acquire lock
    if not lock_manager.acquire():
        return {
            "statusCode": 423,
            "message": "Final merge blocked - compaction already in progress",
        }
    
    try:
        lock_manager.start_heartbeat()
        
        # Perform merge
        result = merge_chunks_with_lock(
            batch_id, total_chunks, config, s3_helper
        )
        
        return {
            "statusCode": 200,
            **result,
            "message": "Final merge completed successfully",
        }
        
    except Exception as e:
        logger.error("Final merge failed: %s", str(e))
        return {
            "statusCode": 500,
            "error": str(e),
            "message": "Final merge failed",
        }
    finally:
        lock_manager.stop_heartbeat()
        lock_manager.release()


def merge_chunks_with_lock(
    batch_id: str, 
    total_chunks: int,
    config: CompactionConfig,
    s3_helper: S3Helper
) -> Dict:
    """Merge intermediate chunks into final snapshot."""
    start_time = time.time()
    snapshot_id = str(uuid.uuid4())
    total_embeddings = 0
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create final collection
        main_client = chromadb.PersistentClient(
            path=str(temp_path / "main")
        )
        main_collection = main_client.get_or_create_collection(
            name=config.collection_name,
            metadata={
                "batch_id": batch_id,
                "snapshot_id": snapshot_id,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        
        # Merge all chunks
        compactor = ChromaDBCompactor(config, s3_helper)
        
        for chunk_index in range(total_chunks):
            intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
            logger.info("Merging chunk %d/%d", chunk_index + 1, total_chunks)
            
            chunk_dir = temp_path / "chunks" / f"chunk-{chunk_index}"
            
            if not s3_helper.download_prefix(intermediate_key, chunk_dir):
                logger.warning("No data for chunk %d", chunk_index)
                continue
            
            # Load and merge chunk
            chunk_client = chromadb.PersistentClient(path=str(chunk_dir))
            chunk_collection = chunk_client.get_collection(config.collection_name)
            
            chunk_data = chunk_collection.get(
                include=["embeddings", "documents", "metadatas"]
            )
            
            if chunk_data["ids"]:
                count = len(chunk_data["ids"])
                main_collection.add(
                    ids=chunk_data["ids"],
                    embeddings=chunk_data["embeddings"],
                    documents=chunk_data["documents"],
                    metadatas=chunk_data["metadatas"],
                )
                total_embeddings += count
                logger.info("Merged %d embeddings from chunk %d", count, chunk_index)
        
        # Upload final snapshot
        snapshot_key = f"snapshot/{snapshot_id}/"
        s3_helper.upload_directory(temp_path / "main", snapshot_key)
        
        # Cleanup intermediate chunks if configured
        if config.delete_intermediate_chunks:
            for chunk_index in range(total_chunks):
                intermediate_key = f"intermediate/{batch_id}/chunk-{chunk_index}/"
                s3_helper.delete_prefix(intermediate_key)
    
    elapsed_time = time.time() - start_time
    
    return {
        "snapshot_id": snapshot_id,
        "snapshot_key": snapshot_key,
        "total_embeddings": total_embeddings,
        "chunks_merged": total_chunks,
        "processing_time_seconds": elapsed_time,
    }


# Keep legacy handler for backward compatibility
def legacy_compact_handler(event: Dict) -> Dict:
    """Legacy compaction for backward compatibility."""
    # Implementation would be similar to original compact_handler
    # but using the refactored classes
    pass