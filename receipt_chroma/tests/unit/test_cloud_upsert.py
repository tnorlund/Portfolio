# ruff: noqa: E402
"""Unit tests for direct Chroma Cloud upsert from the ingest path.

Covers the success path, non-fatal partial batch failure, the disabled
no-op, and batching against Chroma Cloud's 300-record ceiling.
"""

# IMPORTANT: Clear Chroma Cloud env vars BEFORE importing chromadb
# ChromaDB may check credentials at import time
import os

_CHROMA_ENV_VARS = [
    "CHROMA_API_KEY",
    "CHROMA_CLOUD_API_KEY",
    "CHROMA_CLOUD_TENANT",
    "CHROMA_CLOUD_DATABASE",
    "CHROMA_CLOUD_ENABLED",
]
for _var in _CHROMA_ENV_VARS:
    os.environ.pop(_var, None)

from unittest.mock import MagicMock, patch

import pytest

from receipt_chroma.compaction.dual_write import CloudConfig
from receipt_chroma.embedding.cloud_upsert import (
    UPSERT_BATCH_SIZE,
    CloudUpsertResult,
    upsert_payload_to_cloud,
)

_CLIENT_FACTORY = (
    "receipt_chroma.embedding.cloud_upsert._create_cloud_client_for_sync"
)

CLOUD_CFG = {
    "api_key": "test-key",
    "tenant": "test-tenant",
    "database": "receipt_test",
}


def make_payload(count: int) -> dict:
    """Build a payload shaped like build_row_payload/build_word_payload."""
    return {
        "ids": [f"id-{i}" for i in range(count)],
        "embeddings": [[float(i), 0.5] for i in range(count)],
        "documents": [f"doc {i}" for i in range(count)],
        "metadatas": [{"receipt_id": i} for i in range(count)],
    }


# =============================================================================
# Success
# =============================================================================


class TestUpsertSuccess:
    """Happy-path upserts."""

    def test_single_batch_upserts_every_record(self):
        """A small payload is written in one batch."""
        client = MagicMock()
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(3), "words", CLOUD_CFG
            )

        assert result.success is True
        assert result.enabled is True
        assert result.attempted == 3
        assert result.upserted == 3
        assert result.batches == 1
        assert result.failed_batches == 0
        assert result.error is None
        client.close.assert_called_once()

        kwargs = client.upsert.call_args.kwargs
        assert kwargs["collection_name"] == "words"
        assert kwargs["ids"] == ["id-0", "id-1", "id-2"]
        assert kwargs["embeddings"] == [[0.0, 0.5], [1.0, 0.5], [2.0, 0.5]]
        assert kwargs["documents"] == ["doc 0", "doc 1", "doc 2"]
        assert kwargs["metadatas"] == [
            {"receipt_id": 0},
            {"receipt_id": 1},
            {"receipt_id": 2},
        ]

    def test_accepts_cloud_config_dataclass(self):
        """CloudConfig is accepted alongside the ingest path's dict form."""
        client = MagicMock()
        config = CloudConfig(
            api_key="k", tenant="t", database="d", enabled=True
        )
        with patch(_CLIENT_FACTORY, return_value=client) as factory:
            result = upsert_payload_to_cloud(make_payload(1), "lines", config)

        assert result.success is True
        assert factory.call_args.args[0] is config

    def test_empty_payload_creates_no_client(self):
        """Nothing to write means no cloud connection."""
        with patch(_CLIENT_FACTORY) as factory:
            result = upsert_payload_to_cloud({"ids": []}, "words", CLOUD_CFG)

        factory.assert_not_called()
        assert result.enabled is True
        assert result.attempted == 0
        assert result.success is True

    def test_payload_without_documents_omits_the_column(self):
        """A missing payload column is passed through as None."""
        client = MagicMock()
        payload = {
            "ids": ["a"],
            "embeddings": [[0.1]],
            "metadatas": [{"k": "v"}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        assert client.upsert.call_args.kwargs["documents"] is None

    def test_oversized_metadata_keys_are_dropped(self):
        """Keys past Chroma Cloud's 36-byte limit are stripped, not fatal."""
        client = MagicMock()
        long_key = "label_" + "x" * 60
        payload = {
            "ids": ["a"],
            "embeddings": [[0.1]],
            "metadatas": [{long_key: 1, "keep": 2}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        assert result.success is True
        assert client.upsert.call_args.kwargs["metadatas"] == [{"keep": 2}]


# =============================================================================
# Batching
# =============================================================================


class TestUpsertBatching:
    """Chroma Cloud rejects upserts above 300 records."""

    def test_batch_size_default_stays_under_the_cloud_limit(self):
        assert UPSERT_BATCH_SIZE <= 300

    def test_large_payload_is_split_into_capped_batches(self):
        """601 records become three batches of at most 250."""
        client = MagicMock()
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(601), "words", CLOUD_CFG
            )

        assert result.batches == 3
        assert result.upserted == 601
        sizes = [
            len(call.kwargs["ids"]) for call in client.upsert.call_args_list
        ]
        assert sizes == [250, 250, 101]
        assert max(sizes) <= UPSERT_BATCH_SIZE

    def test_requested_batch_size_cannot_exceed_the_cap(self):
        """An oversized batch_size is clamped rather than trusted."""
        client = MagicMock()
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(300),
                "words",
                CLOUD_CFG,
                batch_size=1000,
            )

        assert result.batches == 2
        sizes = [
            len(call.kwargs["ids"]) for call in client.upsert.call_args_list
        ]
        assert sizes == [250, 50]


# =============================================================================
# Failure is non-fatal
# =============================================================================


class TestUpsertFailureIsNonFatal:
    """Cloud is best effort; compaction remains the durable path."""

    def test_partial_batch_failure_does_not_raise(self):
        """One failing batch is reported, the rest still land."""
        client = MagicMock()
        client.upsert.side_effect = [
            None,
            RuntimeError("cloud 503"),
            None,
        ]
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(601), "words", CLOUD_CFG
            )

        assert result.success is False
        assert result.batches == 3
        assert result.failed_batches == 1
        assert result.upserted == 351
        assert result.attempted == 601
        assert result.error == "RuntimeError: cloud 503"
        client.close.assert_called_once()

    def test_every_batch_failing_does_not_raise(self):
        client = MagicMock()
        client.upsert.side_effect = RuntimeError("cloud down")
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(10), "lines", CLOUD_CFG
            )

        assert result.success is False
        assert result.upserted == 0
        assert result.failed_batches == 1

    def test_client_creation_failure_does_not_raise(self):
        """An unreachable cloud is reported, not propagated."""
        with patch(_CLIENT_FACTORY, side_effect=ValueError("bad tenant")):
            result = upsert_payload_to_cloud(
                make_payload(5), "words", CLOUD_CFG
            )

        assert result.success is False
        assert result.error == "ValueError: bad tenant"
        assert result.upserted == 0
        assert result.batches == 0

    def test_close_failure_does_not_mask_success(self):
        client = MagicMock()
        client.close.side_effect = RuntimeError("close failed")
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(2), "words", CLOUD_CFG
            )

        assert result.success is True
        assert result.upserted == 2


# =============================================================================
# Cloud disabled
# =============================================================================


class TestCloudDisabled:
    """Absent or incomplete config means the call is a no-op."""

    @pytest.mark.parametrize(
        "config",
        [
            None,
            {},
            {"api_key": "k"},
            {"api_key": "k", "tenant": "t"},
            {"api_key": "", "tenant": "t", "database": "d"},
            {"api_key": "k", "tenant": "  ", "database": "d"},
        ],
        ids=[
            "none",
            "empty",
            "api_key_only",
            "missing_database",
            "blank_api_key",
            "blank_tenant",
        ],
    )
    def test_incomplete_config_is_a_noop(self, config):
        with patch(_CLIENT_FACTORY) as factory:
            result = upsert_payload_to_cloud(make_payload(5), "words", config)

        factory.assert_not_called()
        assert result.enabled is False
        assert result.attempted == 0
        assert result.upserted == 0
        assert result.error is None

    def test_disabled_cloud_config_dataclass_is_a_noop(self):
        config = CloudConfig(
            api_key="k", tenant="t", database="d", enabled=False
        )
        with patch(_CLIENT_FACTORY) as factory:
            result = upsert_payload_to_cloud(make_payload(5), "words", config)

        factory.assert_not_called()
        assert result.enabled is False


# =============================================================================
# Result
# =============================================================================


class TestCloudUpsertResult:
    """Result reporting."""

    def test_success_requires_no_error_and_no_failed_batch(self):
        assert CloudUpsertResult(collection="words").success is True
        assert (
            CloudUpsertResult(collection="words", failed_batches=1).success
            is False
        )
        assert (
            CloudUpsertResult(collection="words", error="boom").success
            is False
        )

    def test_to_dict_is_json_serializable(self):
        import json

        result = CloudUpsertResult(
            collection="words",
            attempted=10,
            upserted=10,
            batches=1,
            duration_seconds=1.23456,
        )
        payload = json.loads(json.dumps(result.to_dict()))
        assert payload["collection"] == "words"
        assert payload["duration_seconds"] == 1.235
        assert payload["success"] is True
