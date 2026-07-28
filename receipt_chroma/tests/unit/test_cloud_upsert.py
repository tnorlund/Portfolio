# ruff: noqa: E402
"""Unit tests for direct Chroma Cloud upsert from the ingest path.

Covers batching against Chroma Cloud's 300-record ceiling, the disabled
no-op, non-fatal failure handling, the wall-clock budget, and the two
Chroma behaviors this module exists to handle: metadata merges on write
(so cleared keys need explicit ``None`` tombstones) and empty metadata
dicts being rejected outright.

The tombstone and empty-metadata cases are also exercised against a real
local Chroma, not just a mock, because both were originally missed by
mock-only coverage.
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

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from chromadb.errors import (
    BatchSizeExceededError,
    DuplicateIDError,
    InvalidArgumentError,
    QuotaError,
    RateLimitError,
)

from receipt_chroma.compaction.dual_write import CloudConfig
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.embedding.cloud_upsert import (
    MAX_DOCUMENT_BYTES,
    MAX_ID_BYTES,
    MAX_METADATA_KEYS,
    MAX_METADATA_VALUE_BYTES,
    TOMBSTONE_KEYS,
    UPSERT_BATCH_SIZE,
    CloudUpsertResult,
    upsert_payload_to_cloud,
)

_CLIENT_FACTORY = "receipt_chroma.embedding.cloud_upsert._create_cloud_client"

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
        "metadatas": [
            {"image_id": "img", "receipt_id": i} for i in range(count)
        ],
    }


def sent_metadatas(client) -> list:
    """Flatten the metadatas from every upsert call on a mock client."""
    out = []
    for call in client.upsert.call_args_list:
        out.extend(call.kwargs["metadatas"])
    return out


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
        assert result.dropped == 0
        assert result.error is None
        client.close.assert_called_once()

        kwargs = client.upsert.call_args.kwargs
        assert kwargs["collection_name"] == "words"
        assert kwargs["ids"] == ["id-0", "id-1", "id-2"]
        assert kwargs["embeddings"] == [[0.0, 0.5], [1.0, 0.5], [2.0, 0.5]]
        assert kwargs["documents"] == ["doc 0", "doc 1", "doc 2"]

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
            "metadatas": [{"image_id": "img"}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        assert client.upsert.call_args.kwargs["documents"] is None

    def test_request_timeout_is_passed_to_the_client(self):
        """The Cloud session must not be left unbounded."""
        client = MagicMock()
        with patch(_CLIENT_FACTORY, return_value=client) as factory:
            upsert_payload_to_cloud(
                make_payload(1),
                "words",
                CLOUD_CFG,
                request_timeout=(5.0, 15.0),
            )

        assert factory.call_args.args[2] == (5.0, 15.0)


# =============================================================================
# Tombstones (metadata merges on write)
# =============================================================================


class TestTombstones:
    """Chroma merges metadata, so cleared keys need explicit None."""

    def test_absent_optional_keys_are_sent_as_none(self):
        client = MagicMock()
        payload = {
            "ids": ["w1"],
            "embeddings": [[0.1]],
            "metadatas": [{"image_id": "img", "label_status": "unvalidated"}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        (metadata,) = sent_metadatas(client)
        for key in TOMBSTONE_KEYS["words"]:
            assert key in metadata, f"{key} must be tombstoned"
            assert metadata[key] is None
        assert metadata["label_status"] == "unvalidated"

    def test_present_optional_keys_keep_their_value(self):
        client = MagicMock()
        payload = {
            "ids": ["w1"],
            "embeddings": [[0.1]],
            "metadatas": [
                {"image_id": "img", "valid_labels_array": ["GRAND_TOTAL"]}
            ],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        (metadata,) = sent_metadatas(client)
        assert metadata["valid_labels_array"] == ["GRAND_TOTAL"]
        assert metadata["label_confidence"] is None

    def test_lines_use_the_line_tombstone_set(self):
        client = MagicMock()
        payload = {
            "ids": ["l1"],
            "embeddings": [[0.1]],
            "metadatas": [{"image_id": "img", "text": "TOTAL"}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            upsert_payload_to_cloud(payload, "lines", CLOUD_CFG)

        (metadata,) = sent_metadatas(client)
        assert metadata["section_label"] is None
        assert metadata["anchor_phone"] is None

    def test_legacy_neighbour_fields_are_tombstoned(self):
        """Row ids reuse the primary line id, so pre-row records persist."""
        client = MagicMock()
        payload = {
            "ids": ["l1"],
            "embeddings": [[0.1]],
            "metadatas": [{"image_id": "img", "text": "TOTAL"}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            upsert_payload_to_cloud(payload, "lines", CLOUD_CFG)

        (metadata,) = sent_metadatas(client)
        assert metadata["prev_line"] is None
        assert metadata["next_line"] is None

    def test_unknown_collection_adds_no_tombstones(self):
        client = MagicMock()
        payload = {
            "ids": ["x"],
            "embeddings": [[0.1]],
            "metadatas": [{"image_id": "img"}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            upsert_payload_to_cloud(payload, "other", CLOUD_CFG)

        assert sent_metadatas(client) == [{"image_id": "img"}]


class TestTombstonesAgainstRealChroma:
    """The merge semantics this module compensates for, verified for real."""

    def _client_factory(self, persist_dir):
        def factory(_config, _collection, _timeout):
            return ChromaClient(
                persist_directory=persist_dir,
                mode="write",
                metadata_only=True,
            )

        return factory

    def _read(self, persist_dir, record_id):
        client = ChromaClient(
            persist_directory=persist_dir, mode="write", metadata_only=True
        )
        try:
            got = client.get_collection("words").get(
                ids=[record_id], include=["metadatas"]
            )
            return got["metadatas"][0]
        finally:
            client.close()

    def test_reingest_clears_a_removed_label_array(self, tmp_path):
        """Without tombstones the old array survives the re-upsert."""
        persist_dir = str(tmp_path / "chroma")
        factory = self._client_factory(persist_dir)

        labelled = {
            "ids": ["w1"],
            "embeddings": [[0.1, 0.2]],
            "documents": ["TOTAL"],
            "metadatas": [
                {
                    "image_id": "img",
                    "text": "TOTAL",
                    "label_status": "validated",
                    "valid_labels_array": ["GRAND_TOTAL"],
                }
            ],
        }
        with patch(_CLIENT_FACTORY, factory):
            upsert_payload_to_cloud(labelled, "words", CLOUD_CFG)
        assert self._read(persist_dir, "w1")["valid_labels_array"] == [
            "GRAND_TOTAL"
        ]

        # Label removed upstream: the builder pops the key entirely.
        unlabelled = {
            "ids": ["w1"],
            "embeddings": [[0.1, 0.2]],
            "documents": ["TOTAL"],
            "metadatas": [
                {
                    "image_id": "img",
                    "text": "TOTAL",
                    "label_status": "unvalidated",
                }
            ],
        }
        with patch(_CLIENT_FACTORY, factory):
            result = upsert_payload_to_cloud(unlabelled, "words", CLOUD_CFG)

        assert result.success is True
        metadata = self._read(persist_dir, "w1")
        assert "valid_labels_array" not in metadata
        assert metadata["label_status"] == "unvalidated"

    def test_empty_metadata_record_is_dropped_not_batch_failing(
        self, tmp_path
    ):
        """Chroma rejects empty metadata; one bad record must not fail 250."""
        persist_dir = str(tmp_path / "chroma")
        oversized_key = "label_" + "x" * 60
        payload = {
            "ids": ["good", "bad"],
            "embeddings": [[0.1, 0.2], [0.3, 0.4]],
            "documents": ["ok", "dropped"],
            "metadatas": [
                {"image_id": "img", "text": "OK"},
                {oversized_key: 1},
            ],
        }
        with patch(_CLIENT_FACTORY, self._client_factory(persist_dir)):
            result = upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        assert result.upserted == 1
        assert result.dropped == 1
        assert result.drop_reasons == {"empty_metadata": 1}
        assert result.failed_batches == 0
        assert result.success is False
        assert self._read(persist_dir, "good")["text"] == "OK"


# =============================================================================
# Cloud limits
# =============================================================================


class TestCloudLimits:
    """Quotas from docs.trychroma.com/cloud/quotas-limits."""

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

    def test_requested_batch_size_cannot_exceed_the_cap(self):
        client = MagicMock()
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(300), "words", CLOUD_CFG, batch_size=1000
            )

        assert result.batches == 2
        sizes = [
            len(call.kwargs["ids"]) for call in client.upsert.call_args_list
        ]
        assert sizes == [250, 50]

    def test_oversized_metadata_value_is_truncated(self):
        client = MagicMock()
        payload = {
            "ids": ["a"],
            "embeddings": [[0.1]],
            "metadatas": [{"image_id": "img", "text": "x" * 20000}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        (metadata,) = sent_metadatas(client)
        assert len(metadata["text"].encode("utf-8")) <= (
            MAX_METADATA_VALUE_BYTES
        )
        assert result.truncated == 1
        assert result.upserted == 1

    def test_oversized_document_is_truncated(self):
        client = MagicMock()
        payload = {
            "ids": ["a"],
            "embeddings": [[0.1]],
            "documents": ["y" * (MAX_DOCUMENT_BYTES + 500)],
            "metadatas": [{"image_id": "img"}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        document = client.upsert.call_args.kwargs["documents"][0]
        assert len(document.encode("utf-8")) <= MAX_DOCUMENT_BYTES
        assert result.truncated == 1

    def test_too_many_keys_are_truncated_keeping_identity(self):
        client = MagicMock()
        metadata = {f"extra_{i}": i for i in range(60)}
        metadata.update({"image_id": "img", "receipt_id": 1, "word_id": 2})
        payload = {
            "ids": ["a"],
            "embeddings": [[0.1]],
            "metadatas": [metadata],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        (sent,) = sent_metadatas(client)
        assert len(sent) <= MAX_METADATA_KEYS
        assert sent["image_id"] == "img"
        assert sent["receipt_id"] == 1
        assert sent["word_id"] == 2
        assert result.truncated == 1

    def test_oversized_id_is_dropped_not_truncated(self):
        """Truncating an id would corrupt identity."""
        client = MagicMock()
        payload = {
            "ids": ["ok", "z" * (MAX_ID_BYTES + 1)],
            "embeddings": [[0.1], [0.2]],
            "metadatas": [{"image_id": "img"}, {"image_id": "img"}],
        }
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        assert result.dropped == 1
        assert result.drop_reasons == {"id_too_long": 1}
        assert client.upsert.call_args.kwargs["ids"] == ["ok"]

    @pytest.mark.parametrize(
        "column", ["embeddings", "documents", "metadatas"]
    )
    def test_mismatched_column_length_writes_nothing(self, column):
        """Misaligned columns would attach metadata to the wrong record."""
        payload = make_payload(3)
        payload[column] = payload[column][:2]

        with patch(_CLIENT_FACTORY) as factory:
            result = upsert_payload_to_cloud(payload, "words", CLOUD_CFG)

        factory.assert_not_called()
        assert result.success is False
        assert result.upserted == 0
        assert "ColumnLengthMismatch" in result.error


# =============================================================================
# Failure is non-fatal
# =============================================================================


class TestUpsertFailureIsNonFatal:
    """Cloud is best effort; the S3 delta remains the durable path."""

    def test_batch_value_error_falls_back_to_per_record(self):
        """One rejected record must not cost the rest of its batch."""
        client = MagicMock()
        rejected = []

        def upsert(**kwargs):
            ids = kwargs["ids"]
            if len(ids) > 1:
                raise ValueError("Expected metadata to be a non-empty dict")
            if ids == ["id-1"]:
                rejected.append(ids)
                raise ValueError("bad record")

        client.upsert.side_effect = upsert
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(3), "words", CLOUD_CFG
            )

        assert result.upserted == 2
        assert result.dropped == 1
        assert result.drop_reasons == {"rejected": 1}
        assert result.failed_batches == 0
        assert rejected == [["id-1"]]

    def test_non_value_error_fails_the_batch_without_raising(self):
        client = MagicMock()
        client.upsert.side_effect = RuntimeError("cloud 503")
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(10), "lines", CLOUD_CFG
            )

        assert result.success is False
        assert result.failed_batches == 1
        assert result.upserted == 0
        assert result.error == "RuntimeError: cloud 503"

    def test_client_creation_failure_does_not_raise(self):
        with patch(_CLIENT_FACTORY, side_effect=ValueError("bad tenant")):
            result = upsert_payload_to_cloud(
                make_payload(5), "words", CLOUD_CFG
            )

        assert result.success is False
        assert result.error == "ValueError: bad tenant"
        assert result.upserted == 0

    def test_close_failure_does_not_mask_success(self):
        client = MagicMock()
        client.close.side_effect = RuntimeError("close failed")
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(2), "words", CLOUD_CFG
            )

        assert result.success is True
        assert result.upserted == 2


class TestValidationErrorFallback:
    """Cloud maps validation 400s to ChromaError, not ValueError."""

    @pytest.mark.parametrize(
        "error",
        [
            ValueError("Expected metadata to be a non-empty dict"),
            InvalidArgumentError("metadata value too large"),
            BatchSizeExceededError("batch too large"),
            DuplicateIDError("duplicate id"),
        ],
        ids=[
            "value_error",
            "invalid_argument",
            "batch_size",
            "duplicate_id",
        ],
    )
    def test_validation_errors_trigger_per_record_fallback(self, error):
        client = MagicMock()
        seen = []

        def upsert(**kwargs):
            seen.append(len(kwargs["ids"]))
            if len(kwargs["ids"]) > 1:
                raise error

        client.upsert.side_effect = upsert
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(3), "words", CLOUD_CFG
            )

        assert seen == [3, 1, 1, 1]
        assert result.upserted == 3

    @pytest.mark.parametrize(
        "error",
        [RateLimitError("429"), QuotaError("quota exhausted")],
        ids=["rate_limit", "quota"],
    )
    def test_rate_and_quota_errors_do_not_fan_out(self, error):
        """250 retries would make a rate limit strictly worse."""
        client = MagicMock()
        client.upsert.side_effect = error
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(3), "words", CLOUD_CFG
            )

        assert client.upsert.call_count == 1
        assert result.failed_batches == 1
        assert result.upserted == 0


class TestDeadline:
    """A stalled Cloud must not eat the caller's remaining runtime."""

    def test_slow_batches_stop_at_the_deadline(self):
        """Once the budget is spent, remaining records go to compaction."""
        client = MagicMock()
        clock = {"now": 0.0}

        def upsert(**_kwargs):
            clock["now"] += 40.0

        client.upsert.side_effect = upsert
        with (
            patch(_CLIENT_FACTORY, return_value=client),
            patch(
                "receipt_chroma.embedding.cloud_upsert.time.monotonic",
                side_effect=lambda: clock["now"],
            ),
        ):
            result = upsert_payload_to_cloud(
                make_payload(750),
                "words",
                CLOUD_CFG,
                deadline_seconds=60.0,
            )

        # First batch runs, second starts at 40s, third is past 60s.
        assert result.deadline_exceeded is True
        assert result.batches == 2
        assert result.upserted == 500
        assert result.success is False
        assert "deadline" in result.error
        client.close.assert_called_once()

    def test_fast_batches_never_trip_the_deadline(self):
        client = MagicMock()
        with patch(_CLIENT_FACTORY, return_value=client):
            result = upsert_payload_to_cloud(
                make_payload(500), "words", CLOUD_CFG, deadline_seconds=60.0
            )

        assert result.deadline_exceeded is False
        assert result.upserted == 500

    def test_a_stalled_client_constructor_cannot_outlast_the_budget(self):
        """Chroma's CloudClient() issues its own untimed requests."""
        started = threading.Event()

        def never_returns(*_args, **_kwargs):
            started.set()
            time.sleep(30)
            raise AssertionError("should have been abandoned")

        began = time.monotonic()
        with patch(_CLIENT_FACTORY, side_effect=never_returns):
            result = upsert_payload_to_cloud(
                make_payload(5),
                "words",
                CLOUD_CFG,
                deadline_seconds=0.5,
            )
        elapsed = time.monotonic() - began

        assert started.is_set()
        assert elapsed < 5.0, "returned only after the constructor stalled"
        assert result.deadline_exceeded is True
        assert result.success is False
        assert result.upserted == 0

    def test_per_record_fallback_checks_the_deadline(self):
        """A rejected batch is 250 more chances to stall."""
        client = MagicMock()
        clock = {"now": 0.0}

        def upsert(**kwargs):
            if len(kwargs["ids"]) > 1:
                raise ValueError("batch rejected")
            clock["now"] += 30.0

        client.upsert.side_effect = upsert
        with (
            patch(_CLIENT_FACTORY, return_value=client),
            patch(
                "receipt_chroma.embedding.cloud_upsert.time.monotonic",
                side_effect=lambda: clock["now"],
            ),
        ):
            result = upsert_payload_to_cloud(
                make_payload(10),
                "words",
                CLOUD_CFG,
                deadline_seconds=60.0,
            )

        assert result.deadline_exceeded is True
        assert result.upserted < 10
        assert "per-record fallback" in result.error


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

    def test_success_requires_a_completely_clean_run(self):
        assert CloudUpsertResult(collection="words").success is True
        for kwargs in (
            {"failed_batches": 1},
            {"error": "boom"},
            {"dropped": 1},
            {"deadline_exceeded": True},
        ):
            assert (
                CloudUpsertResult(collection="words", **kwargs).success
                is False
            ), kwargs

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
