"""
ChromaDB Snapshot Client - Modular Infrastructure-Aligned Implementation

This client provides a simple interface for ChromaDB snapshot operations
by wrapping the existing, battle-tested infrastructure functions from
chroma_s3_helpers.py.

Unlike the flawed SnapshotManager, this client:
- Uses correct S3 path structure ({collection}/snapshot/latest/)  
- Integrates with existing .snapshot_hash files
- Leverages proven infrastructure functions
- Supports proper hash verification
"""

import os
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path
from enum import Enum

logger = logging.getLogger(__name__)


class ChromaDBCollection(Enum):
    """Supported ChromaDB collections matching infrastructure"""
    WORDS = "words"
    LINES = "lines"


class ChromaDBSnapshotClient:
    """
    Simple, reliable ChromaDB snapshot client.
    
    This client wraps existing infrastructure functions to provide
    a clean interface for snapshot operations while ensuring
    compatibility with the production infrastructure.
    """
    
    def __init__(self, bucket_name: str, region: Optional[str] = None):
        """
        Initialize the snapshot client.
        
        Args:
            bucket_name: S3 bucket name (e.g., "chromadb-vectors-dev")
            region: AWS region (optional, uses default if not specified)
        """
        self.bucket_name = bucket_name
        self.region = region
        
        # Import infrastructure functions
        try:
            from receipt_label.utils.chroma_s3_helpers import (
                download_snapshot_with_verification,
                verify_chromadb_sync,
                consume_latest_snapshot
            )
            self._download_snapshot_with_verification = download_snapshot_with_verification
            self._verify_chromadb_sync = verify_chromadb_sync
            self._consume_latest_snapshot = consume_latest_snapshot
            
        except ImportError as e:
            logger.error("Failed to import infrastructure functions: %s", e)
            raise RuntimeError(
                "Could not import required infrastructure functions. "
                "Ensure chroma_s3_helpers.py is available."
            ) from e
    
    def download_collection_snapshot(
        self,
        collection: ChromaDBCollection,
        local_directory: str,
        verify_hash: bool = True,
        expected_hash: Optional[str] = None,
        hash_algorithm: str = "md5"
    ) -> Dict[str, Any]:
        """
        Download a ChromaDB collection snapshot from S3.
        
        Uses the existing infrastructure function with proper S3 path construction.
        
        Args:
            collection: ChromaDB collection to download
            local_directory: Local directory to download to
            verify_hash: Whether to verify hash after download
            expected_hash: Expected hash (if None, reads from .snapshot_hash file)
            hash_algorithm: Hash algorithm for verification
            
        Returns:
            Dict with download status, hash verification results, and statistics
        """
        # Construct proper S3 path: {collection}/snapshot/latest/
        snapshot_key = f"{collection.value}/snapshot/latest/"
        
        logger.info(
            "Downloading %s collection snapshot to %s", 
            collection.value, local_directory
        )
        
        # Ensure local directory exists
        Path(local_directory).mkdir(parents=True, exist_ok=True)
        
        # Use existing infrastructure function
        result = self._download_snapshot_with_verification(
            bucket=self.bucket_name,
            snapshot_key=snapshot_key,
            local_snapshot_path=local_directory,
            verify_hash=verify_hash,
            expected_hash=expected_hash,
            hash_algorithm=hash_algorithm,
            region=self.region
        )
        
        # Add client metadata
        result["collection"] = collection.value
        result["bucket"] = self.bucket_name
        result["snapshot_key"] = snapshot_key
        
        return result
    
    def verify_collection_sync(
        self,
        collection: ChromaDBCollection,
        local_directory: Optional[str] = None,
        download_for_comparison: bool = False,
        hash_algorithm: str = "md5"
    ) -> Dict[str, Any]:
        """
        Verify if local ChromaDB directory is in sync with S3 snapshot.
        
        This provides fast comparison using hash values without downloading
        the full snapshot (unless requested).
        
        Args:
            collection: ChromaDB collection to verify
            local_directory: Local directory path (if None, only checks S3 hash)
            download_for_comparison: If True, downloads S3 for full comparison
            hash_algorithm: Hash algorithm to use for comparison
            
        Returns:
            Dict with sync status ("identical", "different", "error") and recommendations
        """
        logger.info(
            "Verifying %s collection sync with local directory: %s",
            collection.value, local_directory
        )
        
        # Use existing infrastructure function with proper database name
        result = self._verify_chromadb_sync(
            database=collection.value,
            bucket=self.bucket_name,
            local_path=local_directory,
            download_for_comparison=download_for_comparison,
            hash_algorithm=hash_algorithm,
            region=self.region
        )
        
        # Add client metadata
        result["collection"] = collection.value
        result["bucket"] = self.bucket_name
        
        return result
    
    def create_collection_client(
        self,
        collection: ChromaDBCollection,
        local_directory: str = "/tmp/chroma_snapshot"
    ):
        """
        Download snapshot and create ChromaDB client ready for querying.
        
        This is the standard pattern for applications that need to query
        the vector database.
        
        Args:
            collection: ChromaDB collection to setup
            local_directory: Local directory to download snapshot to
            
        Returns:
            Configured ChromaDBClient ready for querying
            
        Raises:
            RuntimeError: If collection doesn't exist or download fails
        """
        logger.info(
            "Creating ChromaDB client for %s collection at %s",
            collection.value, local_directory
        )
        
        # Use existing infrastructure function
        client = self._consume_latest_snapshot(
            local_path=local_directory,
            bucket_name=self.bucket_name,
            collection_name=collection.value
        )
        
        return client
    
    def list_available_collections(self) -> List[str]:
        """
        List available collections in the S3 bucket.
        
        Returns:
            List of collection names found in S3
        """
        try:
            import boto3
            
            # Create S3 client
            client_kwargs = {"service_name": "s3"}
            if self.region:
                client_kwargs["region_name"] = self.region
            s3_client = boto3.client(**client_kwargs)
            
            # List common prefixes to find collections
            response = s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Delimiter="/",
                MaxKeys=100
            )
            
            collections = []
            for prefix in response.get("CommonPrefixes", []):
                collection_name = prefix["Prefix"].rstrip("/")
                # Filter for known collection patterns
                if collection_name in [ChromaDBCollection.WORDS.value, ChromaDBCollection.LINES.value]:
                    collections.append(collection_name)
            
            logger.info("Found collections in S3: %s", collections)
            return collections
            
        except Exception as e:
            logger.error("Failed to list collections: %s", e)
            return []
    
    def get_collection_info(self, collection: ChromaDBCollection) -> Dict[str, Any]:
        """
        Get information about a collection's snapshot.
        
        Args:
            collection: ChromaDB collection to inspect
            
        Returns:
            Dict with collection metadata including hash, size, etc.
        """
        try:
            import boto3
            
            # Create S3 client  
            client_kwargs = {"service_name": "s3"}
            if self.region:
                client_kwargs["region_name"] = self.region
            s3_client = boto3.client(**client_kwargs)
            
            # Get hash file
            hash_key = f"{collection.value}/snapshot/latest/.snapshot_hash"
            
            try:
                response = s3_client.get_object(
                    Bucket=self.bucket_name,
                    Key=hash_key
                )
                
                import json
                hash_data = json.loads(response["Body"].read().decode("utf-8"))
                
                return {
                    "collection": collection.value,
                    "bucket": self.bucket_name,
                    "hash_key": hash_key,
                    "hash_data": hash_data,
                    "last_modified": response.get("LastModified"),
                    "status": "available"
                }
                
            except s3_client.exceptions.NoSuchKey:
                return {
                    "collection": collection.value,
                    "bucket": self.bucket_name,
                    "status": "no_hash_file",
                    "error": f"No .snapshot_hash file found at {hash_key}"
                }
                
        except Exception as e:
            logger.error("Failed to get collection info: %s", e)
            return {
                "collection": collection.value,
                "bucket": self.bucket_name,
                "status": "error",
                "error": str(e)
            }


# Convenience functions for common use cases

def download_words_collection(
    bucket_name: str, 
    local_directory: str,
    verify_hash: bool = True,
    region: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convenience function to download the words collection.
    
    Args:
        bucket_name: S3 bucket name
        local_directory: Local directory to download to
        verify_hash: Whether to verify hash after download
        region: AWS region (optional)
        
    Returns:
        Download result dict
    """
    client = ChromaDBSnapshotClient(bucket_name, region)
    return client.download_collection_snapshot(
        ChromaDBCollection.WORDS,
        local_directory,
        verify_hash=verify_hash
    )


def download_lines_collection(
    bucket_name: str,
    local_directory: str, 
    verify_hash: bool = True,
    region: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convenience function to download the lines collection.
    
    Args:
        bucket_name: S3 bucket name
        local_directory: Local directory to download to
        verify_hash: Whether to verify hash after download
        region: AWS region (optional)
        
    Returns:
        Download result dict
    """
    client = ChromaDBSnapshotClient(bucket_name, region)
    return client.download_collection_snapshot(
        ChromaDBCollection.LINES,
        local_directory,
        verify_hash=verify_hash
    )


def create_words_client(
    bucket_name: str,
    local_directory: str = "/tmp/chroma_words",
    region: Optional[str] = None
):
    """
    Convenience function to create a words collection client.
    
    Args:
        bucket_name: S3 bucket name
        local_directory: Local directory for snapshot
        region: AWS region (optional)
        
    Returns:
        Configured ChromaDBClient for words collection
    """
    client = ChromaDBSnapshotClient(bucket_name, region)
    return client.create_collection_client(
        ChromaDBCollection.WORDS,
        local_directory
    )