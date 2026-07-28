"""Tests for the ingest path's non-fatal Chroma Cloud upsert.

The upsert makes freshly embedded words and lines queryable immediately
instead of waiting for compaction to merge the delta tarball. Three
properties matter and are covered here: a payload is staged by its worker
and published only once the CompactionRun that can reconcile it is durable,
publication never fails ingest for any reason including telemetry, and a
label snapshot too stale to be safely ordered is not published at all.
"""

import json
import os
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


def ok_result(**kwargs):
    defaults = dict(
        collection="words",
        attempted=2,
        upserted=2,
        batches=1,
        duration_seconds=0.4,
    )
    defaults.update(kwargs)
    return CloudUpsertResult(**defaults)


@pytest.fixture(autouse=True)
def _enable_metrics(monkeypatch):
    monkeypatch.setenv("ENABLE_METRICS", "true")
    monkeypatch.delenv(
        "INGEST_CLOUD_UPSERT_MAX_LABEL_AGE_SECONDS", raising=False
    )


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
        assert blob["image_id"] == "img-1"

    def test_disabled_by_env(self, capsys, monkeypatch):
        monkeypatch.setenv("ENABLE_METRICS", "false")
        ep._emit_emf_metrics({"IngestCloudUpsertSuccess": 1})
        assert emitted_metrics(capsys) == []


class TestStagedPublication:
    """Workers hand payloads back; the parent publishes after the run row."""

    def test_stage_and_publish_round_trip(self, capsys):
        path = ep._stage_cloud_payload(PAYLOAD, "words", CLOUD_CFG)
        assert path and os.path.exists(path)

        try:
            with patch.object(
                ep, "upsert_payload_to_cloud", return_value=ok_result()
            ) as upsert:
                ep._publish_staged_payload(
                    path, "words", CLOUD_CFG, "img-1", 1
                )

            assert upsert.call_args.kwargs["payload"] == PAYLOAD
            (blob,) = emitted_metrics(capsys)
            assert blob["IngestCloudUpsertSuccess"] == 1
        finally:
            ep._discard_staged_payload(path)

        assert not os.path.exists(path)

    def test_staging_is_skipped_when_cloud_is_disabled(self):
        assert ep._stage_cloud_payload(PAYLOAD, "words", None) is None

    def test_publishing_without_a_staged_path_is_a_noop(self, capsys):
        with patch.object(ep, "upsert_payload_to_cloud") as upsert:
            ep._publish_staged_payload(None, "words", CLOUD_CFG, "img-1", 1)

        upsert.assert_not_called()
        assert emitted_metrics(capsys) == []

    def test_unreadable_staged_payload_does_not_raise(self, capsys):
        with patch.object(ep, "upsert_payload_to_cloud") as upsert:
            ep._publish_staged_payload(
                "/nonexistent/staged.pkl", "words", CLOUD_CFG, "img-1", 1
            )

        upsert.assert_not_called()
        assert emitted_metrics(capsys) == []

    def test_staging_failure_degrades_to_no_publication(self):
        with patch.object(
            ep.tempfile, "mkstemp", side_effect=OSError("no space")
        ):
            assert ep._stage_cloud_payload(PAYLOAD, "words", CLOUD_CFG) is None

    def test_discarding_a_missing_file_is_silent(self):
        ep._discard_staged_payload("/nonexistent/staged.pkl")
        ep._discard_staged_payload(None)

    def test_a_failed_write_leaves_no_partial_file(self):
        """A warm Lambda keeps /tmp, so a half-written file would persist."""
        created = []
        real_mkstemp = ep.tempfile.mkstemp

        def tracking_mkstemp(*args, **kwargs):
            handle, path = real_mkstemp(*args, **kwargs)
            created.append(path)
            return handle, path

        with (
            patch.object(ep.tempfile, "mkstemp", side_effect=tracking_mkstemp),
            patch.object(
                ep.pickle, "dump", side_effect=OSError("disk full mid-write")
            ),
        ):
            assert ep._stage_cloud_payload(PAYLOAD, "words", CLOUD_CFG) is None

        assert created, "expected a temp file to have been created"
        for path in created:
            assert not os.path.exists(path), f"{path} was left behind"

    def test_a_successful_write_keeps_the_file(self):
        path = ep._stage_cloud_payload(PAYLOAD, "words", CLOUD_CFG)
        try:
            assert path and os.path.exists(path)
        finally:
            ep._discard_staged_payload(path)


class TestTracingRetryCleanup:
    """The worker reruns its pipeline if the LangSmith flush fails."""

    def test_first_staged_payload_is_dropped_before_the_rerun(self):
        """Otherwise the first attempt's file is orphaned in /tmp."""
        first = ep._stage_cloud_payload(PAYLOAD, "words", CLOUD_CFG)
        assert first and os.path.exists(first)

        # What the worker's except branch does with the discarded result.
        ep._discard_staged_payload(
            {"cloud_payload_path": first}.get("cloud_payload_path")
        )

        assert not os.path.exists(first)

    @pytest.mark.parametrize(
        "worker",
        ["_run_lines_pipeline_worker", "_run_words_pipeline_worker"],
    )
    def test_both_workers_clean_up_before_rerunning(self, worker):
        import inspect

        source = inspect.getsource(getattr(ep, worker))
        assert "traced_result" in source
        assert "_discard_staged_payload(" in source


class TestStalenessFence:
    """Direct writes bypass compaction's FIFO ordering."""

    def test_stale_label_snapshot_skips_the_cloud_write(self, capsys):
        with (
            patch.object(ep, "upsert_payload_to_cloud") as upsert,
            patch.object(ep.time, "monotonic", return_value=1000.0),
        ):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
                labels_fetched_at=1000.0 - 600.0,
            )

        upsert.assert_not_called()
        (blob,) = emitted_metrics(capsys)
        assert blob["IngestCloudUpsertSkipped"] == 1
        assert blob["reason"] == "stale_risk"
        assert blob["label_age_seconds"] == 600.0

    def test_fresh_label_snapshot_publishes(self, capsys):
        with (
            patch.object(
                ep, "upsert_payload_to_cloud", return_value=ok_result()
            ) as upsert,
            patch.object(ep.time, "monotonic", return_value=1000.0),
        ):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
                labels_fetched_at=1000.0 - 5.0,
            )

        upsert.assert_called_once()
        (blob,) = emitted_metrics(capsys)
        assert blob["IngestCloudUpsertSuccess"] == 1

    def test_threshold_is_configurable(self, capsys, monkeypatch):
        monkeypatch.setenv("INGEST_CLOUD_UPSERT_MAX_LABEL_AGE_SECONDS", "10")
        with (
            patch.object(ep, "upsert_payload_to_cloud") as upsert,
            patch.object(ep.time, "monotonic", return_value=1000.0),
        ):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
                labels_fetched_at=1000.0 - 30.0,
            )

        upsert.assert_not_called()

    def test_unparseable_threshold_falls_back_to_the_default(
        self, monkeypatch
    ):
        monkeypatch.setenv(
            "INGEST_CLOUD_UPSERT_MAX_LABEL_AGE_SECONDS", "not-a-number"
        )
        assert ep._max_label_age_seconds() == 300.0


class TestUpsertToCloudNonFatal:
    """The wrapper never raises and always reports."""

    def test_success_emits_success_metric(self, capsys):
        with patch.object(
            ep, "upsert_payload_to_cloud", return_value=ok_result()
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

    def test_partial_failure_emits_failure_metric_without_raising(
        self, capsys
    ):
        result = ok_result(
            collection="lines",
            attempted=601,
            upserted=351,
            batches=3,
            failed_batches=1,
            dropped=2,
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
        assert blob["IngestCloudUpsertDropped"] == 2
        assert blob["IngestCloudUpsertRecords"] == 351
        assert blob["error"] == "RuntimeError: cloud 503"

    def test_deadline_exceeded_is_reported(self, capsys):
        result = ok_result(
            upserted=250,
            attempted=750,
            batches=2,
            deadline_exceeded=True,
            error="deadline of 60s exceeded with 500 record(s) unwritten",
        )
        with patch.object(ep, "upsert_payload_to_cloud", return_value=result):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )

        (blob,) = emitted_metrics(capsys)
        assert blob["IngestCloudUpsertFailure"] == 1
        assert blob["deadline_exceeded"] is True

    def test_telemetry_after_an_overrun_is_time_bounded(self):
        """Reporting a blown budget must not blow it further."""
        import time as _time

        result = ok_result(
            upserted=0,
            attempted=2,
            deadline_exceeded=True,
            error="deadline of 60s exceeded",
        )
        with (
            patch.object(ep, "upsert_payload_to_cloud", return_value=result),
            patch.object(
                ep,
                "_emit_emf_metrics",
                side_effect=lambda *a, **k: _time.sleep(5),
            ),
            patch.object(
                ep, "_log", side_effect=lambda *a, **k: _time.sleep(5)
            ),
        ):
            began = _time.monotonic()
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )
            elapsed = _time.monotonic() - began

        assert elapsed < 2.0, f"telemetry tail was unbounded: {elapsed:.2f}s"

    def test_backpressure_also_bounds_the_telemetry(self):
        """A refused attempt means earlier ones are stuck on I/O."""
        import time as _time

        result = ok_result(
            upserted=0,
            attempted=37,
            dropped=37,
            backpressure=True,
            drop_reasons={"orphaned_threads": 37},
            error="more than 3 cloud upsert attempts are still in flight",
        )
        with (
            patch.object(ep, "upsert_payload_to_cloud", return_value=result),
            patch.object(
                ep,
                "_emit_emf_metrics",
                side_effect=lambda *a, **k: _time.sleep(5),
            ),
            patch.object(
                ep, "_log", side_effect=lambda *a, **k: _time.sleep(5)
            ),
        ):
            began = _time.monotonic()
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )
            elapsed = _time.monotonic() - began

        assert elapsed < 2.0, f"telemetry tail was unbounded: {elapsed:.2f}s"

    def test_backpressure_is_reported_as_a_failure(self, capsys):
        result = ok_result(
            upserted=0,
            attempted=37,
            dropped=37,
            backpressure=True,
            drop_reasons={"orphaned_threads": 37},
        )
        with patch.object(ep, "upsert_payload_to_cloud", return_value=result):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )

        (blob,) = emitted_metrics(capsys)
        assert blob["IngestCloudUpsertFailure"] == 1
        assert blob["IngestCloudUpsertDropped"] == 37
        assert blob["backpressure"] is True

    def test_unexpected_exception_is_swallowed(self, capsys):
        with patch.object(
            ep, "upsert_payload_to_cloud", side_effect=RuntimeError("boom")
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

    def test_telemetry_failure_does_not_escape(self):
        """A broken metric write must not fail an ingest that succeeded."""
        with (
            patch.object(
                ep, "upsert_payload_to_cloud", return_value=ok_result()
            ),
            patch.object(
                ep,
                "_emit_emf_metrics",
                side_effect=RuntimeError("stdout gone"),
            ),
        ):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )

    def test_logging_failure_does_not_escape(self):
        with (
            patch.object(
                ep, "upsert_payload_to_cloud", return_value=ok_result()
            ),
            patch.object(ep, "_log", side_effect=RuntimeError("log gone")),
        ):
            ep._upsert_to_cloud_nonfatal(
                payload=PAYLOAD,
                collection_name="words",
                cloud_cfg=CLOUD_CFG,
                image_id="img-1",
                receipt_id=1,
            )

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
