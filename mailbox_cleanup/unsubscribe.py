#!/usr/bin/env python3
"""Act on the unsubscribe endpoints found by extract_unsubscribe.py.

Reads reports/unsubscribe.csv and, by default, only acts on senders listed in
delete_list.txt -- so you unsubscribe from exactly what you're deleting.

Modes (default is a dry run that just prints the plan):

  --one-click   POST `List-Unsubscribe=One-Click` to RFC 8058 endpoints.
                Fully automated, no browser, no tracking pixels. Safest.
  --open        Open each http-only unsubscribe URL in your browser to click
                manually (capped by --limit to avoid a tab avalanche).
  --mailto      Open a pre-filled Mail.app compose window per mailto: sender
                (you press Send -- nothing is sent automatically).

Examples:
  python3 unsubscribe.py                       # dry run: show the plan
  python3 unsubscribe.py --one-click           # auto-unsubscribe the easy ones
  python3 unsubscribe.py --open --limit 15     # open 15 link-only ones
  python3 unsubscribe.py --mailto              # draft the mailto: ones

Tip: account notifications (LinkedIn, GitHub, Twitter, Facebook) often point at
a login-required settings page rather than a real unsubscribe -- those are
better silenced in each site's notification settings.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "reports", "unsubscribe.csv")


def load_targets(list_path: str) -> set[str] | None:
    if not os.path.isfile(list_path):
        return None
    targets = set()
    with open(list_path) as fh:
        for ln in fh:
            ln = ln.split("#", 1)[0].strip()
            if ln:
                targets.add(ln.lower())
    return targets or None


def load_rows(targets: set[str] | None) -> list[dict]:
    if not os.path.isfile(CSV_PATH):
        sys.exit(f"Missing {CSV_PATH} -- run extract_unsubscribe.py first.")
    rows = []
    with open(CSV_PATH) as fh:
        for r in csv.DictReader(fh):
            sender = r["sender"]
            if targets is None or any(t in sender for t in targets):
                rows.append(r)
    return rows


def post_one_click(url: str) -> tuple[bool, str]:
    data = b"List-Unsubscribe=One-Click"
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "mailbox-cleanup/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 400, f"HTTP {resp.status}"
    except Exception as e:  # noqa: BLE001 - report any failure, keep going
        return False, str(e)[:120]


def compose_mailto(addr: str, subject: str):
    """Open (not send) a pre-filled Mail.app compose window."""
    subject = subject or "unsubscribe"
    script = f'''
    tell application "Mail"
        set m to make new outgoing message with properties {{subject:"{subject}", content:"unsubscribe", visible:true}}
        tell m to make new to recipient at end of to recipients with properties {{address:"{addr}"}}
        activate
    end tell'''
    subprocess.run(["osascript", "-e", script], check=False)


def main():
    flags = set(a for a in sys.argv[1:] if a.startswith("--"))
    limit = 9999
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    targets = load_targets(os.path.join(HERE, "delete_list.txt"))
    rows = load_rows(targets)
    scope = "delete_list.txt senders" if targets else "ALL senders"
    print(f">>> scope: {scope} -- {len(rows)} matching senders with unsubscribe info\n")

    one_click = [r for r in rows if r["method"] == "one-click"]
    http_only = [r for r in rows if r["method"] == "http"]
    mailto = [r for r in rows if r["method"] == "mailto"]
    print(f"    one-click: {len(one_click)}   http-link: {len(http_only)}   "
          f"mailto: {len(mailto)}\n")

    if "--one-click" in flags:
        print(f">>> POSTing one-click unsubscribe to {len(one_click)} senders\n")
        ok = 0
        for r in one_click[:limit]:
            success, msg = post_one_click(r["http_url"])
            ok += success
            print(f"  [{'OK ' if success else 'FAIL'}] {r['sender']:45s} {msg}")
        print(f"\n>>> {ok}/{min(len(one_click), limit)} succeeded")
    elif "--open" in flags:
        targets_open = http_only[:limit]
        print(f">>> opening {len(targets_open)} link-only unsubscribe pages")
        for r in targets_open:
            subprocess.run(["open", r["http_url"]], check=False)
            print(f"  opened {r['sender']}")
    elif "--mailto" in flags:
        targets_m = mailto[:limit]
        print(f">>> drafting {len(targets_m)} mailto: unsubscribes (you press Send)")
        for r in targets_m:
            compose_mailto(r["mailto_addr"], r["mailto_subject"])
            print(f"  drafted -> {r['mailto_addr']} (for {r['sender']})")
    else:
        print(">>> DRY RUN. Plan:")
        for label, group in (("one-click (POST)", one_click),
                             ("http link (open)", http_only),
                             ("mailto (draft)", mailto)):
            print(f"\n  -- {label}: {len(group)} --")
            for r in group[:20]:
                ep = r["http_url"] or r["mailto_addr"]
                print(f"     {r['sender']:45s} {ep[:70]}")
            if len(group) > 20:
                print(f"     ... and {len(group) - 20} more")
        print("\n>>> re-run with --one-click / --open / --mailto to act.")


if __name__ == "__main__":
    main()
