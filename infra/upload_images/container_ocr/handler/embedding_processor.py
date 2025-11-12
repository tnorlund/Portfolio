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
from receipt_label.embedding.line.realtime import embed_lines_realtime
from receipt_label.embedding.word.realtime import embed_words_realtime
from receipt_label.merchant_resolution.embeddings import upsert_embeddings
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

            # Step 2: Resolve merchant (pass entities directly to avoid re-reading from DynamoDB)
            merchant_resolution = self._resolve_merchant(
                image_id, receipt_id, receipt_lines=receipt_lines, receipt_words=receipt_words
            )
            if merchant_resolution is None:
                # Handle case where resolution returns None
                merchant_name = None
                receipt_metadata = None
            else:
                merchant_name, receipt_metadata = merchant_resolution

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
                "receipt_metadata": receipt_metadata,  # Pass to validation
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
        self, image_id: str, receipt_id: int, receipt_lines: Optional[list] = None, receipt_words: Optional[list] = None
    ) -> tuple[Optional[str], Optional[Any]]:
        """
        Resolve merchant using LangGraph workflow with Ollama Cloud.

        This creates a ReceiptMetadata record in DynamoDB with validated
        merchant information using the LangGraph workflow.

        Args:
            image_id: Image identifier
            receipt_id: Receipt identifier
            receipt_lines: Optional pre-fetched receipt lines (to avoid re-reading from DynamoDB)
            receipt_words: Optional pre-fetched receipt words (to avoid re-reading from DynamoDB)

        Returns:
            Tuple of (merchant_name, receipt_metadata) or (None, None) if resolution fails
        """
        try:
            import asyncio
            import tempfile
            from pathlib import Path

            # Import LangGraph workflow
            from receipt_label.langchain.metadata_creation import create_receipt_metadata_simple

            # Get API keys from environment
            ollama_api_key = os.environ.get("OLLAMA_API_KEY")
            langchain_api_key = os.environ.get("LANGCHAIN_API_KEY")

            if not ollama_api_key:
                raise ValueError("OLLAMA_API_KEY is required for LangGraph metadata creation")
            if not langchain_api_key:
                raise ValueError("LANGCHAIN_API_KEY is required for LangGraph metadata creation")

            # Try EFS first, fallback to S3 download for ChromaDB (optional fast path)
            chroma_line_client = None
            embed_fn = None
            storage_mode = os.environ.get("CHROMADB_STORAGE_MODE", "auto").lower()
            efs_root = os.environ.get("CHROMA_ROOT")

            # Determine storage mode
            if storage_mode == "s3":
                use_efs = False
                mode_reason = "explicitly set to S3-only"
            elif storage_mode == "efs":
                use_efs = True
                mode_reason = "explicitly set to EFS"
            elif storage_mode == "auto":
                # Auto-detect based on EFS availability
                use_efs = efs_root and efs_root != "/tmp/chroma"
                mode_reason = f"auto-detected (efs_root={'available' if use_efs else 'not available'})"
            else:
                # Default to S3-only for unknown modes
                use_efs = False
                mode_reason = f"unknown mode '{storage_mode}', defaulting to S3-only"

            _log(f"Storage mode: {storage_mode}, use_efs: {use_efs}, reason: {mode_reason}")

            # Setup ChromaDB client if available (optional fast path)
            if use_efs:
                try:
                    from .efs_snapshot_manager import UploadEFSSnapshotManager

                    _log("Using EFS for ChromaDB snapshot access")
                    efs_manager = UploadEFSSnapshotManager("lines", logger)

                    # Get snapshot ready for ChromaDB operations
                    snapshot_info = efs_manager.get_snapshot_for_chromadb()
                    if snapshot_info:
                        # Create local ChromaDB client pointing to local copy
                        chroma_line_client = VectorClient.create_chromadb_client(
                            persist_directory=snapshot_info["local_path"],
                            mode="read",
                        )
                        _log(
                            f"Created ChromaDB client from EFS snapshot "
                            f"(version: {snapshot_info['version']}, "
                            f"source: {snapshot_info['source']}, "
                            f"copy_time_ms: {snapshot_info.get('copy_time_ms', 0)})"
                        )
                    else:
                        _log("Failed to get snapshot from EFS, falling back to S3")
                        use_efs = False

                except Exception as e:
                    logger.warning(f"Failed to use EFS for ChromaDB access: {e}. Falling back to S3.")
                    use_efs = False

            # Fallback to S3 download if EFS failed or disabled
            if not use_efs:
                _log("Using S3 download for ChromaDB snapshot access (optional)")
                try:
                    # Download latest ChromaDB snapshot from S3
                    s3 = boto3.client("s3")
                    pointer_key = "lines/snapshot/latest-pointer.txt"

                    response = s3.get_object(
                        Bucket=self.chromadb_bucket, Key=pointer_key
                    )
                    timestamp = response["Body"].read().decode().strip()
                    _log(f"Latest snapshot timestamp from pointer: {timestamp}")

                    # Create temporary directory for snapshot
                    snapshot_dir = tempfile.mkdtemp(prefix="chroma_snapshot_")

                    # Download the timestamped snapshot files from S3
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
                            "Created local ChromaDB client from snapshot for optional fast-path"
                        )

                except Exception as e:
                    logger.warning(
                        f"Failed to download ChromaDB snapshot from S3: {e}. "
                        f"Will proceed without ChromaDB fast-path."
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
                                f"Failed to create HTTP ChromaDB client: {http_error}. "
                                f"Proceeding without ChromaDB fast-path."
                            )

            # Create embedding function for ChromaDB (if available)
            if chroma_line_client:
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
                        f"OpenAI embeddings created for ChromaDB: "
                        f"{len(resp.data)} embeddings"
                    )
                    return [d.embedding for d in resp.data]
                embed_fn = _embed_texts

            # Create metadata using LangGraph workflow
            _log("Creating ReceiptMetadata using LangGraph workflow")
            metadata = asyncio.run(
                create_receipt_metadata_simple(
                    client=self.dynamo,
                    image_id=image_id,
                    receipt_id=receipt_id,
                    google_places_api_key=self.google_places_api_key,
                    ollama_api_key=ollama_api_key,
                    langsmith_api_key=langchain_api_key,
                    thinking_strength="medium",
                    receipt_lines=receipt_lines,  # Pass entities directly to avoid re-reading
                    receipt_words=receipt_words,  # Pass entities directly to avoid re-reading
                    chroma_line_client=chroma_line_client,  # Optional fast-path
                    embed_fn=embed_fn,  # Optional embedding function
                )
            )

            if metadata:
                merchant_name = metadata.merchant_name
                _log(
                    f"✅ Successfully created ReceiptMetadata: {merchant_name} "
                    f"(place_id: {metadata.place_id})"
                )
                return merchant_name, metadata
            else:
                _log("⚠️ LangGraph workflow did not create ReceiptMetadata")
                return None, None

        except Exception as e:
            logger.error(f"Merchant resolution failed: {e}", exc_info=True)
            return None, None

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

