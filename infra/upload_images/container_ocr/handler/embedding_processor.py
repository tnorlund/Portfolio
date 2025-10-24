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
import tempfile
import uuid
from typing import Dict, Any, Optional

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
    ) -> Dict[str, Any]:
        """
        Process embeddings with merchant validation.
        
        Steps:
        1. Fetch lines and words from DynamoDB
        2. Resolve merchant (ChromaDB + Google Places)
        3. Create ReceiptMetadata
        4. Generate embeddings with merchant context
        5. Upload deltas to S3
        6. Create COMPACTION_RUN record
        
        Returns:
            Dict with run_id, merchant_name, and success status
        """
        try:
            # Step 1: Fetch lines and words from DynamoDB
            receipt_lines, _ = self.dynamo.list_receipt_lines(
                image_id, receipt_id, limit=1000
            )
            receipt_words, _ = self.dynamo.list_receipt_words(
                image_id, receipt_id, limit=10000
            )
            
            logger.info(
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
            
            logger.info(
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
            # Initialize ChromaDB client for merchant resolution
            chroma_line_client = None
            if self.chroma_http_endpoint:
                try:
                    chroma_line_client = VectorClient.create_chromadb_client(
                        mode="read",
                        http_url=self.chroma_http_endpoint
                    )
                    logger.info(
                        f"ChromaDB client initialized: {self.chroma_http_endpoint}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to initialize ChromaDB client: {e}"
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
                logger.info(
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
            
            logger.info(
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
        logger.info(
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
        
        logger.info(
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
        
        logger.info(
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
        self.dynamo.add_compaction_run(
            CompactionRun(
                run_id=run_id,
                image_id=image_id,
                receipt_id=receipt_id,
                lines_delta_prefix=lines_prefix,
                words_delta_prefix=words_prefix,
            )
        )
        
        logger.info(f"COMPACTION_RUN created: run_id={run_id}")
        
        return run_id

