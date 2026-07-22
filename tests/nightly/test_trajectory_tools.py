"""Tests for scripts/nightly/trajectory_tools.py (plan humble-skipping-quilt
H1). Written failing-test-first, before the tool exists, against two recorded
stream-json fixtures:

  - trajectory_sample.jsonl   : a real 2-turn ``claude -p`` headless run
    ("list two files then say done"), recorded with
    ``--output-format stream-json --verbose``.
  - trajectory_truncated.jsonl: the same trace hand-cut mid-stream (a partial
    final JSON line, no terminal ``result`` event) — the parser must not
    crash, must report what it has, and must flag ``truncated``.

Pinned numbers come from the recorded fixture. The streamed ``assistant``
events repeat one ``message.id`` across its content blocks, so usage is summed
per distinct message (dedup by id); on this trace the deduped input/cache sums
equal the terminal aggregate exactly (16 / 9618 / 39344), which anchors the
method.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.nightly import trajectory_tools

REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES = Path(__file__).parent.parent / "fixtures" / "nightly"
SAMPLE = FIXTURES / "trajectory_sample.jsonl"
TRUNCATED = FIXTURES / "trajectory_truncated.jsonl"
TOOL = REPO_ROOT / "scripts" / "nightly" / "trajectory_tools.py"

SESSION_ID = "74c0e794-b1c7-46a0-8c47-7920e3e8676a"
RESULT_TEXT = (
    "The directory contains four files: `alpha.txt`, `beta.txt`, "
    "`stderr.log`, and `trajectory_sample.jsonl`.\n\ndone"
)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        check=False,
    )


# --------------------------------------------------------------------------
# extract-result (agent_stdout.log continuity)
# --------------------------------------------------------------------------
def test_extract_result_returns_final_result_text():
    assert trajectory_tools.extract_result(SAMPLE) == RESULT_TEXT


def test_extract_result_cli_prints_result_to_stdout():
    proc = run_cli("extract-result", str(SAMPLE))
    assert proc.returncode == 0, proc.stderr
    # Regenerated agent_stdout.log must be the final assistant text, not JSON.
    assert proc.stdout.rstrip("\n") == RESULT_TEXT
    assert '{"type"' not in proc.stdout


# --------------------------------------------------------------------------
# metrics -> run_metrics.json
# --------------------------------------------------------------------------
def test_metrics_numbers():
    m = trajectory_tools.compute_metrics(
        SAMPLE, claude_version="2.1.217 (Claude Code)"
    )
    assert m["session_id"] == SESSION_ID  # from the init event
    assert m["claude_version"] == "2.1.217 (Claude Code)"
    assert m["total_cost_usd"] == pytest.approx(0.07367019999999999)
    assert m["num_turns"] == 2
    assert m["duration_ms"] == 10284
    assert m["truncated"] is False
    assert m["parse_errors"] == 0
    assert m["usage_event_count"] == 2  # two distinct assistant messages
    assert m["token_totals"] == {
        "input": 16,
        "output": 7,
        "cache_creation": 9618,
        "cache_read": 39344,
    }


def test_metrics_wrapper_duration_passthrough():
    m = trajectory_tools.compute_metrics(SAMPLE, duration_secs=42)
    assert m["wrapper_duration_secs"] == 42


def test_metrics_cli_emits_valid_json(tmp_path):
    out = tmp_path / "run_metrics.json"
    proc = run_cli(
        "metrics",
        str(SAMPLE),
        "--claude-version",
        "2.1.217 (Claude Code)",
        "--duration-secs",
        "42",
        "-o",
        str(out),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    assert data["session_id"] == SESSION_ID
    assert data["num_turns"] == 2
    assert data["claude_version"] == "2.1.217 (Claude Code)"
    assert data["wrapper_duration_secs"] == 42


# --------------------------------------------------------------------------
# summarize (human replay timeline)
# --------------------------------------------------------------------------
def test_summarize_renders_every_turn():
    text = trajectory_tools.render_summary(SAMPLE)
    # The tool call and its (truncated) result must both appear.
    assert "Bash" in text
    assert "ls" in text
    assert "alpha.txt" in text
    # Both distinct assistant messages are rendered.
    assert "I'll list the files" in text
    assert "The directory contains four files" in text
    # Run-level replay footer.
    assert SESSION_ID in text
    assert "num_turns" in text or "turns" in text.lower()


def test_summarize_cli_runs():
    proc = run_cli("summarize", str(SAMPLE))
    assert proc.returncode == 0, proc.stderr
    assert "Bash" in proc.stdout


# --------------------------------------------------------------------------
# truncated / mid-stream cut: parser must degrade, never crash
# --------------------------------------------------------------------------
def test_truncated_parse_does_not_crash_and_flags_truncated():
    parsed = trajectory_tools.parse(TRUNCATED)
    assert parsed.truncated is True
    assert parsed.parse_errors >= 1  # the partial final line
    assert parsed.init is not None  # init survived the cut
    assert parsed.result is None  # no terminal result event


def test_truncated_metrics_reports_partial_with_flag():
    m = trajectory_tools.compute_metrics(TRUNCATED)
    assert m["truncated"] is True
    assert m["parse_errors"] >= 1
    assert m["session_id"] == SESSION_ID  # still recoverable from init
    # No terminal result event: cost/turns/duration are absent, not fabricated.
    assert m["total_cost_usd"] is None
    assert m["num_turns"] is None
    assert m["duration_ms"] is None
    # Only the first assistant message's usage is present pre-cut.
    assert m["usage_event_count"] == 1
    assert m["token_totals"] == {
        "input": 10,
        "output": 3,
        "cache_creation": 6922,
        "cache_read": 17589,
    }


def test_truncated_extract_result_falls_back_without_crashing():
    # No result event -> fall back to the last assistant text, no exception.
    text = trajectory_tools.extract_result(TRUNCATED)
    assert isinstance(text, str)
    assert "list the files" in text


def test_truncated_summarize_marks_truncation():
    text = trajectory_tools.render_summary(TRUNCATED)
    assert "Bash" in text  # renders what it has
    assert "TRUNCATED" in text.upper()


def test_truncated_cli_exit_zero():
    # A truncated trajectory is a reportable condition, not a tool crash.
    for sub in ("extract-result", "metrics", "summarize"):
        proc = run_cli(sub, str(TRUNCATED))
        assert proc.returncode == 0, (sub, proc.stderr)
