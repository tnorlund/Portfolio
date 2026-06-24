#!/usr/bin/env python3
"""Extract per-sender unsubscribe endpoints from Apple Mail .mbox exports.

For every sender it records the unsubscribe method advertised in the *most
recent* message from them (endpoints rotate, so newest wins):

  - one-click : RFC 8058 -- an HTTPS URL plus `List-Unsubscribe-Post:
                List-Unsubscribe=One-Click`. Safe to automate with a POST.
  - http      : an unsubscribe URL with no one-click support (needs a browser).
  - mailto    : unsubscribe by sending an email to an address.

Reads headers only; never touches the live mailbox.

Usage:
  python3 extract_unsubscribe.py ~/Downloads                 # all senders
  python3 extract_unsubscribe.py ~/Downloads --only delete_list.txt
"""
from __future__ import annotations

import csv
import os
import re
import sys
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as default_policy
from email.utils import parseaddr, parsedate_to_datetime

SEPARATOR = re.compile(rb"^From .*\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b")
HEADER_PARSER = BytesParser(policy=default_policy)

URL_RE = re.compile(r"<\s*(https?://[^>]+?)\s*>", re.I)
MAILTO_RE = re.compile(r"<\s*mailto:([^>]+?)\s*>", re.I)


@dataclass
class Unsub:
    count: int = 0
    last_dt: float = -1.0          # epoch of newest message seen
    one_click: bool = False
    http_url: str = ""
    mailto_addr: str = ""
    mailto_subject: str = ""


def discover_mboxes(args: list[str]) -> list[str]:
    paths = []
    for a in args:
        a = os.path.expanduser(a)
        if os.path.isdir(a) and not a.endswith(".mbox"):
            for name in sorted(os.listdir(a)):
                if name.endswith(".mbox"):
                    paths.append(os.path.join(a, name))
        else:
            paths.append(a)
    out = []
    for p in paths:
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "mbox")):
            out.append(os.path.join(p, "mbox"))
        elif os.path.isfile(p):
            out.append(p)
    return out


def parse_mailto(value: str) -> tuple[str, str]:
    """Return (address, subject) from a mailto: target."""
    m = MAILTO_RE.search(value)
    if not m:
        return "", ""
    target = m.group(1)
    addr, _, query = target.partition("?")
    subject = ""
    for part in query.split("&"):
        if part.lower().startswith("subject="):
            subject = part[len("subject="):]
    return addr.strip(), subject.strip()


def record(stats: dict[str, Unsub], header_bytes: bytes, only: set[str] | None):
    msg = HEADER_PARSER.parsebytes(header_bytes, headersonly=True)
    lu = msg.get("List-Unsubscribe")
    if not lu:
        return
    sender = (parseaddr(msg.get("From", ""))[1] or "(unknown)").lower().strip()
    if only is not None and not any(t in sender for t in only):
        return
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
        ts = dt.timestamp() if dt else 0.0
    except (TypeError, ValueError, IndexError, OverflowError):
        ts = 0.0

    s = stats.setdefault(sender, Unsub())
    s.count += 1
    if ts < s.last_dt:
        return  # keep endpoints from the newest message
    s.last_dt = ts

    post = (msg.get("List-Unsubscribe-Post") or "")
    url_m = URL_RE.search(lu)
    s.http_url = url_m.group(1) if url_m else ""
    s.one_click = bool(url_m) and "one-click" in post.lower()
    s.mailto_addr, s.mailto_subject = parse_mailto(lu)


def stream(path: str, stats: dict[str, Unsub], only: set[str] | None):
    header_buf = bytearray()
    in_header = True
    have = False

    def flush():
        if have:
            record(stats, bytes(header_buf), only)

    with open(path, "rb") as fh:
        for line in fh:
            if SEPARATOR.match(line):
                flush()
                header_buf = bytearray()
                in_header = True
                have = True
                continue
            if in_header:
                if line in (b"\n", b"\r\n"):
                    in_header = False
                else:
                    header_buf += line
        flush()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = None
    if "--only" in sys.argv:
        list_path = sys.argv[sys.argv.index("--only") + 1]
        with open(os.path.expanduser(list_path)) as fh:
            only = set()
            for ln in fh:
                ln = ln.split("#", 1)[0].strip()
                if ln:
                    only.add(ln.lower())
    if not args:
        print(__doc__)
        sys.exit(1)

    files = discover_mboxes(args)
    stats: dict[str, Unsub] = {}
    for p in files:
        print(f"Scanning {p} ...", flush=True)
        stream(p, stats, only)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "unsubscribe.csv")
    rows = sorted(stats.items(), key=lambda kv: kv[1].count, reverse=True)
    methods = {"one-click": 0, "http": 0, "mailto": 0, "none": 0}
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sender", "count", "method", "http_url",
                    "mailto_addr", "mailto_subject"])
        for sender, s in rows:
            if s.one_click:
                method = "one-click"
            elif s.http_url:
                method = "http"
            elif s.mailto_addr:
                method = "mailto"
            else:
                method = "none"
            methods[method] += 1
            w.writerow([sender, s.count, method, s.http_url,
                        s.mailto_addr, s.mailto_subject])

    print(f"\nWrote {csv_path}")
    print(f"Senders with an unsubscribe header: {len(rows):,}")
    for k, v in methods.items():
        print(f"  {k:10s}: {v:,}")


if __name__ == "__main__":
    main()
