"""
Embedding processor that combines merchant validation and embedding creation.

Extracted from embed_from_ndjson handler, this module:
1. Validates merchant using ChromaDB and Google Places API
2. Creates ReceiptMetadata in DynamoDB
3. Generates embeddings for lines and words with merchant context
4. Writes ChromaDB deltas to S3
5. Creates/updates COMPACTION_RUN record
"""

import logging
import os
import sys
import tempfile
import uuid
from typing import Dict, Any, Optional

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_label.data.places_api import PlacesAPI
from receipt_label.embedding.line.realtime import embed_lines_realtime
from receipt_label.embedding.word.realtime import embed_words_realtime
from receipt_label.merchant_resolution.embeddings import upsert_embeddings
from receipt_label.merchant_resolution.resolver import resolve_receipt
from receipt_label.utils.chroma_s3_helpers import upload_bundled_delta_to_s3
from receipt_label.vector_store import VectorClient

logger = logging.getLogger(__name__)

def _log(msg: str):
    """Log message with immediate flush for CloudWatch visibility."""
    print(f"[EMBEDDING_PROCESSOR] {msg}", flush=True)
    logger.info(msg)


class EmbeddingProcessor:
    """Handles merchant validation and embedding creation."""
    
    def __init__(
        self,
        table_name: str,
        chromadb_bucket: str,
        chroma_http_endpoint: Optional[str],
        google_places_api_key: Optional[str],
        openai_api_key: Optional[str],
    ):
        self.dynamo = DynamoClient(table_name)
        self.chromadb_bucket = chromadb_bucket
        self.chroma_http_endpoint = chroma_http_endpoint
        self.google_places_api_key = google_places_api_key
        self.openai_api_key = openai_api_key
        
        # Initialize Places API if key provided
        self.places_api = (
            PlacesAPI(api_key=google_places_api_key)
            if google_places_api_key
            else None
        )
    
    def process_embeddings(
        self,
        image_id: str,
        receipt_id: int,
        lines: Optional[list] = None,
        words: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Process embeddings with merchant validation.
        
        Steps:
        1. Use provided lines/words or fetch from DynamoDB
        2. Resolve merchant (ChromaDB + Google Places)
        3. Create ReceiptMetadata
        4. Generate embeddings with merchant context
        5. Upload deltas to S3
        6. Create COMPACTION_RUN record
        
        Args:
            image_id: Image identifier
            receipt_id: Receipt identifier
            lines: Optional list of receipt lines (if not provided, fetched from DynamoDB)
            words: Optional list of receipt words (if not provided, fetched from DynamoDB)
        
        Returns:
            Dict with run_id, merchant_name, and success status
        """
        try:
            # Step 1: Use provided lines/words or fetch from DynamoDB
            if lines is not None and words is not None:
                receipt_lines = lines
                receipt_words = words
                _log(
                    f"Using provided {len(receipt_lines)} lines, {len(receipt_words)} words "
                    f"for image_id={image_id} receipt_id={receipt_id}"
                )
            else:
                receipt_lines = self.dynamo.list_receipt_lines_from_receipt(
                    image_id, receipt_id
                )
                receipt_words = self.dynamo.list_receipt_words_from_receipt(
                    image_id, receipt_id
                )
                _log(
                    f"Fetched {len(receipt_lines)} lines, {len(receipt_words)} words "
                    f"from DynamoDB for image_id={image_id} receipt_id={receipt_id}"
                )
            
            _log(
                f"Loaded {len(receipt_lines)} lines, {len(receipt_words)} words "
                f"for image_id={image_id} receipt_id={receipt_id}"
            )
            
            # Step 2: Resolve merchant
            merchant_name = self._resolve_merchant(image_id, receipt_id)
            
            # Step 3: Generate embeddings and upload deltas
            run_id = self._create_embeddings_and_deltas(
                image_id=image_id,
                receipt_id=receipt_id,
                lines=receipt_lines,
                words=receipt_words,
                merchant_name=merchant_name,
            )
            
            _log(
                f"Embeddings completed successfully: "
                f"run_id={run_id}, merchant_name={merchant_name}"
            )
            
            return {
                "success": True,
                "run_id": run_id,
                "merchant_name": merchant_name,
                "lines_count": len(receipt_lines),
                "words_count": len(receipt_words),
            }
        
        except Exception as e:
            logger.error(f"Embedding processing failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }
    
    def _resolve_merchant(
        self, image_id: str, receipt_id: int
    ) -> Optional[str]:
        """
        Resolve merchant using ChromaDB and Google Places API.
        
        This creates a ReceiptMetadata record in DynamoDB with validated
        merchant information.
        
        Returns:
            Merchant name or None if resolution fails
        """
        try:
            import tempfile
            from pathlib import Path
            import io
            
            # Download latest ChromaDB snapshot from S3
            chroma_line_client = None
            try:
                _log(
                    "Downloading ChromaDB snapshot from S3 for merchant resolution"
                )
                
                # Step 1: Read latest-pointer.txt to get the correct timestamp
                s3 = boto3.client("s3")
                pointer_key = "lines/snapshot/latest-pointer.txt"
                
                response = s3.get_object(
                    Bucket=self.chromadb_bucket, Key=pointer_key
                )
                timestamp = response["Body"].read().decode().strip()
                _log(f"Latest snapshot timestamp from pointer: {timestamp}")
                
                # Step 2: Create temporary directory for snapshot
                snapshot_dir = tempfile.mkdtemp(prefix="chroma_snapshot_")
                
                # Step 3: Download the timestamped snapshot files from S3
                prefix = f"lines/snapshot/timestamped/{timestamp}/"
                
                paginator = s3.get_paginator("list_objects_v2")
                pages = paginator.paginate(
                    Bucket=self.chromadb_bucket, Prefix=prefix
                )
                
                downloaded_files = 0
                for page in pages:
                    if "Contents" not in page:
                        continue
                    
                    for obj in page["Contents"]:
                        key = obj["Key"]
                        # Skip the .snapshot_hash file
                        if key.endswith(".snapshot_hash"):
                            continue
                        
                        # Get the relative path within the snapshot
                        relative_path = key[len(prefix):]
                        if not relative_path:
                            continue
                        
                        # Create local directory structure
                        local_path = Path(snapshot_dir) / relative_path
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        
                        # Download the file
                        s3.download_file(
                            self.chromadb_bucket, key, str(local_path)
                        )
                        downloaded_files += 1
                
                _log(
                    f"Downloaded {downloaded_files} snapshot files to {snapshot_dir}"
                )
                
                if downloaded_files > 0:
                    # Create local ChromaDB client pointing to snapshot
                    chroma_line_client = VectorClient.create_chromadb_client(
                        persist_directory=snapshot_dir,
                        mode="read",
                    )
                    _log(
                        "Created local ChromaDB client from snapshot for merchant resolution"
                    )
            
            except Exception as e:
                logger.warning(
                    f"Failed to download ChromaDB snapshot from S3: {e}. "
                    f"Will attempt HTTP connection as fallback."
                )
                # Fall back to HTTP if snapshot download fails
                if self.chroma_http_endpoint:
                    try:
                        chroma_line_client = VectorClient.create_chromadb_client(
                            mode="read",
                            http_url=self.chroma_http_endpoint
                        )
                        _log(
                            f"Created HTTP ChromaDB client: {self.chroma_http_endpoint}"
                        )
                    except Exception as http_error:
                        logger.warning(
                            f"Failed to create HTTP ChromaDB client: {http_error}"
                        )
            
            # Create embedding function for merchant resolution
            def _embed_texts(texts):
                if not texts:
                    return []
                if not self.openai_api_key:
                    raise RuntimeError("OPENAI_API_KEY is not set")
                
                from receipt_label.utils import get_client_manager
                openai_client = get_client_manager().openai
                resp = openai_client.embeddings.create(
                    model="text-embedding-3-small",
                    input=list(texts)
                )
                _log(
                    f"OpenAI embeddings created for merchant resolution: "
                    f"{len(resp.data)} embeddings"
                )
                return [d.embedding for d in resp.data]
            
            # Resolve merchant
            resolution = resolve_receipt(
                key=(image_id, receipt_id),
                dynamo=self.dynamo,
                places_api=self.places_api,
                chroma_line_client=chroma_line_client,
                embed_fn=_embed_texts,
                write_metadata=True,  # Creates ReceiptMetadata in DynamoDB
            )
            
            # Extract merchant name from resolution
            decision = resolution.get("decision") or {}
            best = decision.get("best") or {}
            merchant_name = best.get("name") or best.get("merchant_name")
            
            _log(
                f"Merchant resolved: {merchant_name} "
                f"(source: {best.get('source')}, score: {best.get('score')})"
            )
            
            return merchant_name
        
        except Exception as e:
            logger.error(f"Merchant resolution failed: {e}", exc_info=True)
            return None
    
    def _create_embeddings_and_deltas(
        self,
        image_id: str,
        receipt_id: int,
        lines: list,
        words: list,
        merchant_name: Optional[str],
    ) -> str:
        """
        Create embeddings and upload ChromaDB deltas to S3.
        
        Returns:
            run_id for the compaction run
        """
        # Generate run ID
        run_id = str(uuid.uuid4())
        
        # Create local ChromaDB deltas
        delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
        delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")
        
        line_client = VectorClient.create_chromadb_client(
            persist_directory=delta_lines_dir,
            mode="delta",
            metadata_only=True
        )
        word_client = VectorClient.create_chromadb_client(
            persist_directory=delta_words_dir,
            mode="delta",
            metadata_only=True
        )
        
        # Upsert embeddings with merchant context
        _log(
            f"Creating embeddings with merchant_name={merchant_name}"
        )
        upsert_embeddings(
            line_client=line_client,
            word_client=word_client,
            line_embed_fn=embed_lines_realtime,
            word_embed_fn=embed_words_realtime,
            ctx={"lines": lines, "words": words},
            merchant_name=merchant_name,  # ← Included in metadata!
        )
        
        # Upload deltas to S3
        lines_prefix = f"lines/delta/{run_id}/"
        words_prefix = f"words/delta/{run_id}/"
        
        _log(
            f"Uploading line delta to s3://{self.chromadb_bucket}/{lines_prefix}"
        )
        upload_bundled_delta_to_s3(
            local_delta_dir=delta_lines_dir,
            bucket=self.chromadb_bucket,
            delta_prefix=lines_prefix,
            metadata={
                "run_id": run_id,
                "image_id": image_id,
                "receipt_id": str(receipt_id),
                "collection": "lines",
            },
        )
        
        _log(
            f"Uploading word delta to s3://{self.chromadb_bucket}/{words_prefix}"
        )
        upload_bundled_delta_to_s3(
            local_delta_dir=delta_words_dir,
            bucket=self.chromadb_bucket,
            delta_prefix=words_prefix,
            metadata={
                "run_id": run_id,
                "image_id": image_id,
                "receipt_id": str(receipt_id),
                "collection": "words",
            },
        )
        
        # Create COMPACTION_RUN record
        try:
            _log(f"About to create COMPACTION_RUN with run_id={run_id}")
            self.dynamo.add_compaction_run(
                CompactionRun(
                    run_id=run_id,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    lines_delta_prefix=lines_prefix,
                    words_delta_prefix=words_prefix,
                )
            )
            _log(f"COMPACTION_RUN created: run_id={run_id}")
        except Exception as e:
            _log(f"ERROR creating COMPACTION_RUN: {e}")
            logger.error(f"Failed to create COMPACTION_RUN: {e}", exc_info=True)
            raise
        
        return run_id

