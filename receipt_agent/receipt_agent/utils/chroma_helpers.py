"""
Shared utilities for ChromaDB initialization and management.

This module provides centralized functions for loading ChromaDB snapshots
from S3 and initializing clients, reducing code duplication across workflows.
"""

import logging
import os
import tempfile
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)


def load_dual_chroma_from_s3(
    chromadb_bucket: str,
    base_chroma_path: Optional[str] = None,
    verify_integrity: bool = False,
) -> Tuple[Any, Callable[[list[str]], list[list[float]]]]:
    """
    Load ChromaDB snapshots for both lines and words collections from S3.

    Downloads snapshots to separate directories (lines/ and words/) and sets
    environment variables for DualChromaClient to use them.

    Args:
        chromadb_bucket: S3 bucket name containing ChromaDB snapshots
        base_chroma_path: Base directory for ChromaDB persistence.
                         Defaults to RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY or /tmp/chromadb
        verify_integrity: Whether to verify snapshot integrity (slower but safer)

    Returns:
        Tuple of (chroma_client, embed_fn)

    Raises:
        RuntimeError: If snapshot download or client creation fails
    """
    from receipt_chroma.s3 import download_snapshot_atomic

    if base_chroma_path is None:
        base_chroma_path = os.environ.get(
            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY",
            os.path.join(tempfile.gettempdir(), "chromadb"),
        )

    lines_path = os.path.join(base_chroma_path, "lines")
    words_path = os.path.join(base_chroma_path, "words")

    # Check if already cached
    lines_db_file = os.path.join(lines_path, "chroma.sqlite3")
    words_db_file = os.path.join(words_path, "chroma.sqlite3")

    if not os.path.exists(lines_db_file) or not os.path.exists(words_db_file):
        # Download lines collection to separate directory
        if not os.path.exists(lines_db_file):
            logger.info(
                f"Downloading ChromaDB lines snapshot from s3://{chromadb_bucket}/lines/"
            )
            lines_result = download_snapshot_atomic(
                bucket=chromadb_bucket,
                collection="lines",
                local_path=lines_path,
                verify_integrity=verify_integrity,
            )

            if lines_result.get("status") != "downloaded":
                raise RuntimeError(
                    f"Failed to download ChromaDB lines snapshot: {lines_result.get('error')}"
                )

            logger.info(
                f"ChromaDB lines snapshot downloaded: version={lines_result.get('version_id')}"
            )
        else:
            logger.info(f"ChromaDB lines already cached at {lines_path}")

        # Download words collection to separate directory
        if not os.path.exists(words_db_file):
            logger.info(
                f"Downloading ChromaDB words snapshot from s3://{chromadb_bucket}/words/"
            )
            words_result = download_snapshot_atomic(
                bucket=chromadb_bucket,
                collection="words",
                local_path=words_path,
                verify_integrity=verify_integrity,
            )

            if words_result.get("status") != "downloaded":
                raise RuntimeError(
                    f"Failed to download ChromaDB words snapshot: {words_result.get('error')}"
                )

            logger.info(
                f"ChromaDB words snapshot downloaded: version={words_result.get('version_id')}"
            )
        else:
            logger.info(f"ChromaDB words already cached at {words_path}")
    else:
        logger.info(f"ChromaDB already cached at {base_chroma_path}")

    # Set environment variables for DualChromaClient (separate directories)
    os.environ["RECEIPT_AGENT_CHROMA_LINES_DIRECTORY"] = lines_path
    os.environ["RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"] = words_path

    # Create clients using the receipt_agent factory
    from receipt_agent.clients.factory import (
        create_chroma_client,
        create_embed_fn,
    )
    from receipt_agent.config.settings import get_settings

    settings = get_settings()

    # Verify OpenAI API key is available for embeddings
    if not settings.openai_api_key:
        logger.warning(
            "RECEIPT_AGENT_OPENAI_API_KEY not set - embeddings may fail"
        )
    else:
        logger.info("OpenAI API key available for embeddings")

    chroma_client = create_chroma_client(settings=settings)
    embed_fn = create_embed_fn(settings=settings)

    logger.info("ChromaDB and embeddings loaded and cached")
    return chroma_client, embed_fn
