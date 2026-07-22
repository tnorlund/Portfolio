#!/usr/bin/env python3
"""Process-group watchdog for the nightly loop (W2 review finding #1).

Runs a command in its OWN session/process group (``start_new_session=True``
= setsid) and, on timeout, kills the WHOLE GROUP: SIGTERM to -PGID, a
grace period, then SIGKILL to -PGID. This replaces the previous
timeout/gtimeout/perl-alarm fallback chain, whose perl leg killed only the
direct child and left grandchildren (stdio MCP servers, subagents) alive.

Token circuit breaker (plan humble-skipping-quilt H2): with ``--token-budget
N --trajectory FILE`` the watchdog also polls the growing ``trajectory.jsonl``
about every ``--poll-secs`` (default 15s) and computes the deduped per-message
token sum over ALL usage token types unweighted (input + output +
cache_creation + cache_read). Summing is REUSED from ``trajectory_tools`` (the
H1 module) rather than re-implemented: its ``streamed_token_totals`` are the
per-distinct-message-id sums the live path can compute incrementally. Counting
all four types unweighted is the conservative "trip early, never late" choice
(see the H1 module's H2 HAND-OFF note: the exactly-counted input+cache side
dominates and grows monotonically; a single-turn output-heavy runaway is the
wall-clock watchdog's job, not the breaker's). A breach kills the WHOLE GROUP
on the SAME path as a wall-clock timeout and returns exit 123.

Fail-open contract (plan Cross-cutting): the breaker NEVER trips or crashes on
malformed/partial trajectory lines. ``trajectory_tools`` parses tolerantly
(counts what parses, skips what doesn't); any unexpected error in the token
poll is swallowed and treated as "no signal, keep running" so a parse bug can
never kill a healthy night.

Usage:
    watchdog.py [--grace SECS] [--token-budget N --trajectory FILE]
                [--poll-secs SECS] TIMEOUT_SECS -- COMMAND [ARGS...]
    watchdog.py --self-test

Exit codes:
    child's exit code on normal completion
    124  timeout (group killed)
    123  token budget exceeded (group killed)
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
DEFAULT_POLL_SECS = 15.0
TIMEOUT_EXIT = 124
TOKEN_EXIT = 123
NOT_FOUND_EXIT = 127
USAGE_EXIT = 64


def _kill_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _terminate_group(proc: subprocess.Popen, pgid: int, grace: float) -> None:
    """Kill the whole group: SIGTERM, ``grace`` to drain, then a group
    SIGKILL. Shared by the wall-clock and token-breaker kill paths."""
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


def _token_usage(trajectory: str) -> int | None:
    """Deduped per-message token sum over all four usage types, or None.

    REUSES ``trajectory_tools`` summing (never re-implemented). Returns None
    on ANY failure so the breaker fails open: a parse bug or import problem is
    treated as "no signal", never as a reason to kill a healthy night. The
    underlying parser is already tolerant (counts what parses, skips garbage),
    so pure-garbage or partial files yield 0 here, never an exception.
    """
    try:
        try:
            from scripts.nightly import trajectory_tools  # package context
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import trajectory_tools  # type: ignore[no-redef]
        # streamed_token_totals = per-distinct-message-id sums (the live path
        # can compute these incrementally). Sum all four types unweighted.
        totals = trajectory_tools.compute_metrics(trajectory)[
            "streamed_token_totals"
        ]
        return sum(v for v in totals.values() if isinstance(v, int))
    except Exception:  # fail open: never let a poll error kill the night
        return None


def run(
    timeout: float,
    grace: float,
    cmd: list[str],
    *,
    token_budget: int | None = None,
    trajectory: str | None = None,
    poll_interval: float = DEFAULT_POLL_SECS,
) -> int:
    """Run cmd in its own process group under a group-kill watchdog.

    With ``token_budget`` + ``trajectory`` set, the trajectory file is polled
    every ``poll_interval`` seconds and a breach group-kills with exit 123.
    Without them, behavior is wall-clock-only and unchanged.
    """
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

    breaker_armed = token_budget is not None and trajectory is not None

    if not breaker_armed:
        # Wall-clock-only fast path (byte-for-byte the original behavior).
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            print(
                f"watchdog: timeout after {timeout}s; SIGTERM to group {pgid}",
                file=sys.stderr,
            )
            _terminate_group(proc, pgid, grace)
            return TIMEOUT_EXIT

    # Token-breaker path: wait in poll_interval slices, checking the budget
    # between slices while still honoring the wall-clock deadline.
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(
                f"watchdog: timeout after {timeout}s; SIGTERM to group {pgid}",
                file=sys.stderr,
            )
            _terminate_group(proc, pgid, grace)
            return TIMEOUT_EXIT
        try:
            return proc.wait(timeout=min(poll_interval, remaining))
        except subprocess.TimeoutExpired:
            pass
        used = _token_usage(trajectory)  # None on any error -> fail open
        if used is not None and used > token_budget:
            print(
                f"watchdog: token budget exceeded ({used} of {token_budget}); "
                f"SIGTERM to group {pgid}",
                file=sys.stderr,
            )
            _terminate_group(proc, pgid, grace)
            return TOKEN_EXIT


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
    poll_interval = DEFAULT_POLL_SECS
    token_budget: int | None = None
    trajectory: str | None = None

    # Consume optional leading "--flag VALUE" pairs (any order) before the
    # positional TIMEOUT and the "--" command separator.
    i = 0
    while i < len(args) and args[i].startswith("--") and args[i] != "--":
        flag = args[i]
        if i + 1 >= len(args):
            print(__doc__, file=sys.stderr)
            return USAGE_EXIT
        val = args[i + 1]
        if flag == "--grace":
            try:
                grace = float(val)
            except ValueError:
                print(f"watchdog: bad --grace {val!r}", file=sys.stderr)
                return USAGE_EXIT
        elif flag == "--token-budget":
            try:
                token_budget = int(val)
            except ValueError:
                print(f"watchdog: bad --token-budget {val!r}", file=sys.stderr)
                return USAGE_EXIT
        elif flag == "--trajectory":
            trajectory = val
        elif flag == "--poll-secs":
            try:
                poll_interval = float(val)
            except ValueError:
                print(f"watchdog: bad --poll-secs {val!r}", file=sys.stderr)
                return USAGE_EXIT
        else:
            print(f"watchdog: unknown flag {flag!r}", file=sys.stderr)
            print(__doc__, file=sys.stderr)
            return USAGE_EXIT
        i += 2
    args = args[i:]

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
    # --token-budget and --trajectory arm the breaker together or not at all.
    if (token_budget is None) != (trajectory is None):
        print(
            "watchdog: --token-budget and --trajectory must be given together",
            file=sys.stderr,
        )
        return USAGE_EXIT
    return run(
        timeout=timeout,
        grace=grace,
        cmd=cmd,
        token_budget=token_budget,
        trajectory=trajectory,
        poll_interval=poll_interval,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
