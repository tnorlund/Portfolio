# mailbox-cleanup

Tools to analyze an Apple Mail `.mbox` export and bulk-clean a Gmail account
through Mail.app. Built for a ~173k-message inbox.

The work splits into two halves with very different risk profiles:

| Phase | Touches | Risk | Tool |
|-------|---------|------|------|
| **Analyze** | the static `.mbox` export only | none (read-only, offline) | `analyze_mbox.py` |
| **Execute** | your live iCloud mailbox (IMAP) | reversible (moves to Trash) | `imap_delete.py` |

> **Provider:** this export is **iCloud Mail** (`tnorlund@me.com`), confirmed
> from the `Delivered-To` headers — not Gmail. Apple has no mail API, so **IMAP**
> is the programmatic path. The old Mail.app AppleScript tool
> (`delete_by_sender.applescript` / `run_delete.sh`) is kept for reference but is
> too slow at this scale.

## 1. Analyze (safe, offline)

Streams the mbox export reading only headers, so it's fast and low-memory even
on multi-GB files. Ranks senders by message count and by total size, flags
newsletters (anything with `List-Unsubscribe`/`List-Id`), and buckets by year.

```bash
python3 analyze_mbox.py ~/Downloads        # auto-finds INBOX*.mbox
# or point at specific files:
python3 analyze_mbox.py ~/Downloads/INBOX*.mbox
```

Outputs:
- `reports/senders.csv` — every sender with count, size, newsletter flag, year range
- `reports/summary.md` — top senders, top by size, top newsletters, year histogram

## 2. Decide

Open `reports/summary.md`, and copy the senders/domains you want gone into
`delete_list.txt` (one per line; a bare domain nukes the whole domain).

## 3. Execute (IMAP, reversible)

One-time: store an iCloud **app-specific password** (make one at
appleid.apple.com → Sign-In and Security → App-Specific Passwords) in Keychain:

```bash
security add-generic-password -s mailbox-cleanup -a tnorlund@me.com -w
# paste the xxxx-xxxx-xxxx-xxxx password when prompted
```

Then:

```bash
python3 imap_delete.py --test            # verify login, list folders
python3 imap_delete.py                    # DRY RUN: count matches per sender
python3 imap_delete.py --live             # MOVE matches to "Deleted Messages"
python3 imap_delete.py --live --purge     # permanently expunge (irreversible)
```

Default is a dry run. `--live` moves to iCloud's **Deleted Messages** (recoverable
~30 days); `--purge` erases permanently. The server does the search, so it's fast.

## Unsubscribe (so deleted senders stop coming back)

Deleting clears the backlog; unsubscribing stops the future flow. Legitimate
bulk senders advertise a `List-Unsubscribe` header, which we mine straight from
the export.

```bash
# 1. Extract unsubscribe endpoints per sender (offline, safe)
python3 extract_unsubscribe.py ~/Downloads            # -> reports/unsubscribe.csv
#    or restrict to what you're deleting:
python3 extract_unsubscribe.py ~/Downloads --only delete_list.txt

# 2. Act on them (defaults to delete_list.txt senders; dry run by default)
python3 unsubscribe.py                 # show the plan
python3 unsubscribe.py --one-click     # auto-POST RFC 8058 one-click unsubs
python3 unsubscribe.py --open --limit 15   # open link-only pages to click
python3 unsubscribe.py --mailto        # draft mailto: unsubs (you press Send)
```

Three methods, by safety:
- **one-click** — RFC 8058 `POST`. Automated, no browser, no tracking pixel. Safest.
- **http** — a URL with no one-click support; opened in your browser to click.
- **mailto** — opens a pre-filled Mail compose window; *you* send it.

Notes:
- Unsubscribe **before** deleting a sender (the backlog can be trashed after).
- Account notifications (LinkedIn, GitHub, Twitter, Facebook) usually link to a
  login-gated settings page — silence those in each site's settings instead.
- Clicking unsubscribe confirms your address is live; fine for real companies,
  not worth it for outright spam (just delete/block those).

## Why Mail.app AppleScript (not Gmail API / IMAP)
Chosen to ride the existing Mail.app/Okta login — no OAuth client, no app
passwords, no credentials stored. Trade-off: it's UI-bound and slower at scale,
so it's best for targeted sender/domain sweeps rather than per-message churn.

## Safety notes
- Analysis never touches the live account.
- Deletion is dry-run by default and only ever moves to Trash.
- `delete_list.txt`, `reports/`, and any `*.mbox` are git-ignored — no personal
  email content is committed.
