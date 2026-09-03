#!/usr/bin/env python3
import argparse
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

DEFAULT_MISSION = (
    "Read CONTEXT.md then CHARTER.md in this worktree. They hold the full context "
    "from the session that set this branch up plus your specific mission. Follow "
    "the charter milestones in order and self-review with codex along the way "
    "exactly as CONTEXT.md mandates. Begin with milestone 1 now."
)

ANSI_RE = re.compile(
    rb"\x1b\[[0-9;?]*[ -/]*[@-~]"
    rb"|\x1b\][^\x07]*(?:\x07|\x1b\\)"
    rb"|\x1b[@-_]"
)


def strip_control(data: bytes) -> str:
    data = ANSI_RE.sub(b"", data)
    data = data.replace(b"\x00", b"")
    return data.decode("utf-8", errors="ignore")


def set_winsize(fd: int, rows: int = 40, cols: int = 140) -> None:
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


def write_all(fd: int, data: bytes) -> None:
    total = 0
    while total < len(data):
        total += os.write(fd, data[total:])


def send_line(fd: int, text: str) -> None:
    write_all(fd, text.encode("utf-8") + b"\r")


def send_mission(fd: int, mission: str) -> None:
    payload = b"\x1b[200~" + mission.encode("utf-8") + b"\x1b[201~"
    write_all(fd, payload)
    time.sleep(0.5)
    write_all(fd, b"\r")


def attach_and_prime(screen_name: str, label: str, mission: str, timeout: float, post_wait: float) -> int:
    master, slave = pty.openpty()
    set_winsize(slave)

    env = os.environ.copy()
    if env.get("TERM", "") in ("", "dumb"):
        env["TERM"] = "xterm-256color"

    proc = subprocess.Popen(
        ["/usr/bin/screen", "-x", screen_name],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        preexec_fn=os.setsid,
        env=env,
    )
    os.close(slave)

    flags = fcntl.fcntl(master, fcntl.F_GETFL)
    fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    buffer = b""
    deadline = time.monotonic() + timeout
    last_action = 0.0
    sent_bypass = False
    sent_fullscreen = False
    sent_trust = False
    submitted = False
    submitted_at = 0.0

    print(f"[{label}] attached to {screen_name}", file=sys.stderr)

    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.25)
            if readable:
                try:
                    chunk = os.read(master, 8192)
                except BlockingIOError:
                    chunk = b""
                if chunk:
                    buffer = (buffer + chunk)[-60000:]

            if proc.poll() is not None:
                print(f"[{label}] screen client exited before priming finished", file=sys.stderr)
                sample = strip_control(buffer[-2000:])
                if sample.strip():
                    print(f"[{label}] recent screen text:\n{sample[-1200:]}", file=sys.stderr)
                return 1

            plain = strip_control(buffer).lower()
            now = time.monotonic()

            if not sent_bypass and (
                "bypass permissions" in plain
                or "warning: claude code running" in plain
                or "yes, i accept" in plain
            ):
                send_line(master, "2")
                sent_bypass = True
                last_action = now
                print(f"[{label}] accepted bypass warning with numeric 2", file=sys.stderr)
                time.sleep(1.0)
                continue

            if not sent_fullscreen and "fullscreen" in plain and ("renderer" in plain or "try" in plain):
                send_line(master, "2")
                sent_fullscreen = True
                last_action = now
                print(f"[{label}] declined fullscreen renderer prompt with numeric 2", file=sys.stderr)
                time.sleep(1.0)
                continue

            if not sent_trust and "trust" in plain and ("folder" in plain or "workspace" in plain or "files in this" in plain):
                send_line(master, "1")
                sent_trust = True
                last_action = now
                print(f"[{label}] accepted folder trust prompt", file=sys.stderr)
                time.sleep(1.0)
                continue

            remote_active = "/remote-control is active" in plain or "remote-control is active" in plain
            readyish = remote_active or ("remote control" in plain and "active" in plain)
            if not submitted and readyish and now - last_action > 1.5:
                time.sleep(0.8)
                send_mission(master, mission)
                submitted = True
                submitted_at = time.monotonic()
                print(f"[{label}] pasted mission prompt and submitted it", file=sys.stderr)
                continue

            if submitted and now - submitted_at >= post_wait:
                write_all(master, b"\x01d")
                time.sleep(0.5)
                print(f"[{label}] detached after post-submit wait", file=sys.stderr)
                return 0

        if submitted:
            write_all(master, b"\x01d")
            time.sleep(0.5)
            print(f"[{label}] detached after timeout; prompt had been submitted", file=sys.stderr)
            return 0

        sample = strip_control(buffer[-4000:])
        print(f"[{label}] timed out before detecting a ready remote-control prompt", file=sys.stderr)
        if sample.strip():
            print(f"[{label}] recent screen text:\n{sample[-1200:]}", file=sys.stderr)
        return 1
    finally:
        try:
            if proc.poll() is None:
                write_all(master, b"\x01d")
                time.sleep(0.2)
        except OSError:
            pass
        try:
            os.close(master)
        except OSError:
            pass
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Prime a Claude Code remote-control screen through a real PTY.")
    parser.add_argument("--screen", required=True, help="screen session name to attach to")
    parser.add_argument("--label", default="claude", help="label for log messages")
    parser.add_argument("--mission", default=os.environ.get("PORTFOLIO_RC_MISSION", DEFAULT_MISSION))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--post-wait", type=float, default=14.0)
    args = parser.parse_args()

    return attach_and_prime(args.screen, args.label, args.mission, args.timeout, args.post_wait)


if __name__ == "__main__":
    raise SystemExit(main())
