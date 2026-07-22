"""Watchdog group-kill tests (W2 review finding #1).

The regression test reproduces the reviewer's probe: a grandchild
(backgrounded writer) must STOP writing after the watchdog times the
parent out. The old perl alarm+exec fallback killed only the direct
child, so the writer survived; the setsid + killpg watchdog must kill
the whole group.

The token-budget tests (plan humble-skipping-quilt H2) extend the same
group-kill probe: a fast-burn synthetic trajectory grown past a tiny budget
must trip the breaker (exit 123) and kill the WHOLE group (the grandchild
writer stops), while an under-budget run passes the child's exit through, a
pure-garbage trajectory fails OPEN (no kill), and the absence of the budget
flag leaves wall-clock-only behavior unchanged.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from scripts.nightly import watchdog

WATCHDOG_CLI = (
    Path(__file__).parent.parent / "scripts" / "nightly" / "watchdog.py"
)

# A single stream-json assistant usage event carrying ID_MARKER for a distinct
# message id (usage is summed per DISTINCT message id, so unique ids are what
# make the running total grow) and TOKENS input tokens.
_USAGE_EVENT = (
    '{"type":"assistant","message":{"id":"m__ID_MARKER__",'
    '"usage":{"input_tokens":__TOKENS__,"output_tokens":0,'
    '"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}'
)


def _burn_trajectory_script(traj: Path, probe: Path, tokens_per_event: int):
    """A shell program that (a) backgrounds a grandchild writer loop and
    (b) appends a fresh unique usage event to ``traj`` every 0.1s forever.

    It never exits on its own: only the watchdog's group-kill can stop it, so
    the grandchild writer doubles as proof the whole group died.
    """
    event = _USAGE_EVENT.replace("__TOKENS__", str(tokens_per_event))
    # printf template with %d for the incrementing message id; escape the
    # literal % that appear nowhere and the JSON braces are fine for printf.
    event_fmt = event.replace("__ID_MARKER__", "%d")
    return (
        f'(while :; do echo alive >> "{probe}"; sleep 0.1; done) &\n'
        "i=0\n"
        "while :; do\n"
        "  i=$((i+1))\n"
        f'  printf \'{event_fmt}\\n\' "$i" >> "{traj}"\n'
        "  sleep 0.1\n"
        "done\n"
    )


def test_token_budget_breach_group_kills_and_exits_123(tmp_path):
    """Fast-burn trajectory past a tiny budget: exit 123 AND the grandchild
    writer must stop (whole-group death), same probe as the wall-clock test."""
    traj = tmp_path / "trajectory.jsonl"
    probe = tmp_path / "probe.out"
    # 1000 tokens/event, budget 2500 -> trips after the 3rd event (~0.3s).
    script = _burn_trajectory_script(traj, probe, tokens_per_event=1000)

    started = time.time()
    rc = watchdog.run(
        timeout=30,  # generous wall clock: the BREAKER must fire, not this
        grace=1.0,
        cmd=["/bin/sh", "-c", script],
        token_budget=2500,
        trajectory=str(traj),
        poll_interval=0.2,
    )
    assert rc == watchdog.TOKEN_EXIT, rc
    assert time.time() - started < 15  # breaker fired fast, not the 30s wall

    # Group-death proof: the backgrounded grandchild writer must have stopped.
    assert probe.exists(), "writer grandchild never started"
    time.sleep(0.4)
    size_a = probe.stat().st_size
    assert size_a > 0
    time.sleep(0.8)
    size_b = probe.stat().st_size
    assert size_a == size_b, (
        "grandchild survived the token-budget kill "
        f"({size_a} -> {size_b} bytes still growing)"
    )


def test_under_budget_passes_child_exit_through(tmp_path):
    """Budget armed but never breached: the child's own exit code survives."""
    traj = tmp_path / "trajectory.jsonl"
    event = _USAGE_EVENT.replace("__ID_MARKER__", "1").replace(
        "__TOKENS__", "10"
    )
    script = f"printf '{event}\\n' >> \"{traj}\"; sleep 0.5; exit 7"
    rc = watchdog.run(
        timeout=10,
        grace=1.0,
        cmd=["/bin/sh", "-c", script],
        token_budget=1_000_000,
        trajectory=str(traj),
        poll_interval=0.2,
    )
    assert rc == 7


def test_garbage_trajectory_fails_open_child_completes(tmp_path):
    """Pure-garbage trajectory + tiny budget: the breaker must NOT trip and
    must NOT crash the monitor. Count what parses (nothing), skip the rest."""
    traj = tmp_path / "trajectory.jsonl"
    traj.write_text("this is not json\n{not: valid, json at all\n\x00\x01\n")
    script = f'echo "more garbage <<>>" >> "{traj}"; sleep 0.6; exit 0'
    rc = watchdog.run(
        timeout=10,
        grace=1.0,
        cmd=["/bin/sh", "-c", script],
        token_budget=1,  # would trip instantly if any garbage were counted
        trajectory=str(traj),
        poll_interval=0.2,
    )
    assert rc == 0  # failed open: child ran to normal completion


def test_missing_trajectory_fails_open(tmp_path):
    """A trajectory file that never appears must not trip or crash."""
    traj = tmp_path / "never_created.jsonl"
    rc = watchdog.run(
        timeout=10,
        grace=1.0,
        cmd=["/bin/sh", "-c", "sleep 0.5; exit 5"],
        token_budget=1,
        trajectory=str(traj),
        poll_interval=0.2,
    )
    assert rc == 5


def test_no_budget_flag_is_wall_clock_only(tmp_path):
    """Budget absent -> byte-identical to today: normal exit passes through,
    and a wall-clock timeout still group-kills with exit 124."""
    # Pass-through (no token args at all).
    assert (
        watchdog.run(timeout=10, grace=1, cmd=["/bin/sh", "-c", "exit 9"]) == 9
    )
    # Wall-clock timeout path unchanged (exit 124, not 123).
    out = tmp_path / "probe.out"
    script = f'(while :; do echo x >> "{out}"; sleep 0.1; done) & sleep 30'
    rc = watchdog.run(timeout=1.0, grace=1.0, cmd=["/bin/sh", "-c", script])
    assert rc == watchdog.TIMEOUT_EXIT


def test_cli_token_flags_parse_and_passthrough(tmp_path):
    """CLI wiring: --token-budget/--trajectory parse; under budget the child
    exit passes through (proves the flags don't disturb usage parsing)."""
    traj = tmp_path / "trajectory.jsonl"
    traj.write_text("")
    proc = subprocess.run(
        [
            sys.executable,
            str(WATCHDOG_CLI),
            "--grace",
            "1",
            "--token-budget",
            "1000000",
            "--trajectory",
            str(traj),
            "10",
            "--",
            "/bin/sh",
            "-c",
            "exit 4",
        ],
        check=False,
    )
    assert proc.returncode == 4


def test_normal_exit_passes_through_exit_code():
    assert (
        watchdog.run(timeout=10, grace=1, cmd=["/bin/sh", "-c", "exit 7"]) == 7
    )
    assert (
        watchdog.run(timeout=10, grace=1, cmd=["/bin/sh", "-c", "true"]) == 0
    )


def test_missing_command_returns_127():
    rc = watchdog.run(
        timeout=5, grace=1, cmd=["/nonexistent-watchdog-probe-cmd"]
    )
    assert rc == watchdog.NOT_FOUND_EXIT


def test_timeout_kills_whole_process_group_not_just_parent(tmp_path):
    """Grandchild keeps-writing regression: FAILS with a parent-only kill
    (the old perl alarm+exec fallback), passes with setsid + group-kill."""
    out = tmp_path / "probe.out"
    script = f'(while :; do echo alive >> "{out}"; sleep 0.1; done) & sleep 30'
    started = time.time()
    rc = watchdog.run(timeout=1.0, grace=1.0, cmd=["/bin/sh", "-c", script])
    assert rc == watchdog.TIMEOUT_EXIT
    assert time.time() - started < 15  # grace honored, no 30s hang

    assert out.exists(), "writer grandchild never started"
    time.sleep(0.4)
    size_a = out.stat().st_size
    assert size_a > 0
    time.sleep(0.8)
    size_b = out.stat().st_size
    assert size_a == size_b, (
        "grandchild survived the timeout kill "
        f"({size_a} -> {size_b} bytes still growing)"
    )


def test_self_test_passes_on_this_host():
    """The wrapper refuses a real launch unless this passes here."""
    assert watchdog.self_test() == 0


def test_cli_parsing_and_exit_passthrough():
    proc = subprocess.run(
        [
            sys.executable,
            str(WATCHDOG_CLI),
            "--grace",
            "1",
            "5",
            "--",
            "/bin/sh",
            "-c",
            "exit 3",
        ],
        check=False,
    )
    assert proc.returncode == 3


def test_cli_usage_errors():
    for argv in ([], ["5"], ["--", "/bin/true"], ["notanumber", "--", "x"]):
        proc = subprocess.run(
            [sys.executable, str(WATCHDOG_CLI), *argv],
            capture_output=True,
            check=False,
        )
        assert proc.returncode == watchdog.USAGE_EXIT, argv
