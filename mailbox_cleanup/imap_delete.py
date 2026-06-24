#!/usr/bin/env python3
"""Bulk-delete iCloud Mail by sender, over IMAP -- fast, server-side.

This replaces the Mail.app AppleScript path. Apple offers no mail API, so IMAP
is the programmatic route: the server runs the search and we batch-move matches.

Account: iCloud Mail (imap.mail.me.com). Auth: an app-specific password read
from the macOS Keychain -- nothing secret on disk.

  Keychain setup (run once, yourself):
    security add-generic-password -s mailbox-cleanup -a tnorlund@me.com -w
    # paste the xxxx-xxxx-xxxx-xxxx app-specific password when prompted

Senders come from delete_list.txt (one substring per line; IMAP `SEARCH FROM`
is a case-insensitive substring match, same semantics as the AppleScript tool).

Usage:
  python3 imap_delete.py --test                 # login + list folders, nothing else
  python3 imap_delete.py                         # DRY RUN: count matches per sender
  python3 imap_delete.py --live                  # MOVE matches to "Deleted Messages"
  python3 imap_delete.py --live --purge          # permanently EXPUNGE (irreversible)
  python3 imap_delete.py --mailbox Archive --live

Default is always a dry run. --live moves to Trash (recoverable ~30 days);
add --purge to erase permanently instead.
"""
from __future__ import annotations

import argparse
import imaplib
import os
import subprocess
import sys

IMAP_HOST = "imap.mail.me.com"
DEFAULT_USER = "tnorlund@me.com"
TRASH = "Deleted Messages"          # iCloud's trash folder
KEYCHAIN_SERVICE = "mailbox-cleanup"
HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = 300                         # UIDs per IMAP command


def get_password(user: str) -> str:
    env = os.environ.get("ICLOUD_APP_PASSWORD")
    if env:
        return env.strip()
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", user, "-w"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        sys.exit(
            f"No password found. Add it to Keychain:\n"
            f"  security add-generic-password -s {KEYCHAIN_SERVICE} "
            f"-a {user} -w\n"
            f"(or set ICLOUD_APP_PASSWORD)")


def load_senders() -> list[str]:
    path = os.path.join(HERE, "delete_list.txt")
    senders = []
    with open(path) as fh:
        for ln in fh:
            ln = ln.split("#", 1)[0].strip()
            if ln:
                senders.append(ln)
    return senders


def connect(user: str) -> imaplib.IMAP4_SSL:
    pw = get_password(user)
    M = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    M.login(user, pw)
    return M


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def cmd_test(M: imaplib.IMAP4_SSL):
    print("Login OK.\nCapabilities:", " ".join(
        c.decode() for c in M.capabilities))
    print("\nFolders:")
    typ, data = M.list()
    for line in data:
        print("  ", line.decode(errors="replace"))


def search_uids(M: imaplib.IMAP4_SSL, sender: str) -> list[bytes]:
    typ, data = M.uid("SEARCH", None, "FROM", f'"{sender}"')
    if typ != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def delete_uids(M: imaplib.IMAP4_SSL, uids: list[bytes], purge: bool):
    has_move = any(b"MOVE" in c for c in M.capabilities)
    for batch in chunked(uids, BATCH):
        uid_set = b",".join(batch).decode()
        if purge:
            M.uid("STORE", uid_set, "+FLAGS", r"(\Deleted)")
        elif has_move:
            M.uid("MOVE", uid_set, f'"{TRASH}"')
        else:  # fallback: copy to trash, flag, leave expunge for the end
            M.uid("COPY", uid_set, f'"{TRASH}"')
            M.uid("STORE", uid_set, "+FLAGS", r"(\Deleted)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--mailbox", default="INBOX")
    ap.add_argument("--live", action="store_true",
                    help="actually act; otherwise dry-run count only")
    ap.add_argument("--purge", action="store_true",
                    help="permanently expunge instead of moving to Trash")
    ap.add_argument("--test", action="store_true",
                    help="login and list folders, then exit")
    args = ap.parse_args()

    M = connect(args.user)
    try:
        if args.test:
            cmd_test(M)
            return

        # Trash is selected read-write only when expunging; otherwise INBOX rw.
        M.select(f'"{args.mailbox}"' if " " in args.mailbox else args.mailbox)

        senders = load_senders()
        if not senders:
            sys.exit("delete_list.txt is empty -- nothing to do.")

        mode = ("PURGE (permanent)" if args.purge else "MOVE to Trash") \
            if args.live else "DRY RUN (count only)"
        print(f">>> mailbox={args.mailbox}  mode={mode}  senders={len(senders)}\n")

        total = 0
        flagged_for_expunge = False
        for s in senders:
            uids = search_uids(M, s)
            n = len(uids)
            total += n
            print(f"  {s:45s} {n:>6} match{'es' if n != 1 else ''}")
            if args.live and uids:
                delete_uids(M, uids, args.purge)
                has_move = any(b"MOVE" in c for c in M.capabilities)
                if args.purge or not has_move:
                    flagged_for_expunge = True

        print(f"\n>>> total matched: {total}")
        if args.live:
            if flagged_for_expunge:
                M.expunge()
            print(">>> done."
                  + ("" if args.purge else
                     f"  (moved to '{TRASH}', recoverable ~30 days)"))
        else:
            print(">>> dry run -- nothing changed. Re-run with --live to act.")
    finally:
        try:
            M.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
