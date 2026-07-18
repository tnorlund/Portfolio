"""Contract tests for the stable ``receipt_chroma`` public API."""

import ast
from pathlib import Path

from receipt_chroma import ChromaClient, LockManager
from receipt_chroma.compaction import (
    CloudConfig,
    CollectionUpdateResult,
    sync_collection_to_cloud,
)
from receipt_chroma.compaction.dual_write import (
    CloudConfig as InternalCloudConfig,
)
from receipt_chroma.compaction.dual_write import (
    sync_collection_to_cloud as internal_sync_collection_to_cloud,
)
from receipt_chroma.compaction.models import (
    CollectionUpdateResult as InternalCollectionUpdateResult,
)
from receipt_chroma.data.chroma_client import (
    ChromaClient as InternalChromaClient,
)
from receipt_chroma.embedding import (
    EmbeddingConfig,
    build_words_payload,
    create_compaction_run,
    create_embeddings_and_compaction_run,
    download_and_embed_parallel,
    upload_lines_delta,
    upload_words_delta,
)
from receipt_chroma.embedding.formatting import build_receipt_rows
from receipt_chroma.embedding.formatting.receipt_rows import (
    build_receipt_rows as internal_build_receipt_rows,
)
from receipt_chroma.embedding.orchestration import (
    EmbeddingConfig as InternalEmbeddingConfig,
)
from receipt_chroma.embedding.orchestration import (
    build_words_payload as internal_build_words_payload,
)
from receipt_chroma.embedding.orchestration import (
    create_compaction_run as internal_create_compaction_run,
)
from receipt_chroma.embedding.orchestration import (
    create_embeddings_and_compaction_run as internal_create_embeddings,
)
from receipt_chroma.embedding.orchestration import (
    download_and_embed_parallel as internal_download_and_embed_parallel,
)
from receipt_chroma.embedding.orchestration import (
    upload_lines_delta as internal_upload_lines_delta,
)
from receipt_chroma.embedding.orchestration import (
    upload_words_delta as internal_upload_words_delta,
)
from receipt_chroma.lock_manager import LockManager as InternalLockManager
from receipt_chroma.s3 import upload_snapshot_with_hash
from receipt_chroma.s3.helpers import (
    upload_snapshot_with_hash as internal_upload_snapshot_with_hash,
)


def test_public_client_exports_match_implementations() -> None:
    """The package root remains the supported client import location."""
    assert ChromaClient is InternalChromaClient
    assert LockManager is InternalLockManager


def test_compaction_facade_exports_match_implementations() -> None:
    """Runtime callers can use the compaction package as one stable facade."""
    assert CloudConfig is InternalCloudConfig
    assert CollectionUpdateResult is InternalCollectionUpdateResult
    assert sync_collection_to_cloud is internal_sync_collection_to_cloud


def test_embedding_facade_exports_orchestration_stages() -> None:
    """Intentional orchestration stages are exposed by the public facade."""
    assert EmbeddingConfig is InternalEmbeddingConfig
    assert build_words_payload is internal_build_words_payload
    assert create_compaction_run is internal_create_compaction_run
    assert create_embeddings_and_compaction_run is internal_create_embeddings
    assert download_and_embed_parallel is internal_download_and_embed_parallel
    assert upload_lines_delta is internal_upload_lines_delta
    assert upload_words_delta is internal_upload_words_delta


def test_formatting_facade_exports_receipt_row_builder() -> None:
    """Upload code can materialize rows without importing an internal module."""

    assert build_receipt_rows is internal_build_receipt_rows


def test_s3_facade_exports_hash_upload() -> None:
    """Snapshot hash uploads do not require importing the helpers module."""
    assert upload_snapshot_with_hash is internal_upload_snapshot_with_hash


def test_external_callers_use_public_client_import() -> None:
    """Repository callers must not depend on the private client module."""
    repository_root = Path(__file__).resolve().parents[3]
    private_modules = {
        "receipt_chroma.data",
        "receipt_chroma.data.chroma_client",
    }
    violations = []

    for path in repository_root.rglob("*.py"):
        relative_path = path.relative_to(repository_root)
        if relative_path.parts[0] == "receipt_chroma":
            continue
        if any(part.startswith(".venv") for part in relative_path.parts):
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module in private_modules
            ):
                imported_names = {alias.name for alias in node.names}
                if "ChromaClient" in imported_names:
                    violations.append(f"{relative_path}:{node.lineno}")

    assert not violations, (
        "Import ChromaClient from receipt_chroma, not its private module: "
        + ", ".join(violations)
    )


def test_external_runtime_callers_use_public_facades() -> None:
    """Runtime code must not bypass the supported package facades."""
    repository_root = Path(__file__).resolve().parents[3]
    internal_modules = {
        "receipt_chroma.compaction.dual_write",
        "receipt_chroma.compaction.models",
        "receipt_chroma.embedding.formatting.line_format",
        "receipt_chroma.embedding.formatting.receipt_rows",
        "receipt_chroma.embedding.formatting.word_format",
        "receipt_chroma.embedding.openai.realtime",
        "receipt_chroma.embedding.orchestration",
        "receipt_chroma.embedding.utils.normalize",
        "receipt_chroma.s3.helpers",
    }
    violations = []

    for path in repository_root.rglob("*.py"):
        relative_path = path.relative_to(repository_root)
        if relative_path.parts[0] == "receipt_chroma":
            continue
        if "tests" in relative_path.parts:
            continue
        if any(part.startswith(".venv") for part in relative_path.parts):
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module in internal_modules
            ):
                violations.append(f"{relative_path}:{node.lineno}")

    assert not violations, (
        "Import receipt_chroma APIs from their public package facade: "
        + ", ".join(violations)
    )
