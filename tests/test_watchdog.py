"""Watchdog group-kill tests (W2 review finding #1).

The regression test reproduces the reviewer's probe: a grandchild
(backgrounded writer) must STOP writing after the watchdog times the
parent out. The old perl alarm+exec fallback killed only the direct
child, so the writer survived; the setsid + killpg watchdog must kill
the whole group.
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
