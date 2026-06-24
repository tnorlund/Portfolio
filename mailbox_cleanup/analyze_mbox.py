#!/usr/bin/env python3
"""Stream-parse Apple Mail .mbox exports and report cleanup candidates.

Reads only message headers (not bodies) so it stays fast and low-memory even on
multi-GB mailboxes. Produces:

  reports/senders.csv   one row per sender: count, size, newsletter?, date range
  reports/summary.md    human-readable top-senders / top-by-size / year buckets

Nothing here touches your live mailbox -- it only reads the static export.

Usage:
  python3 analyze_mbox.py ~/Downloads/INBOX*.mbox
  python3 analyze_mbox.py ~/Downloads            # auto-discovers *.mbox inside
"""
from __future__ import annotations

import csv
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as default_policy
from email.utils import parseaddr, parsedate_to_datetime

# A new message starts at a line like:  From <something> <DayOfWeek> <Mon> ...
# Requiring the asctime day-of-week avoids splitting on body lines that merely
# begin with "From ".
SEPARATOR = re.compile(rb"^From .*\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b")
HEADER_PARSER = BytesParser(policy=default_policy)


@dataclass
class SenderStat:
    count: int = 0
    total_bytes: int = 0
    list_count: int = 0          # messages carrying List-Unsubscribe / List-Id
    first_year: int = 9999
    last_year: int = 0
    subjects: list[str] = field(default_factory=list)  # a few samples

    def add(self, size: int, is_list: bool, year: int | None, subject: str):
        self.count += 1
        self.total_bytes += size
        if is_list:
            self.list_count += 1
        if year:
            self.first_year = min(self.first_year, year)
            self.last_year = max(self.last_year, year)
        if len(self.subjects) < 3 and subject:
            self.subjects.append(subject[:80])


def discover_mboxes(args: list[str]) -> list[str]:
    paths: list[str] = []
    for a in args:
        a = os.path.expanduser(a)
        if os.path.isdir(a) and not a.endswith(".mbox"):
            # a plain directory: find *.mbox/mbox inside
            for name in sorted(os.listdir(a)):
                p = os.path.join(a, name)
                if name.endswith(".mbox"):
                    paths.append(p)
        else:
            paths.append(a)
    # Apple Mail stores the actual data at <name>.mbox/mbox
    resolved = []
    for p in paths:
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "mbox")):
            resolved.append(os.path.join(p, "mbox"))
        elif os.path.isfile(p):
            resolved.append(p)
        else:
            print(f"  ! skipping (not found): {p}", file=sys.stderr)
    return resolved


def parse_message(header_bytes: bytes, size: int, stats: dict[str, SenderStat],
                  year_counts: dict[int, int]):
    msg = HEADER_PARSER.parsebytes(header_bytes, headersonly=True)
    sender = (parseaddr(msg.get("From", ""))[1] or "(unknown)").lower().strip()
    subject = (msg.get("Subject", "") or "").strip()
    is_list = bool(msg.get("List-Unsubscribe") or msg.get("List-Id"))
    year = None
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
        if dt:
            year = dt.year
            year_counts[year] += 1
    except (TypeError, ValueError, IndexError):
        pass
    stats[sender].add(size, is_list, year, subject)


def stream_mbox(path: str, stats: dict[str, SenderStat],
                year_counts: dict[int, int]) -> int:
    """Returns number of messages parsed from this file."""
    count = 0
    header_buf = bytearray()
    in_header = True
    cur_size = 0
    have_message = False

    def flush():
        nonlocal count
        if have_message:
            parse_message(bytes(header_buf), cur_size, stats, year_counts)
            count += 1

    with open(path, "rb") as fh:
        for line in fh:
            if SEPARATOR.match(line):
                flush()
                header_buf = bytearray()
                in_header = True
                cur_size = len(line)
                have_message = True
                continue
            cur_size += len(line)
            if in_header:
                if line in (b"\n", b"\r\n"):
                    in_header = False
                else:
                    header_buf += line
        flush()
    return count


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def write_reports(stats: dict[str, SenderStat], year_counts: dict[int, int],
                  total: int, out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    rows = sorted(stats.items(), key=lambda kv: kv[1].count, reverse=True)

    csv_path = os.path.join(out_dir, "senders.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sender", "count", "total_bytes", "total_human",
                    "is_newsletter", "first_year", "last_year", "sample_subject"])
        for sender, s in rows:
            w.writerow([sender, s.count, s.total_bytes, human(s.total_bytes),
                        "yes" if s.list_count >= max(1, s.count // 2) else "no",
                        s.first_year if s.first_year != 9999 else "",
                        s.last_year or "",
                        s.subjects[0] if s.subjects else ""])

    by_size = sorted(stats.items(), key=lambda kv: kv[1].total_bytes, reverse=True)
    newsletters = [(k, v) for k, v in rows
                   if v.list_count >= max(1, v.count // 2)]
    total_bytes = sum(v.total_bytes for v in stats.values())

    md = os.path.join(out_dir, "summary.md")
    with open(md, "w") as fh:
        fh.write(f"# Mailbox analysis\n\n")
        fh.write(f"- **Messages:** {total:,}\n")
        fh.write(f"- **Total size:** {human(total_bytes)}\n")
        fh.write(f"- **Distinct senders:** {len(stats):,}\n")
        fh.write(f"- **Newsletter/list senders:** {len(newsletters):,}\n\n")

        fh.write("## Top 30 senders by message count\n\n")
        fh.write("| Sender | Count | Size | Newsletter | Years |\n")
        fh.write("|---|--:|--:|:--:|---|\n")
        for sender, s in rows[:30]:
            nl = "✉️" if s.list_count >= max(1, s.count // 2) else ""
            yr = f"{s.first_year}–{s.last_year}" if s.last_year else ""
            fh.write(f"| {sender} | {s.count:,} | {human(s.total_bytes)} | {nl} | {yr} |\n")

        fh.write("\n## Top 30 senders by total size\n\n")
        fh.write("| Sender | Size | Count |\n|---|--:|--:|\n")
        for sender, s in by_size[:30]:
            fh.write(f"| {sender} | {human(s.total_bytes)} | {s.count:,} |\n")

        fh.write("\n## Top 40 newsletters (likely safe to bulk-delete)\n\n")
        fh.write("| Sender | Count | Size |\n|---|--:|--:|\n")
        for sender, s in sorted(newsletters, key=lambda kv: kv[1].count,
                                reverse=True)[:40]:
            fh.write(f"| {sender} | {s.count:,} | {human(s.total_bytes)} |\n")

        fh.write("\n## Messages by year\n\n")
        fh.write("| Year | Count |\n|---|--:|\n")
        for y in sorted(year_counts):
            fh.write(f"| {y} | {year_counts[y]:,} |\n")

    print(f"\nWrote {csv_path}")
    print(f"Wrote {md}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    files = discover_mboxes(sys.argv[1:])
    if not files:
        print("No mbox files found.", file=sys.stderr)
        sys.exit(1)

    stats: dict[str, SenderStat] = defaultdict(SenderStat)
    year_counts: dict[int, int] = defaultdict(int)
    total = 0
    for path in files:
        print(f"Parsing {path} ...", flush=True)
        n = stream_mbox(path, stats, year_counts)
        total += n
        print(f"  {n:,} messages", flush=True)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    write_reports(stats, year_counts, total, out_dir)
    print(f"\nTotal: {total:,} messages across {len(files)} file(s), "
          f"{len(stats):,} distinct senders.")


if __name__ == "__main__":
    main()
