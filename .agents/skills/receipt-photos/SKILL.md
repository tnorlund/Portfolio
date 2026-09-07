---
name: receipt-photos
description: Audit Apple Photos receipt coverage, resume receipt exports and dev imports, and verify receipt results and original backups. Use for receipt-photo ingestion or library coverage requests; not for general spending analysis or routine photo cleanup.
---

# Receipt Photos

Read `docs/receipt-photos-automation.md` from the Portfolio checkout containing
this skill (resolve the skill-directory symlink first).
Use its audit workflow by default. Import mode requires a request to import or
process receipts; a request to scan or check coverage is read-only. Existing
session authorization remains valid within that scope.

Use the same private state directory in Claude Code and Codex, outside every
Git checkout. Preserve the source-photo ledger and all previous run evidence.
Use `scripts/receipt_photo_audit.py` for paginated project snapshots and
conservative coverage reports. Read its input contract in the runbook before
writing a manifest. A project inventory alone is not a Photos-library scan.

## Decisions that matter

- Inspect Photos with an available, authorized native-app tool. If the Mac is
  locked, access is denied, or that tool is absent, record the blocker and do
  independent project inventory work. Do not bypass the lock, change privacy
  settings, or read the private Photos database. An exported directory can be
  audited but does not establish full-library coverage.
- Record every enumerated photo, including non-receipts, unreadable photos,
  and uncertain candidates. Full-library coverage requires a complete
  enumeration and no unresolved classifications. A Photos search for
  “receipt” is a candidate finder, not an exhaustive inventory.
- Link each receipt photo by stable source identity and revision. Filename,
  merchant/date/total, perceptual similarity, and upload timestamps are
  candidate evidence only. Review duplicate purchases against both images
  and corroborating transaction details. Keep both originals.
- An upload record is not a verified receipt. Check the source image, words,
  sections, merchant/location, dates, product names, quantities, discounts,
  tax, total, and reconciliation through receipt MCP tools. Verify advertised
  image URLs by fetching and decoding them. After corrections, refresh only
  the affected derivatives, let asynchronous work settle, then read back again.
  Repeat content QA for each new project snapshot; unchanged summary metadata
  cannot prove that item names, quantities or discounts stayed correct. Bind
  passed QA to that snapshot's timestamp as well as its project fingerprint.
- Back up unmodified originals and all companions using
  `scripts/backup_receipt_exports.py`, verify hashes, and test restoration.
  Record local backup and independent backup separately. Neither a processed
  crop nor a copy in an unprotected upload bucket is an archival backup.
- Before any import, acquire the shared run lock and reconcile the ledger.
  Persist the request intent and returned image/job IDs before uploading
  bytes. A lost/ambiguous response blocks automatic retry. Do not use the
  legacy batch uploader as an unattended retry engine.
- Scheduled audit authorization does not authorize uploads, data corrections,
  deployments, messages to others, or deletion. Follow the user's explicit
  scope for an attended import. Photos deletion is always a separate exact
  review under `docs/receipt-photos-backup.md`; this automation never deletes.

Use dev only; verify the account/table before receipt API calls. Respect
`AGENTS.md`, including the production prohibition. Keep private images,
metadata, manifests, MCP payloads, and logs out of the public repository.

Return measured coverage, verified/imported/unimported/duplicate/unresolved
counts, backup status, blockers, and the local report path. Never call a
partial or blocked scan complete. Stop a blocked scheduled run after saving
its checkpoint; do not sit in an unlock/retry loop.
