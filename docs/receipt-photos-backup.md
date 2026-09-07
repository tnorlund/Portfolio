# Receipt Photos: export, verify, then review cleanup

A processed receipt is not a backup of its source photo. OCR, crops, JPEG
exports and labels can omit information. Keep the Photos originals until
the backup and receipt results have both been checked.

## Export and backup

1. In Photos, identify the exact receipt photos by filename, capture time
   and visual inspection. Export **Unmodified Originals**, including any
   Live Photo companions and metadata sidecars. Export edited/full-size
   JPEG versions separately when needed by the OCR workflow.
2. Copy the exported originals into a new backup directory:

   ```sh
   python scripts/backup_receipt_exports.py create \
     /path/to/exported-originals /path/to/backups/unique-batch-name
   python scripts/backup_receipt_exports.py verify \
     /path/to/backups/unique-batch-name
   ```

   The utility copies every file without transcoding, reads the copies
   back, checks SHA-256 and byte counts, and records a portable manifest.
   Existing destinations are refused. Source changes, missing files,
   unexpected files, symlinks and checksum mismatches fail verification.
   Failed attempts retain their partial copies and never delete sources.
3. Keep an independently recoverable copy on another device or in private
   storage with versioning/retention. A second directory on the same disk
   does not protect against disk loss. Verify that second copy and test
   restoring files from it; open the restored originals before cleanup.

The manifest detects missing or changed bytes. It is not an immutable
archive and cannot protect copies from an account that can delete every
copy and its manifest. A routine upload bucket without versioning or
retention should not be the only remaining copy.

## Evaluate the receipt run

Keep the mapping from exported filename/hash to image ID and OCR job ID.
Use receipt MCP tools to inspect the actual crop and decoded words,
merchant/place, dates, item prices, tax and grand total. An upload or OCR
status of `COMPLETED` alone does not certify the results. Check the stored
line items against the photo and require a reconciled item sum. Back up
records before corrections, then refresh affected summaries and embeddings
and read them back through MCP.

## Review library cleanup separately

Prepare an exact list of Photos filenames and capture times, their backup
locations and checksums, and the corresponding verified receipt IDs.
Show that list to the user before deleting anything. Delete only the
reviewed photos through Photos' normal recoverable deletion flow; do not
empty Recently Deleted or remove the backups as part of this workflow.

Stop cleanup if a source cannot be identified unambiguously, any backup
verification fails, an independent restore has not been demonstrated, or
the receipt result is incomplete. This utility intentionally has no
Photos deletion command. Upload or OCR success never triggers deletion.

## Repeatable audits and imports

Use the [shared Claude Code/Codex workflow](receipt-photos-automation.md) to
maintain source-photo identities, resume library audits, distinguish incomplete
imports from verified receipts, and prepare read-only scheduled checks.
