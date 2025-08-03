#!/usr/bin/env python3
"""
Script to migrate vector embeddings from Pinecone to ChromaDB.

This script facilitates the migration of existing receipt word and line embeddings
from Pinecone to ChromaDB as part of the transition to self-hosted vector storage.

Usage:
    python scripts/migrate_pinecone_to_chroma.py [options]

Options:
    --namespace: Pinecone namespace to migrate (default: all)
    --batch-size: Number of vectors to process at once (default: 100)
    --dry-run: Preview migration without making changes
    --chroma-path: Path for ChromaDB persistence (default: from env)
"""

import argparse
import logging
import os
import sys
from typing import Any, Dict, List, Optional
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from pinecone import Pinecone
    PINECONE_AVAILABLE = True
except ImportError:
    PINECONE_AVAILABLE = False
    print("Warning: Pinecone not installed. This script requires pinecone-client.")

from receipt_label.utils.chroma_client import ChromaDBClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PineconeToChromaMigrator:
    """Handles migration of vector data from Pinecone to ChromaDB."""
    
    def __init__(
        self,
        pinecone_api_key: str,
        pinecone_index_name: str,
        pinecone_host: str,
        chroma_persist_path: Optional[str] = None,
        batch_size: int = 100
    ):
        """
        Initialize the migrator.
        
        Args:
            pinecone_api_key: Pinecone API key
            pinecone_index_name: Name of the Pinecone index
            pinecone_host: Pinecone host URL
            chroma_persist_path: Path for ChromaDB persistence
            batch_size: Number of vectors to process at once
        """
        if not PINECONE_AVAILABLE:
            raise ImportError("Pinecone is required for migration. Install with: pip install pinecone-client")
        
        # Initialize Pinecone
        self.pc = Pinecone(api_key=pinecone_api_key)
        self.pinecone_index = self.pc.Index(pinecone_index_name, host=pinecone_host)
        
        # Initialize ChromaDB
        self.chroma_client = ChromaDBClient(
            persist_directory=chroma_persist_path,
            use_persistent_client=chroma_persist_path is not None
        )
        
        self.batch_size = batch_size
        self.stats = {
            "words_migrated": 0,
            "lines_migrated": 0,
            "errors": 0,
            "skipped": 0
        }
    
    def get_namespaces(self) -> List[str]:
        """Get list of namespaces in Pinecone index."""
        try:
            stats = self.pinecone_index.describe_index_stats()
            return list(stats.namespaces.keys())
        except Exception as e:
            logger.error(f"Error getting Pinecone namespaces: {e}")
            return []
    
    def migrate_namespace(self, namespace: str, dry_run: bool = False) -> Dict[str, int]:
        """
        Migrate a specific namespace from Pinecone to ChromaDB.
        
        Args:
            namespace: Namespace to migrate
            dry_run: If True, preview without making changes
            
        Returns:
            Migration statistics
        """
        logger.info(f"Starting migration of namespace: {namespace}")
        
        # Determine collection name based on namespace
        collection_name = namespace if namespace else "default"
        
        try:
            # Get total vector count
            stats = self.pinecone_index.describe_index_stats()
            total_vectors = stats.namespaces.get(namespace, {}).get("vector_count", 0)
            
            if total_vectors == 0:
                logger.warning(f"No vectors found in namespace: {namespace}")
                return {"migrated": 0, "errors": 0}
            
            logger.info(f"Found {total_vectors} vectors to migrate in namespace: {namespace}")
            
            # Iterate through all vectors
            migrated = 0
            errors = 0
            
            # Pinecone doesn't support pagination directly, so we use ID ranges
            # This is a simplified approach - in production you might need a more sophisticated method
            cursor = None
            
            while True:
                try:
                    # Fetch batch of vectors
                    if cursor:
                        # Query with pagination token
                        response = self.pinecone_index.query(
                            namespace=namespace,
                            top_k=self.batch_size,
                            include_values=True,
                            include_metadata=True,
                            filter={"id": {"$gt": cursor}}
                        )
                    else:
                        # Initial query - get some vectors to start
                        # Using a dummy vector for initial fetch
                        response = self.pinecone_index.query(
                            namespace=namespace,
                            vector=[0.0] * 1536,  # Assuming 1536 dimensions
                            top_k=self.batch_size,
                            include_values=True,
                            include_metadata=True
                        )
                    
                    if not response.matches:
                        break
                    
                    # Prepare data for ChromaDB
                    ids = []
                    embeddings = []
                    metadatas = []
                    documents = []
                    
                    for match in response.matches:
                        ids.append(match.id)
                        embeddings.append(match.values)
                        
                        # Extract metadata
                        metadata = match.metadata or {}
                        metadatas.append(metadata)
                        
                        # Use text from metadata as document
                        documents.append(metadata.get("text", ""))
                    
                    if not dry_run and ids:
                        # Store in ChromaDB
                        self.chroma_client.upsert_vectors(
                            collection_name=collection_name,
                            ids=ids,
                            embeddings=embeddings,
                            documents=documents,
                            metadatas=metadatas
                        )
                    
                    migrated += len(ids)
                    
                    # Update cursor for next batch
                    if ids:
                        cursor = ids[-1]
                    
                    # Log progress
                    if migrated % 1000 == 0:
                        logger.info(f"Migrated {migrated}/{total_vectors} vectors from {namespace}")
                    
                    # Check if we've processed all vectors
                    if len(response.matches) < self.batch_size:
                        break
                        
                except Exception as e:
                    logger.error(f"Error migrating batch: {e}")
                    errors += 1
                    # Continue with next batch
                    if cursor:
                        # Move past the problematic ID
                        cursor = f"{cursor}_skip"
            
            # Update stats
            if namespace == "words":
                self.stats["words_migrated"] += migrated
            elif namespace == "lines":
                self.stats["lines_migrated"] += migrated
            
            logger.info(f"Completed migration of {namespace}: {migrated} vectors migrated, {errors} errors")
            
            return {"migrated": migrated, "errors": errors}
            
        except Exception as e:
            logger.error(f"Error migrating namespace {namespace}: {e}")
            return {"migrated": 0, "errors": 1}
    
    def migrate_all(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Migrate all namespaces from Pinecone to ChromaDB.
        
        Args:
            dry_run: If True, preview without making changes
            
        Returns:
            Overall migration statistics
        """
        namespaces = self.get_namespaces()
        
        if not namespaces:
            logger.warning("No namespaces found in Pinecone index")
            return self.stats
        
        logger.info(f"Found namespaces: {namespaces}")
        
        for namespace in namespaces:
            self.migrate_namespace(namespace, dry_run)
        
        return self.stats
    
    def verify_migration(self, namespace: str) -> Dict[str, Any]:
        """
        Verify that migration was successful by comparing counts.
        
        Args:
            namespace: Namespace to verify
            
        Returns:
            Verification results
        """
        try:
            # Get Pinecone count
            stats = self.pinecone_index.describe_index_stats()
            pinecone_count = stats.namespaces.get(namespace, {}).get("vector_count", 0)
            
            # Get ChromaDB count
            collection_name = namespace if namespace else "default"
            chroma_count = self.chroma_client.count(collection_name)
            
            return {
                "namespace": namespace,
                "pinecone_count": pinecone_count,
                "chroma_count": chroma_count,
                "match": pinecone_count == chroma_count
            }
            
        except Exception as e:
            logger.error(f"Error verifying migration: {e}")
            return {
                "namespace": namespace,
                "error": str(e)
            }


def main():
    """Main entry point for the migration script."""
    parser = argparse.ArgumentParser(
        description="Migrate vector embeddings from Pinecone to ChromaDB"
    )
    parser.add_argument(
        "--namespace",
        help="Specific namespace to migrate (default: all)",
        default=None
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of vectors to process at once"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without making changes"
    )
    parser.add_argument(
        "--chroma-path",
        help="Path for ChromaDB persistence (default: from CHROMA_PERSIST_PATH env)",
        default=os.environ.get("CHROMA_PERSIST_PATH")
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify migration by comparing counts"
    )
    
    args = parser.parse_args()
    
    # Check required environment variables
    pinecone_api_key = os.environ.get("PINECONE_API_KEY")
    pinecone_index_name = os.environ.get("PINECONE_INDEX_NAME")
    pinecone_host = os.environ.get("PINECONE_HOST")
    
    if not all([pinecone_api_key, pinecone_index_name, pinecone_host]):
        logger.error(
            "Missing required Pinecone environment variables: "
            "PINECONE_API_KEY, PINECONE_INDEX_NAME, PINECONE_HOST"
        )
        sys.exit(1)
    
    # Initialize migrator
    migrator = PineconeToChromaMigrator(
        pinecone_api_key=pinecone_api_key,
        pinecone_index_name=pinecone_index_name,
        pinecone_host=pinecone_host,
        chroma_persist_path=args.chroma_path,
        batch_size=args.batch_size
    )
    
    if args.verify:
        # Verify mode
        if args.namespace:
            results = migrator.verify_migration(args.namespace)
            print(f"\nVerification results for {args.namespace}:")
            print(f"  Pinecone count: {results.get('pinecone_count', 'N/A')}")
            print(f"  ChromaDB count: {results.get('chroma_count', 'N/A')}")
            print(f"  Match: {results.get('match', False)}")
        else:
            # Verify all namespaces
            namespaces = migrator.get_namespaces()
            print("\nVerification results:")
            for namespace in namespaces:
                results = migrator.verify_migration(namespace)
                print(f"\n{namespace}:")
                print(f"  Pinecone count: {results.get('pinecone_count', 'N/A')}")
                print(f"  ChromaDB count: {results.get('chroma_count', 'N/A')}")
                print(f"  Match: {results.get('match', False)}")
    else:
        # Migration mode
        if args.dry_run:
            logger.info("DRY RUN MODE - No changes will be made")
        
        if args.namespace:
            # Migrate specific namespace
            stats = migrator.migrate_namespace(args.namespace, args.dry_run)
            print(f"\nMigration completed for {args.namespace}:")
            print(f"  Vectors migrated: {stats['migrated']}")
            print(f"  Errors: {stats['errors']}")
        else:
            # Migrate all namespaces
            stats = migrator.migrate_all(args.dry_run)
            print("\nMigration completed:")
            print(f"  Words migrated: {stats['words_migrated']}")
            print(f"  Lines migrated: {stats['lines_migrated']}")
            print(f"  Total errors: {stats['errors']}")
            print(f"  Skipped: {stats['skipped']}")


if __name__ == "__main__":
    main()