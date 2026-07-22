"""Tests for the standing eval-regression corpus gate (H6).

Prove the three behaviors the sprint acceptance requires (item 4):
  - a seeded one-value mutation in a COPY of a corpus fixture makes ``compare``
    fail, naming the receipt AND the metric;
  - a clean corpus produces no findings (exit 0);
  - two same-commit captures are byte-identical (determinism).

Plus guards on the committed artifacts (baseline in sync with fixtures) and
the pinned nondeterminism (graphics deterministically SKIPPED), and that the
``--json`` findings are machine-parseable for H7's comment poster.
"""

from __future__ import annotations

import copy
import json

import pytest

from synthesis_loop import corpus_regression_gate as gate


def _write(path, payload) -> None:
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), "utf-8")


@pytest.fixture()
def temp_corpus(tmp_path, monkeypatch):
    """A one-entry temp corpus reusing the committed fixture_mart fixtures.

    Copies the eval-input bundle into ``tmp_path`` (so the test can mutate it
    without touching the committed fixture) and writes a temp manifest that
    points the gate at it. The hash-verified truth fixture is referenced
    read-only at its committed absolute path.
    """
    entry = {
        "name": "fixture_mart_v1",
        "merchant": "Fixture Mart",
        "truth_fixture": gate._repo_path(
            "tests/fixtures/merchant_truth/fixture_mart.v1.json"
        ),
        "eval_inputs": str(tmp_path / "fixture_mart_v1.inputs.json"),
        "columns_source": "bootstrap",
    }
    committed_inputs = gate._repo_path(
        "tests/fixtures/corpus_gate/fixture_mart_v1.inputs.json"
    )
    inputs = json.loads(open(committed_inputs, encoding="utf-8").read())
    _write(tmp_path / "fixture_mart_v1.inputs.json", inputs)
    manifest_path = tmp_path / "manifest.json"
    _write(manifest_path, {"version": 1, "entries": [entry]})
    return {
        "manifest": str(manifest_path),
        "inputs_path": tmp_path / "fixture_mart_v1.inputs.json",
        "inputs": inputs,
        "name": entry["name"],
    }


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------
def test_same_commit_captures_are_byte_identical(temp_corpus):
    a = gate.capture_all(temp_corpus["manifest"])
    b = gate.capture_all(temp_corpus["manifest"])
    assert a == b
    dump_a = json.dumps(a, indent=2, sort_keys=True)
    dump_b = json.dumps(b, indent=2, sort_keys=True)
    assert dump_a == dump_b


def test_graphics_is_pinned_skipped(temp_corpus):
    """The one nondeterminism source (barcode detector) is pinned OFF."""
    cap = gate.capture_all(temp_corpus["manifest"])
    rec = cap["entries"][temp_corpus["name"]]
    assert rec["metrics"]["graphics"]["verdict"] == "SKIPPED"


# --------------------------------------------------------------------------
# clean corpus -> no findings
# --------------------------------------------------------------------------
def test_clean_corpus_has_no_findings(temp_corpus):
    baseline = gate.capture_all(temp_corpus["manifest"])
    current = gate.capture_all(temp_corpus["manifest"])
    assert gate.diff_baselines(baseline, current) == []


def test_clean_compare_exits_zero(temp_corpus, tmp_path):
    baseline = gate.capture_all(temp_corpus["manifest"])
    baseline_path = tmp_path / "baseline.json"
    _write(baseline_path, baseline)
    monkeypatched = _use_manifest(temp_corpus["manifest"])
    with monkeypatched:
        rc = gate.compare(str(baseline_path))
    assert rc == 0


# --------------------------------------------------------------------------
# seeded one-value mutation -> compare fails naming receipt + metric
# --------------------------------------------------------------------------
def test_seeded_token_mutation_names_receipt_and_metric(temp_corpus):
    baseline = gate.capture_all(temp_corpus["manifest"])

    # ONE-VALUE mutation in a COPY of the fixture: erase a single word from
    # the synth render so it no longer matches the real manifest.
    mutated = copy.deepcopy(temp_corpus["inputs"])
    before = len(mutated["syn_words"])
    mutated["syn_words"] = [
        w for w in mutated["syn_words"] if w["text"] != "CHEESE"
    ]
    assert len(mutated["syn_words"]) == before - 1  # exactly one value changed
    _write(temp_corpus["inputs_path"], mutated)

    current = gate.capture_all(temp_corpus["manifest"])
    findings = gate.diff_baselines(baseline, current)

    assert findings, "mutation must produce findings"
    # every finding names THIS receipt
    assert all(f["receipt"] == temp_corpus["name"] for f in findings)
    # the tokens metric is named as regressed
    tokens = [f for f in findings if f["metric"] == "tokens"]
    assert tokens, f"expected a tokens finding, got {findings}"
    assert any(f["field"] == "tokens.verdict" for f in tokens)
    # the recall drop carries a signed delta
    recall = [f for f in tokens if f["field"] == "tokens.text_recall"]
    assert recall and recall[0]["delta"] < 0


def test_mutation_makes_compare_exit_nonzero(temp_corpus, tmp_path):
    baseline = gate.capture_all(temp_corpus["manifest"])
    baseline_path = tmp_path / "baseline.json"
    _write(baseline_path, baseline)

    mutated = copy.deepcopy(temp_corpus["inputs"])
    mutated["syn_words"] = [
        w for w in mutated["syn_words"] if w["text"] != "CHEESE"
    ]
    _write(temp_corpus["inputs_path"], mutated)

    with _use_manifest(temp_corpus["manifest"]):
        rc = gate.compare(str(baseline_path))
    assert rc == 1


def test_json_findings_are_machine_parseable(temp_corpus, tmp_path, capsys):
    baseline = gate.capture_all(temp_corpus["manifest"])
    baseline_path = tmp_path / "baseline.json"
    _write(baseline_path, baseline)

    mutated = copy.deepcopy(temp_corpus["inputs"])
    mutated["syn_words"] = [
        w for w in mutated["syn_words"] if w["text"] != "CHEESE"
    ]
    _write(temp_corpus["inputs_path"], mutated)

    with _use_manifest(temp_corpus["manifest"]):
        gate.compare(str(baseline_path), as_json=True)
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False
    assert doc["findings"]
    for f in doc["findings"]:
        assert {"receipt", "metric", "field"} <= set(f)


# --------------------------------------------------------------------------
# committed-artifact guards (what CI actually runs)
# --------------------------------------------------------------------------
def test_committed_baseline_in_sync_with_fixtures():
    """`check` against the committed baseline + committed fixtures is clean."""
    baseline = gate._load_json(gate.COMMITTED_BASELINE)
    current = gate.capture_all()
    assert gate.diff_baselines(baseline, current) == []


def test_committed_check_exits_zero():
    assert gate.check() == 0


# --------------------------------------------------------------------------
# helper: run compare/check against a temp manifest by patching capture_all's
# default manifest path.
# --------------------------------------------------------------------------
class _use_manifest:
    def __init__(self, manifest_path):
        self.manifest_path = manifest_path
        self._orig = None

    def __enter__(self):
        self._orig = gate.capture_all

        def _patched(manifest_path=self.manifest_path):
            return self._orig(manifest_path)

        gate.capture_all = _patched
        return self

    def __exit__(self, *exc):
        gate.capture_all = self._orig
        return False
