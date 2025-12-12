"""
Metadata finder processor that uses the receipt metadata finder agent
to find ALL missing metadata fields (place_id, merchant_name, address, phone_number).

This processor runs after initial metadata creation to fill in any missing fields
using agent-based reasoning.
"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, Optional

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_dynamo import DynamoClient
from receipt_places.receipt_places.places_client import PlacesClient

logger = logging.getLogger(__name__)


def _log(msg: str):
    """Log message with immediate flush for CloudWatch visibility."""
    print(f"[METADATA_FINDER] {msg}", flush=True)
    logger.info(msg)


class MetadataFinderProcessor:
    """Uses receipt metadata finder agent to find missing metadata fields."""

    def __init__(
        self,
        table_name: str,
        chromadb_bucket: str,
        chroma_http_endpoint: Optional[str],
        google_places_api_key: Optional[str],
        openai_api_key: Optional[str],
        ollama_api_key: Optional[str],
        langsmith_api_key: Optional[str],
    ):
        self.dynamo = DynamoClient(table_name)
        self.chromadb_bucket = chromadb_bucket
        self.chroma_http_endpoint = chroma_http_endpoint
        self.google_places_api_key = google_places_api_key
        self.openai_api_key = openai_api_key
        self.ollama_api_key = ollama_api_key
        self.langsmith_api_key = langsmith_api_key

    async def find_missing_metadata(
        self,
        image_id: str,
        receipt_id: int,
        receipt_lines: Optional[list] = None,
        receipt_words: Optional[list] = None,
        existing_metadata: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Find missing metadata fields using the receipt metadata finder agent.

        This method:
        1. Checks if metadata has any missing fields
        2. Uses the agent to find missing fields
        3. Updates ReceiptMetadata with found fields

        Args:
            image_id: Image identifier
            receipt_id: Receipt identifier
            receipt_lines: Optional pre-fetched receipt lines
            receipt_words: Optional pre-fetched receipt words
            existing_metadata: Optional existing ReceiptMetadata (to avoid re-reading)

        Returns:
            Dict with success status, fields_found, and updated metadata
        """
        try:
            # Check if we need to run the finder
            if existing_metadata is None:
                try:
                    existing_metadata = self.dynamo.get_receipt_metadata(
                        image_id, receipt_id
                    )
                except Exception:
                    # Metadata doesn't exist yet, skip finder (should be created by initial workflow)
                    _log(
                        f"ReceiptMetadata doesn't exist yet for {image_id}#{receipt_id}, skipping finder"
                    )
                    return {
                        "success": False,
                        "reason": "metadata_not_exists",
                        "fields_found": [],
                    }

            # Check if all fields are already present
            has_all_fields = (
                existing_metadata.place_id
                and existing_metadata.merchant_name
                and existing_metadata.address
                and existing_metadata.phone_number
            )

            if has_all_fields:
                _log(
                    f"All metadata fields already present for {image_id}#{receipt_id}, skipping finder"
                )
                return {
                    "success": True,
                    "reason": "all_fields_present",
                    "fields_found": [],
                    "metadata": existing_metadata,
                }

            # Determine which fields are missing
            missing_fields = []
            if not existing_metadata.place_id:
                missing_fields.append("place_id")
            if not existing_metadata.merchant_name:
                missing_fields.append("merchant_name")
            if not existing_metadata.address:
                missing_fields.append("address")
            if not existing_metadata.phone_number:
                missing_fields.append("phone_number")

            _log(
                f"Found {len(missing_fields)} missing fields for {image_id}#{receipt_id}: "
                f"{', '.join(missing_fields)}"
            )

            # Setup ChromaDB client and embedding function
            chroma_client, embed_fn = await self._setup_chromadb()

            if not chroma_client or not embed_fn:
                _log("⚠️ ChromaDB not available, skipping metadata finder")
                return {
                    "success": False,
                    "reason": "chromadb_unavailable",
                    "fields_found": [],
                }

            # Setup Places client
            places_client = None
            if self.google_places_api_key:
                try:
                    places_client = PlacesClient(
                        api_key=self.google_places_api_key
                    )
                except Exception as e:
                    _log(f"⚠️ Failed to create Places client: {e}")

            # Import and run the metadata finder agent
            from receipt_agent.subagents.metadata_finder import (
                create_receipt_metadata_finder_graph,
                run_receipt_metadata_finder,
            )

            # Create the agent graph
            _log("Creating receipt metadata finder agent graph...")
            graph, state_holder = create_receipt_metadata_finder_graph(
                dynamo_client=self.dynamo,
                chroma_client=chroma_client,
                embed_fn=embed_fn,
                places_api=places_client,
                settings=None,  # Use defaults
            )

            # Run the agent
            # Convert receipt_lines/receipt_words to dict format if provided
            # The agent expects entity objects, but we'll pass them as-is and let the workflow convert
            _log(
                f"Running metadata finder agent for {image_id}#{receipt_id}..."
            )
            agent_result = await run_receipt_metadata_finder(
                graph=graph,
                state_holder=state_holder,
                image_id=image_id,
                receipt_id=receipt_id,
                receipt_lines=receipt_lines,  # Pass pre-loaded lines (avoids DynamoDB read)
                receipt_words=receipt_words,  # Pass pre-loaded words (avoids DynamoDB read)
            )

            # Check if agent found any metadata
            if not agent_result or not agent_result.get("found"):
                _log(
                    f"Agent did not find metadata for {image_id}#{receipt_id}"
                )
                return {
                    "success": False,
                    "reason": "agent_no_results",
                    "fields_found": [],
                    "reasoning": (
                        agent_result.get("reasoning") if agent_result else None
                    ),
                }

            # Update ReceiptMetadata with found fields
            fields_found = agent_result.get("fields_found", [])
            _log(
                f"Agent found {len(fields_found)} fields: {', '.join(fields_found)} "
                f"(confidence: {agent_result.get('confidence', 0):.0%})"
            )

            # Reload metadata to get latest version
            updated_metadata = self.dynamo.get_receipt_metadata(
                image_id, receipt_id
            )

            # Update missing fields
            updated_fields = []
            if "place_id" in fields_found and agent_result.get("place_id"):
                if not updated_metadata.place_id:
                    updated_metadata.place_id = agent_result["place_id"]
                    updated_fields.append("place_id")

            if "merchant_name" in fields_found and agent_result.get(
                "merchant_name"
            ):
                if not updated_metadata.merchant_name:
                    updated_metadata.merchant_name = agent_result[
                        "merchant_name"
                    ]
                    updated_fields.append("merchant_name")

            if "address" in fields_found and agent_result.get("address"):
                if not updated_metadata.address:
                    updated_metadata.address = agent_result["address"]
                    updated_fields.append("address")

            if "phone_number" in fields_found and agent_result.get(
                "phone_number"
            ):
                if not updated_metadata.phone_number:
                    updated_metadata.phone_number = agent_result[
                        "phone_number"
                    ]
                    updated_fields.append("phone_number")

            # Update matched_fields to include newly found fields
            if updated_fields:
                current_matched = set(updated_metadata.matched_fields or [])
                # Map field names to matched_fields format
                field_mapping = {
                    "merchant_name": "name",
                    "phone_number": "phone",
                    "address": "address",
                    "place_id": "place_id",
                }
                for field in updated_fields:
                    matched_field = field_mapping.get(field, field)
                    current_matched.add(matched_field)
                updated_metadata.matched_fields = list(current_matched)

            # Save updates if any fields were updated
            if updated_fields:
                self.dynamo.update_receipt_metadata(updated_metadata)
                _log(
                    f"✅ Updated ReceiptMetadata with {len(updated_fields)} fields: "
                    f"{', '.join(updated_fields)}"
                )
            else:
                _log("No fields updated (all found fields already present)")

            return {
                "success": True,
                "reason": "fields_updated",
                "fields_found": fields_found,
                "fields_updated": updated_fields,
                "metadata": updated_metadata,
                "confidence": agent_result.get("confidence", 0.0),
            }

        except Exception as e:
            _log(f"⚠️ Metadata finder failed: {e}")
            logger.error(f"Metadata finder failed: {e}", exc_info=True)
            return {
                "success": False,
                "reason": "error",
                "error": str(e),
                "fields_found": [],
            }

    async def _setup_chromadb(self) -> tuple[Optional[Any], Optional[Any]]:
        """
        Setup ChromaDB client and embedding function.

        Returns:
            Tuple of (chroma_client, embed_fn) or (None, None) if unavailable
        """
        try:
            import tempfile
            from pathlib import Path

            import boto3

            # Try EFS first, fallback to S3 download, then HTTP
            chroma_client = None
            embed_fn = None

            # Check for EFS
            efs_root = os.environ.get("CHROMA_ROOT")
            storage_mode = os.environ.get(
                "CHROMADB_STORAGE_MODE", "auto"
            ).lower()

            if storage_mode == "efs" or (
                storage_mode == "auto"
                and efs_root
                and efs_root != "/tmp/chroma"
            ):
                try:
                    from .efs_snapshot_manager import UploadEFSSnapshotManager

                    _log("Using EFS for ChromaDB snapshot access")
                    efs_manager = UploadEFSSnapshotManager("lines", logger)
                    snapshot_info = efs_manager.get_snapshot_for_chromadb()
                    if snapshot_info:
                        chroma_client = ChromaClient(
                            persist_directory=snapshot_info["local_path"],
                            mode="read",
                        )
                        _log(f"Created ChromaDB client from EFS snapshot")
                except Exception as e:
                    _log(f"EFS access failed: {e}, falling back to S3")

            # Fallback to S3 download
            if not chroma_client:
                try:
                    _log("Downloading ChromaDB snapshot from S3...")
                    s3 = boto3.client("s3")
                    pointer_key = "lines/snapshot/latest-pointer.txt"

                    response = s3.get_object(
                        Bucket=self.chromadb_bucket, Key=pointer_key
                    )
                    timestamp = response["Body"].read().decode().strip()
                    _log(f"Latest snapshot timestamp: {timestamp}")

                    snapshot_dir = tempfile.mkdtemp(prefix="chroma_snapshot_")
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
                            if key.endswith(".snapshot_hash"):
                                continue
                            relative_path = key[len(prefix) :]
                            if not relative_path:
                                continue
                            local_path = Path(snapshot_dir) / relative_path
                            local_path.parent.mkdir(
                                parents=True, exist_ok=True
                            )
                            s3.download_file(
                                self.chromadb_bucket, key, str(local_path)
                            )
                            downloaded_files += 1

                    if downloaded_files > 0:
                        chroma_client = ChromaClient(
                            persist_directory=snapshot_dir,
                            mode="read",
                        )
                        _log(f"Downloaded {downloaded_files} snapshot files")
                except Exception as e:
                    _log(f"S3 download failed: {e}, trying HTTP")

            # Fallback to HTTP
            if not chroma_client and self.chroma_http_endpoint:
                try:
                    chroma_client = ChromaClient(
                        mode="read", http_url=self.chroma_http_endpoint
                    )
                    _log(
                        f"Using HTTP ChromaDB endpoint: {self.chroma_http_endpoint}"
                    )
                except Exception as e:
                    _log(f"HTTP ChromaDB failed: {e}")

            # Create embedding function
            if chroma_client and self.openai_api_key:

                def _embed_texts(texts):
                    if not texts:
                        return []
                    from receipt_label.utils.client_manager import ClientConfig, ClientManager

                    # Create a ClientManager with the specific API key instead of using environment
                    config = ClientConfig(
                        dynamo_table=os.environ.get("DYNAMODB_TABLE_NAME", ""),
                        openai_api_key=self.openai_api_key,
                        track_usage=False  # Disable usage tracking for embeddings
                    )
                    client_manager = ClientManager(config)
                    openai_client = client_manager.openai
                    
                    resp = openai_client.embeddings.create(
                        model="text-embedding-3-small", input=list(texts)
                    )
                    return [d.embedding for d in resp.data]

                embed_fn = _embed_texts

            return chroma_client, embed_fn

        except Exception as e:
            _log(f"Failed to setup ChromaDB: {e}")
            logger.error(f"ChromaDB setup failed: {e}", exc_info=True)
            return None, None
