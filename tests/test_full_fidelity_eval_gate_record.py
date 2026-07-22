"""Wiring tests for full_fidelity_eval --write-gate-record (W-F).

Two guarantees, per the plan:
  * flag OFF  -> zero gate-record writes (mutation-checked against the guard);
  * flag ON   -> exactly one record whose fields match the printed report.
Plus the dev-pin: prod is refused unconditionally.
"""

import argparse
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (
    os.path.join(REPO, "synthesis_loop"),
    os.path.join(REPO, "scripts"),
    os.path.join(REPO, "receipt_agent"),
    os.path.join(REPO, "tools", "glyph-studio", "py"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import full_fidelity_eval as ffe  # noqa: E402

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE = "ReceiptsTable-d7ff76a"


class _FakeImg:
    size = (10, 20)

    def save(self, path):  # noqa: D401 - test double
        pass


class _FakeTruth:
    slug = "costco-wholesale"
    version = 1
    bundle_hash = "a" * 64

    def component(self, name):
        return {}


CHECKS = {
    "columns": {"verdict": "PASS"},
    "style": {"verdict": "PASS"},
    "tokens": {"verdict": "PASS"},
    "separators": {"verdict": "PASS"},
    "graphics": {"verdict": "PASS"},
    "logo": {"verdict": "PASS_WITH_GAPS", "score": 0.42, "band": "storefront"},
    "arithmetic": {"verdict": "PASS"},
    "coverage_gaps": ["logo", "columns:top:price"],
    "overall": "PASS_WITH_GAPS",
}

FAIL_CHECKS = {
    "columns": {"verdict": "FAIL", "wobble": 3.1},
    "logo": {"verdict": "PASS"},
    "coverage_gaps": [],
    "overall": "FAIL",
}

STAMP = {"git_sha": "deadbeefcafe", "merchant": "costco"}


def _args(tmp_path, write_gate_record):
    return argparse.Namespace(
        merchant="costco",
        image_id="img-1",
        receipt_id=1,
        slug="costco-wholesale",
        out_root=str(tmp_path),
        allow_dirty=True,
        columns_source="bootstrap",
        pin_version=None,
        pin_bundle_hash=None,
        truth_fixture=None,
        write_gate_record=write_gate_record,
    )


def _stub_render(monkeypatch):
    """Stub the heavy render/eval seams so run() reaches the flag guard."""
    monkeypatch.setattr(ffe, "resolve_truth", lambda *a, **k: _FakeTruth())
    monkeypatch.setattr(ffe, "build_stamp", lambda *a, **k: dict(STAMP))
    monkeypatch.setattr(ffe, "inputs_hash", lambda *a, **k: "x" * 16)
    monkeypatch.setattr(ffe, "evaluate_pair", lambda *a, **k: dict(CHECKS))
    monkeypatch.setattr(ffe, "_load_real", lambda *a, **k: (None, [], None))
    monkeypatch.setattr(ffe, "_write_report", lambda *a, **k: None)
    monkeypatch.setattr(ffe, "_write_sheet", lambda *a, **k: None)
    fake_sc = types.ModuleType("section_compare")
    fake_sc.render_pair = lambda *a, **k: (_FakeImg(), _FakeImg(), 0, [])
    monkeypatch.setitem(sys.modules, "section_compare", fake_sc)


def test_flag_off_writes_nothing(monkeypatch, tmp_path):
    _stub_render(monkeypatch)
    calls = []
    monkeypatch.setattr(
        ffe,
        "_write_gate_record",
        lambda *a, **k: calls.append(a),
    )
    rc = ffe.run(_args(tmp_path, write_gate_record=False))
    # PASS_WITH_GAPS overall -> exit 2, and crucially: no gate write happened.
    assert rc == 2
    assert calls == []


def test_flag_on_writes_exactly_one(monkeypatch, tmp_path):
    _stub_render(monkeypatch)
    calls = []
    monkeypatch.setattr(
        ffe,
        "_write_gate_record",
        lambda *a, **k: calls.append(a),
    )
    ffe.run(_args(tmp_path, write_gate_record=True))
    assert len(calls) == 1


def test_gate_record_matches_report(monkeypatch, tmp_path):
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", DEV_TABLE)
    captured = {}

    class _FakeClient:
        def __init__(self, table):
            captured["table"] = table

        def add_gate_record(self, record, expected_table_name):
            captured["record"] = record
            captured["expected"] = expected_table_name
            return record

    import receipt_dynamo.data.dynamo_client as dc

    monkeypatch.setattr(dc, "DynamoClient", _FakeClient)

    stem = os.path.join(str(tmp_path), "costco-wholesale")
    record = ffe._write_gate_record(
        _args(tmp_path, write_gate_record=True),
        _FakeTruth(),
        dict(STAMP),
        dict(CHECKS),
        stem,
    )

    from receipt_dynamo.data.merchant_truth_gate_bridge import (
        bridge_eval_to_gate_results,
    )

    gate = bridge_eval_to_gate_results(CHECKS)
    assert captured["table"] == DEV_TABLE
    assert captured["expected"] == DEV_TABLE
    # Overall + per_metric + gaps are the bridge output VERBATIM (the same
    # derivation a W-J seal's manifest gate_results uses).
    assert record.overall == CHECKS["overall"] == gate["overall"]
    assert record.per_metric == gate["per_metric"]
    assert record.gaps == gate["gaps"]
    # detail is the metric entry's non-verdict fields, verbatim.
    assert record.gaps[0] == {
        "metric": "logo",
        "verdict": "PASS_WITH_GAPS",
        "detail": {"score": 0.42, "band": "storefront"},
    }
    # coverage_gaps live in a SEPARATE field, never mixed into gaps.
    assert record.coverage == CHECKS["coverage_gaps"]
    assert "columns:top:price" not in {g["metric"] for g in record.gaps}
    assert record.eval_git_sha == STAMP["git_sha"]
    assert record.bundle_hash == _FakeTruth.bundle_hash
    assert record.version == _FakeTruth.version
    assert record.receipt_tested["image_id"] == "img-1"


def test_record_gaps_byte_equal_seal_gate_results():
    """Cross-artifact: the gate RECORD gaps equal a seal's manifest
    gate_results gaps, byte-for-byte, for both non-FAIL and FAIL runs
    (contract section 7.5's same-verbatim-in-both)."""
    from receipt_dynamo.data.merchant_truth_gate_bridge import (
        GateBlockedError,
        bridge_eval_to_gate_results,
    )

    # PASS_WITH_GAPS: bridge returns gate_results directly.
    seal_gate_results = bridge_eval_to_gate_results(CHECKS)
    record_derivation = ffe._derive_gate_results(CHECKS)
    assert record_derivation == seal_gate_results
    assert record_derivation["gaps"] == seal_gate_results["gaps"]

    # FAIL: the seal is blocked, but the record still derives the SAME gaps
    # off the exception -- one derivation, no re-implementation.
    try:
        bridge_eval_to_gate_results(FAIL_CHECKS)
        raise AssertionError("FAIL_CHECKS should block the seal")
    except GateBlockedError as blocked:
        fail_seal_results = blocked.gate_results
    fail_record = ffe._derive_gate_results(FAIL_CHECKS)
    assert fail_record == fail_seal_results
    assert fail_record["gaps"] == fail_seal_results["gaps"]
    assert fail_record["overall"] == "FAIL"


def test_fail_run_writes_work_list_record(monkeypatch, tmp_path):
    """A FAIL eval still writes a record (the work list), append-only."""
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", DEV_TABLE)
    captured = {}

    class _FakeClient:
        def __init__(self, table):
            captured["table"] = table

        def add_gate_record(self, record, expected_table_name):
            captured["record"] = record
            return record

    import receipt_dynamo.data.dynamo_client as dc

    monkeypatch.setattr(dc, "DynamoClient", _FakeClient)
    record = ffe._write_gate_record(
        _args(tmp_path, write_gate_record=True),
        _FakeTruth(),
        dict(STAMP),
        dict(FAIL_CHECKS),
        os.path.join(str(tmp_path), "costco-wholesale"),
    )
    assert record.overall == "FAIL"
    assert {g["metric"] for g in record.gaps} == {"columns"}
    assert record.gaps[0]["detail"] == {"wobble": 3.1}


def test_prod_table_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", PROD_TABLE)
    with pytest.raises(SystemExit, match="never touches prod"):
        ffe._write_gate_record(
            _args(tmp_path, write_gate_record=True),
            _FakeTruth(),
            dict(STAMP),
            dict(CHECKS),
            os.path.join(str(tmp_path), "costco-wholesale"),
        )
