"""Tests for the ingest path's non-fatal Chroma Cloud upsert.

The upsert makes freshly embedded words and lines queryable immediately
instead of waiting for compaction to merge the delta tarball. It must never
fail ingest, and it must report itself through EMF metrics.
"""

import json
from unittest.mock import patch

import pytest
from receipt_chroma.embedding.cloud_upsert import CloudUpsertResult

from receipt_upload.merchant_resolution import embedding_processor as ep

CLOUD_CFG = {
    "api_key": "test-key",
    "tenant": "test-tenant",
    "database": "receipt_test",
}

PAYLOAD = {
    "ids": ["a", "b"],
    "embeddings": [[0.1], [0.2]],
    "documents": ["a", "b"],
    "metadatas": [{"k": 1}, {"k": 2}],
}


def emitted_metrics(capsys):
    """Parse EMF blobs printed to stdout."""
    blobs = []
    for line in capsys.readouterr().out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "_aws" in parsed:
            blobs.append(parsed)
    return blobs


@pytest.fixture(autouse=True)
def _enable_metrics(monkeypatch):
    monkeypatch.setenv("ENABLE_METRICS", "true")


class TestEmitEmfMetrics:
    """EMF formatting matches the compaction handler's conventions."""

    def test_namespace_dimensions_and_units(self, capsys):
        ep._emit_emf_metrics(
            {"IngestCloudUpsertSuccess": 1, "IngestCloudUpsertLatency": 0.5},
            dimensions={"collection": "words"},
            units={"IngestCloudUpsertLatency": "Seconds"},
            properties={"image_id": "img-1"},
        )

        (blob,) = emitted_metrics(capsys)
        directive = blob["_aws"]["CloudWatchMetrics"][0]
        assert directive["Namespace"] == "EmbeddingWorkflow"
        assert directive["Dimensions"] == [["collection"]]
        assert {m["Name"]: m["Unit"] for m in directive["Metrics"]} == {
            "IngestCloudUpsertSuccess": "Count",
            "IngestCloudUpsertLatency": "Seconds",
        }
        assert blob["collection"] == "words"
        assert blob["IngestCloudUpsertSuccess"] == 1
        assert blob["image_id"] == "img-1"

    def test_disabled_by_env(self, capsys, monkeypatch):
        monkeypatch.setenv("ENABLE_METRICS", "false")
        ep._emit_emf_metrics({"IngestCloudUpsertSuccess": 1})
        assert emitted_metrics(capsys) == []


class TestUpsertToCloudNonFatal:
    """The wrapper never raises and always reports."""

    def test_success_emits_success_metric(self, capsys):
        result = CloudUpsertResult(
            collection="words",
            attempted=2,
            upserted=2,
            batches=1,
            duration_seconds=0.4,
        )
        with patch.object(
            ep, "upsert_payload_to_cloud", return_value=result
        ) as upsert:
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )

        assert upsert.call_args.kwargs["collection_name"] == "words"
        assert upsert.call_args.kwargs["cloud_config"] == CLOUD_CFG

        (blob,) = emitted_metrics(capsys)
        assert blob["IngestCloudUpsertSuccess"] == 1
        assert "IngestCloudUpsertFailure" not in blob
        assert blob["IngestCloudUpsertRecords"] == 2
        assert blob["collection"] == "words"

    def test_partial_failure_emits_failure_metric_without_raising(
        self, capsys
    ):
        result = CloudUpsertResult(
            collection="lines",
            attempted=601,
            upserted=351,
            batches=3,
            failed_batches=1,
            error="RuntimeError: cloud 503",
            duration_seconds=1.1,
        )
        with patch.object(ep, "upsert_payload_to_cloud", return_value=result):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="lines",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )

        (blob,) = emitted_metrics(capsys)
        assert blob["IngestCloudUpsertFailure"] == 1
        assert blob["IngestCloudUpsertFailedBatches"] == 1
        assert blob["IngestCloudUpsertRecords"] == 351
        assert blob["error"] == "RuntimeError: cloud 503"

    def test_unexpected_exception_is_swallowed(self, capsys):
        with patch.object(
            ep,
            "upsert_payload_to_cloud",
            side_effect=RuntimeError("boom"),
        ):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )

        (blob,) = emitted_metrics(capsys)
        assert blob["IngestCloudUpsertFailure"] == 1
        assert blob["error"] == "RuntimeError: boom"

    @pytest.mark.parametrize("cloud_cfg", [None, {}], ids=["none", "empty"])
    def test_cloud_disabled_is_a_noop(self, capsys, cloud_cfg):
        with patch.object(ep, "upsert_payload_to_cloud") as upsert:
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=cloud_cfg,
                image_id="img-1",
                receipt_id=1,
            )

        upsert.assert_not_called()
        assert emitted_metrics(capsys) == []
