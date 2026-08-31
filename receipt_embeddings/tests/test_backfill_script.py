"""Offline tests for the embedding backfill script.

Cover the Round C gates: dev-table-only refusal, per-receipt
skip-and-report, idempotent re-run writing nothing, the end-of-run
report, and the bounded searchability wait.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from scripts.embedding_backfill import backfill_embeddings
from scripts.embedding_backfill.backfill_embeddings import (
    FixtureVectorSource,
    _wait_for_searchability,
)
from scripts.similarity_harness.capture_golden import build_offline_bootstrap

from receipt_embeddings import ScoredItem
from receipt_embeddings.writer import EmbeddingRequest

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
LINE_KEY = f"IMAGE#{IMAGE_ID}#RECEIPT#00001#LINE#00001"


@pytest.mark.unit
def test_backfill_refuses_non_dev_table(capsys):
    with pytest.raises(SystemExit, match="refusing to write"):
        backfill_embeddings.main(["--table-name", "ReceiptsTable-d7ff76a"])


@pytest.mark.unit
def test_backfill_rejects_bad_limit():
    with pytest.raises(SystemExit, match="--limit"):
        backfill_embeddings.main(["--limit", "0"])


@pytest.mark.unit
def test_fixture_vector_source_partial_coverage():
    # The bootstrap builder needs enough receipts to fill its top-k
    # neighbor lists (word queries capture exactly 30 neighbors).
    fixture = build_offline_bootstrap(
        [
            {
                "image_id": IMAGE_ID,
                "receipt_id": receipt_id,
                "merchant_name": "Costco",
            }
            for receipt_id in range(1, 31)
        ]
    )
    source = FixtureVectorSource(fixture)
    covered = EmbeddingRequest(key=LINE_KEY, input_text="ignored")
    uncovered = EmbeddingRequest(
        key=LINE_KEY.replace("LINE#00001", "LINE#00099"),
        input_text="ignored",
    )
    vectors = source.vectors_for([covered, uncovered])
    # Covered keys resolve; uncovered ones are simply absent so the
    # writer can skip-and-report them.
    assert set(vectors) == {LINE_KEY}
    assert len(vectors[LINE_KEY]) == 16  # bootstrap fixture dimension


class _FakeSearchClient:
    """get_vector/search stub with a controllable searchability delay."""

    def __init__(self, *, searchable_after_polls: int = 0, missing=()):
        self._countdown = dict.fromkeys([], 0)
        self._searchable_after = searchable_after_polls
        self._missing = set(missing)
        self.polls = 0

    def get_vector(self, key):
        if key in self._missing:
            raise KeyError(f"unknown vector key: {key}")
        return [1.0, 0.0]

    def search(self, vector, index, top_k, filters=None):
        self.polls += 1
        if self.polls > self._searchable_after:
            return [ScoredItem(key=LINE_KEY, distance=0.0)]
        return []


@pytest.mark.unit
def test_searchability_wait_finds_item():
    client = _FakeSearchClient(searchable_after_polls=1)
    result = _wait_for_searchability(
        client,
        [(LINE_KEY, "lines-vectors")],
        timeout_seconds=5.0,
        poll_seconds=0.01,
    )
    assert result["found"] == [LINE_KEY]
    assert result["timed_out"] == []


@pytest.mark.unit
def test_searchability_wait_is_bounded():
    client = _FakeSearchClient(searchable_after_polls=10_000)
    result = _wait_for_searchability(
        client,
        [(LINE_KEY, "lines-vectors")],
        timeout_seconds=0.05,
        poll_seconds=0.01,
    )
    assert result["found"] == []
    assert result["timed_out"] == [LINE_KEY]
    assert result["elapsed_seconds"] >= 0.05


@pytest.mark.unit
def test_searchability_wait_tolerates_vanished_item():
    client = _FakeSearchClient(missing={LINE_KEY})
    result = _wait_for_searchability(
        client,
        [(LINE_KEY, "lines-vectors")],
        timeout_seconds=0.05,
        poll_seconds=0.01,
    )
    assert result["found"] == []
    assert result["timed_out"] == []


def _run_main(monkeypatch, tmp_path, reports_by_key, argv_extra=()):
    """Drive main() with a stubbed DynamoClient and writer."""

    import receipt_dynamo

    monkeypatch.setattr(
        receipt_dynamo, "DynamoClient", lambda table_name: SimpleNamespace()
    )

    class FakeWriter:
        def __init__(self, dynamo, *, vector_source):
            pass

        def embed_receipt(self, image_id, receipt_id):
            result = reports_by_key[f"{image_id}#{receipt_id:05d}"]
            if isinstance(result, Exception):
                raise result
            return result

    monkeypatch.setattr(backfill_embeddings, "EmbedAndPutWriter", FakeWriter)
    monkeypatch.setattr(
        backfill_embeddings,
        "_build_vector_source",
        lambda args: (object(), "stub", lambda: None),
    )

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"image_id": key.rsplit("#", 1)[0], "receipt_id": int(key.rsplit("#", 1)[1])}
                for key in reports_by_key
            ]
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    exit_code = backfill_embeddings.main(
        [
            "--manifest",
            str(manifest),
            "--skip-wait",
            "--report-out",
            str(report_path),
            *argv_extra,
        ]
    )
    return exit_code, json.loads(report_path.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_main_reports_written_and_skipped(monkeypatch, tmp_path):
    ok_report = SimpleNamespace(
        written_line_keys=["a"],
        written_word_keys=["b", "c"],
        existing_line_keys=[],
        existing_word_keys=[],
        failures=[],
        written_count=3,
        skipped_existing_count=0,
    )
    reports = {
        f"{IMAGE_ID}#00001": ok_report,
        f"{IMAGE_ID}#00002": ValueError("receipt has no lines or words"),
    }
    exit_code, report = _run_main(monkeypatch, tmp_path, reports)

    assert exit_code == 0
    assert report["written_line_items"] == 1
    assert report["written_word_items"] == 2
    assert report["receipts_processed"] == 1
    assert report["receipt_skip_reasons"] == {"incomplete_receipt_data": 1}
    assert report["vector_source"] == "stub"


@pytest.mark.unit
def test_main_absent_receipt_skips_and_reports(monkeypatch, tmp_path):
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

    reports = {
        f"{IMAGE_ID}#00001": EntityNotFoundError("receipt does not exist"),
    }
    exit_code, report = _run_main(monkeypatch, tmp_path, reports)

    assert exit_code == 0
    assert report["receipts_processed"] == 0
    assert report["receipt_skip_reasons"] == {"receipt_not_found": 1}


@pytest.mark.unit
def test_main_rerun_shape_writes_nothing(monkeypatch, tmp_path):
    """An all-existing run reports zero writes (idempotency evidence)."""

    rerun_report = SimpleNamespace(
        written_line_keys=[],
        written_word_keys=[],
        existing_line_keys=["a"],
        existing_word_keys=["b", "c"],
        failures=[],
        written_count=0,
        skipped_existing_count=3,
    )
    reports = {f"{IMAGE_ID}#00001": rerun_report}
    exit_code, report = _run_main(monkeypatch, tmp_path, reports)

    assert exit_code == 0
    assert report["written_line_items"] == 0
    assert report["written_word_items"] == 0
    assert report["existing_line_items_skipped"] == 1
    assert report["existing_word_items_skipped"] == 2
