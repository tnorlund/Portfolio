#!/usr/bin/env python3
"""Process-group watchdog for the nightly loop (W2 review finding #1).

Runs a command in its OWN session/process group (``start_new_session=True``
= setsid) and, on timeout, kills the WHOLE GROUP: SIGTERM to -PGID, a
grace period, then SIGKILL to -PGID. This replaces the previous
timeout/gtimeout/perl-alarm fallback chain, whose perl leg killed only the
direct child and left grandchildren (stdio MCP servers, subagents) alive.

Usage:
    watchdog.py [--grace SECS] TIMEOUT_SECS -- COMMAND [ARGS...]
    watchdog.py --self-test

Exit codes:
    child's exit code on normal completion
    124  timeout (group killed)
    127  command not found
    64   usage error
    --self-test: 0 iff setsid + group-kill provably work on this host
    (a spawned grandchild stops writing after the kill).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time

DEFAULT_GRACE = 30.0
TIMEOUT_EXIT = 124
NOT_FOUND_EXIT = 127
USAGE_EXIT = 64


def _kill_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run(timeout: float, grace: float, cmd: list[str]) -> int:
    """Run cmd in its own process group under a group-kill watchdog."""
    try:
        proc = subprocess.Popen(cmd, start_new_session=True)
    except FileNotFoundError:
        print(f"watchdog: command not found: {cmd[0]}", file=sys.stderr)
        return NOT_FOUND_EXIT
    pgid = proc.pid  # start_new_session makes the child its own group leader

    def _forward(signum: int, _frame: object) -> None:
        _kill_group(pgid, signal.SIGTERM)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _forward)
    signal.signal(signal.SIGINT, _forward)

    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(
            f"watchdog: timeout after {timeout}s; SIGTERM to group {pgid}",
            file=sys.stderr,
        )
        _kill_group(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        # Always follow with a group SIGKILL: the direct child may be dead
        # while TERM-ignoring grandchildren linger in the group.
        _kill_group(pgid, signal.SIGKILL)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print(
                f"watchdog: group {pgid} did not reap after SIGKILL",
                file=sys.stderr,
            )
        return TIMEOUT_EXIT


def self_test() -> int:
    """Prove group-kill works HERE: a grandchild must die with the group.

    Spawns sh -> backgrounded writer loop (a grandchild of the watchdog),
    times the parent out, then asserts the writer stopped writing. This is
    exactly the probe that exposed the perl-alarm fallback leaving
    orphaned writers alive.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "probe.out")
        script = (
            f'(while :; do echo alive >> "{out}"; sleep 0.1; done) & '
            "sleep 30"
        )
        code = run(timeout=1.0, grace=1.0, cmd=["/bin/sh", "-c", script])
        if code != TIMEOUT_EXIT:
            print(
                f"watchdog self-test: expected {TIMEOUT_EXIT}, got {code}",
                file=sys.stderr,
            )
            return 1
        if not os.path.exists(out):
            print(
                "watchdog self-test: probe writer never started",
                file=sys.stderr,
            )
            return 1
        time.sleep(0.4)
        size_a = os.path.getsize(out)
        time.sleep(0.8)
        size_b = os.path.getsize(out)
        if size_a != size_b:
            print(
                "watchdog self-test: grandchild survived the group kill "
                f"({size_a} -> {size_b} bytes)",
                file=sys.stderr,
            )
            return 1
    print("watchdog self-test: group-kill OK", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    args = list(argv)
    if args == ["--self-test"]:
        return self_test()
    grace = DEFAULT_GRACE
    if len(args) >= 2 and args[0] == "--grace":
        try:
            grace = float(args[1])
        except ValueError:
            print(f"watchdog: bad --grace {args[1]!r}", file=sys.stderr)
            return USAGE_EXIT
        args = args[2:]
    if "--" not in args:
        print(__doc__, file=sys.stderr)
        return USAGE_EXIT
    sep = args.index("--")
    head, cmd = args[:sep], args[sep + 1 :]
    if len(head) != 1 or not cmd:
        print(__doc__, file=sys.stderr)
        return USAGE_EXIT
    try:
        timeout = float(head[0])
    except ValueError:
        print(f"watchdog: bad timeout {head[0]!r}", file=sys.stderr)
        return USAGE_EXIT
    return run(timeout=timeout, grace=grace, cmd=cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
