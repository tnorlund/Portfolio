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

The stall tests (plan humble-skipping-quilt H3) reuse the same group-kill
probe for a different silence: a child that writes progress briefly then
sleeps forever must be killed at ~stall+grace (exit 122), a continuous writer
survives to its own exit, a never-created progress file trips at stall_secs
after launch (the watchdog's start time is the baseline), and a stat error in
the poll fails OPEN. A combined test arms wall + budget + stall together and
shows each fires independently with its own exit code.
"""

from __future__ import annotations

import os
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


def test_poll_error_fails_open_child_completes(tmp_path, monkeypatch):
    """Safety-critical: if the token poll itself RAISES (not just parses
    garbage), the swallow layer must fail open — the armed watchdog's child
    completes normally, its own exit code preserved, no kill.

    The garbage/missing tests only exercise the parser's tolerance; this one
    forces the summing call to blow up (a hypothetical compute_metrics bug or
    stream-json shape drift) and pins that a poll exception can NEVER kill a
    healthy night. Remove the ``except Exception`` in watchdog._token_usage and
    this test reds (run() propagates the error and the child is orphaned)."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated compute_metrics failure")

    # _token_usage does `from scripts.nightly import trajectory_tools` and calls
    # compute_metrics at poll time, so patching the module attribute makes every
    # poll raise.
    monkeypatch.setattr(
        "scripts.nightly.trajectory_tools.compute_metrics", _boom
    )

    traj = tmp_path / "trajectory.jsonl"
    # Write real, over-budget usage so that WITHOUT the swallow the run would
    # both crash (poll raises) — proving the swallow, not a low count, is what
    # keeps the child alive.
    traj.write_text(
        _USAGE_EVENT.replace("__ID_MARKER__", "1").replace(
            "__TOKENS__", "999999"
        )
        + "\n"
    )
    rc = watchdog.run(
        timeout=10,
        grace=1.0,
        cmd=["/bin/sh", "-c", "sleep 0.6; exit 5"],
        token_budget=1,  # tiny: any counted token would trip
        trajectory=str(traj),
        poll_interval=0.2,
    )
    assert rc == 5  # failed open despite the raising poll; child's code kept


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


# --- H3: stall watchdog (exit 122) -----------------------------------------


def test_stall_write_then_sleep_group_kills_and_exits_122(tmp_path):
    """Write-then-sleep probe: the child touches the progress file a few times
    then sleeps forever. The stall watchdog must kill the WHOLE group at
    ~stall+grace (exit 122); the backgrounded grandchild writer is the
    group-death proof (it must FREEZE after the kill)."""
    progress = tmp_path / "trajectory.jsonl"
    probe = tmp_path / "probe.out"
    # Grandchild writer -> probe (NOT a progress file: it keeps writing until
    # the group dies). Main proc touches progress 3x (~0.3s) then sleeps 300s,
    # so the progress signal goes quiet and the stall trips.
    script = (
        f'(while :; do echo alive >> "{probe}"; sleep 0.1; done) &\n'
        f'for i in 1 2 3; do echo tick >> "{progress}"; sleep 0.1; done\n'
        "sleep 300\n"
    )
    started = time.time()
    rc = watchdog.run(
        timeout=30,  # generous wall clock: STALL must fire, not the wall
        grace=1.0,
        cmd=["/bin/sh", "-c", script],
        stall_secs=1.0,
        progress_files=[str(progress)],
        poll_interval=0.2,
    )
    assert rc == watchdog.STALL_EXIT, rc
    assert time.time() - started < 15  # stall fired fast, not the 30s wall

    # Group-death proof: the backgrounded grandchild writer must have stopped.
    assert probe.exists(), "writer grandchild never started"
    time.sleep(0.4)
    size_a = probe.stat().st_size
    assert size_a > 0
    time.sleep(0.8)
    size_b = probe.stat().st_size
    assert size_a == size_b, (
        "grandchild survived the stall kill "
        f"({size_a} -> {size_b} bytes still growing)"
    )


def test_stall_continuous_writer_survives_to_its_own_exit(tmp_path):
    """A child that keeps touching the progress file faster than stall_secs
    never trips the stall watchdog and reaches its own exit code."""
    progress = tmp_path / "trajectory.jsonl"
    # Touch progress every 0.1s for ~2s (well under stall_secs=1.0), then exit.
    script = (
        f'for i in $(seq 1 20); do echo tick >> "{progress}"; sleep 0.1; '
        "done\n"
        "exit 6\n"
    )
    rc = watchdog.run(
        timeout=30,
        grace=1.0,
        cmd=["/bin/sh", "-c", script],
        stall_secs=1.0,
        progress_files=[str(progress)],
        poll_interval=0.2,
    )
    assert rc == 6


def test_stall_never_created_progress_trips_at_stall_secs(tmp_path):
    """A progress file that NEVER appears must still trip: the watchdog's own
    start time is the baseline, so a never-created file trips at stall_secs
    after launch (not immediately, and not never)."""
    progress = tmp_path / "never_created.jsonl"  # nothing ever writes it
    started = time.time()
    rc = watchdog.run(
        timeout=30,
        grace=0.5,
        cmd=["/bin/sh", "-c", "sleep 300"],  # idle: never writes anything
        stall_secs=1.0,
        progress_files=[str(progress)],
        poll_interval=0.2,
    )
    assert rc == watchdog.STALL_EXIT, rc
    elapsed = time.time() - started
    assert elapsed >= 0.9, elapsed  # did NOT trip before stall_secs
    assert elapsed < 10, elapsed  # DID trip (baseline is the clock)


def test_stall_stat_error_fails_open_child_completes(tmp_path, monkeypatch):
    """Safety-critical: if os.stat itself RAISES (a non-missing error), the
    swallow layer must fail open — the armed watchdog's child completes
    normally, no kill. Mirrors the token breaker's poll-error test. The tiny
    stall_secs would trip instantly if the error were not swallowed."""
    progress = tmp_path / "trajectory.jsonl"
    progress.write_text("tick\n")  # exists, but stat will blow up on it
    real_stat = os.stat

    def _boom(path, *args, **kwargs):
        if str(path) == str(progress):
            raise RuntimeError("simulated stat failure")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", _boom)

    rc = watchdog.run(
        timeout=10,
        grace=0.5,
        cmd=["/bin/sh", "-c", "sleep 0.6; exit 5"],
        stall_secs=0.1,  # tiny: would trip at once if the error weren't swallowed
        progress_files=[str(progress)],
        poll_interval=0.2,
    )
    assert rc == 5  # failed open despite the raising stat; child's code kept


def test_combined_wall_budget_stall_each_trips_independently(tmp_path):
    """Arm wall + budget + stall together and prove each fires on its own in
    three scenarios, each with its distinct exit code."""
    # --- Scenario 1: WALL trips (124). No usage events (token can't fire),
    # progress kept fresh (stall can't fire), short 1s wall wins.
    progress1 = tmp_path / "s1_progress.jsonl"
    traj1 = tmp_path / "s1_traj.jsonl"
    traj1.write_text("")  # stays empty -> 0 tokens
    script1 = f'while :; do echo tick >> "{progress1}"; sleep 0.1; done\n'
    started = time.time()
    rc1 = watchdog.run(
        timeout=1.0,
        grace=1.0,
        cmd=["/bin/sh", "-c", script1],
        token_budget=1_000_000,
        trajectory=str(traj1),
        stall_secs=30.0,
        progress_files=[str(progress1)],
        poll_interval=0.2,
    )
    assert rc1 == watchdog.TIMEOUT_EXIT, rc1  # 124
    assert time.time() - started < 10

    # --- Scenario 2: TOKEN trips (123). Fast burn past a tiny budget; the burn
    # keeps its own trajectory fresh (stall can't fire), 30s wall is generous.
    traj2 = tmp_path / "s2_traj.jsonl"
    probe2 = tmp_path / "s2_probe.out"
    script2 = _burn_trajectory_script(traj2, probe2, tokens_per_event=1000)
    rc2 = watchdog.run(
        timeout=30,
        grace=1.0,
        cmd=["/bin/sh", "-c", script2],
        token_budget=2500,
        trajectory=str(traj2),
        stall_secs=30.0,
        progress_files=[str(traj2)],  # fresh -> stall silent
        poll_interval=0.2,
    )
    assert rc2 == watchdog.TOKEN_EXIT, rc2  # 123

    # --- Scenario 3: STALL trips (122). Progress goes quiet, no usage events
    # (token silent), 30s wall is generous.
    progress3 = tmp_path / "s3_progress.jsonl"
    traj3 = tmp_path / "s3_traj.jsonl"
    traj3.write_text("")  # empty -> 0 tokens, token can't fire
    script3 = (
        f'for i in 1 2 3; do echo tick >> "{progress3}"; sleep 0.1; done\n'
        "sleep 300\n"
    )
    rc3 = watchdog.run(
        timeout=30,
        grace=1.0,
        cmd=["/bin/sh", "-c", script3],
        token_budget=1_000_000,
        trajectory=str(traj3),
        stall_secs=1.0,
        progress_files=[str(progress3)],
        poll_interval=0.2,
    )
    assert rc3 == watchdog.STALL_EXIT, rc3  # 122


def test_no_stall_flag_is_wall_clock_only(tmp_path):
    """Stall (and token) flags absent -> byte-identical wall-clock-only path:
    normal exit passes through, and a wall-clock timeout still exits 124."""
    assert (
        watchdog.run(timeout=10, grace=1, cmd=["/bin/sh", "-c", "exit 8"]) == 8
    )
    out = tmp_path / "probe.out"
    script = f'(while :; do echo x >> "{out}"; sleep 0.1; done) & sleep 30'
    rc = watchdog.run(timeout=1.0, grace=1.0, cmd=["/bin/sh", "-c", script])
    assert rc == watchdog.TIMEOUT_EXIT


def test_cli_stall_flags_parse_and_passthrough(tmp_path):
    """CLI wiring: --stall-secs parses and --progress is REPEATABLE; under the
    threshold (fresh files) the child's exit passes through."""
    progress = tmp_path / "trajectory.jsonl"
    progress.write_text("")
    report = tmp_path / "report.md"
    report.write_text("")
    proc = subprocess.run(
        [
            sys.executable,
            str(WATCHDOG_CLI),
            "--grace",
            "1",
            "--stall-secs",
            "30",
            "--progress",
            str(progress),
            "--progress",
            str(report),
            "10",
            "--",
            "/bin/sh",
            "-c",
            "exit 4",
        ],
        check=False,
    )
    assert proc.returncode == 4


def test_cli_stall_requires_progress():
    """--stall-secs without any --progress is a usage error (both-or-neither)."""
    proc = subprocess.run(
        [
            sys.executable,
            str(WATCHDOG_CLI),
            "--stall-secs",
            "30",
            "10",
            "--",
            "/bin/true",
        ],
        capture_output=True,
        check=False,
    )
    assert proc.returncode == watchdog.USAGE_EXIT


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
