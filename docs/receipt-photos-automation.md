# Receipt Photos workflow for Claude Code and Codex

A reusable agent workflow audits Photos against the dev receipt project and
maintains a private source-photo ledger. Scheduled runs audit and report.
An attended import can export, back up, upload, verify, and correct the receipts
requested by the user. Neither mode deletes Photos content.

## Run it

From a checkout containing this PR:

- **Codex:** `$receipt-photos audit the full Photos library and resume the saved checkpoint.`
- **Claude Code:** `/receipt-photos audit the full Photos library and resume the saved checkpoint.`
- **Import after review:** invoke the same skill with `Import the confirmed new receipts into dev, back up their originals, and verify them through MCP.`

The canonical skill is `.agents/skills/receipt-photos/SKILL.md`; the Claude
entry is a relative directory symlink at `.claude/skills/receipt-photos`.
If the skill has not appeared in the current session, start a new session in
this checkout or explicitly ask the agent to read the canonical file. It has
the same instructions in both clients. No model override is required.

This PR installs the workflow and deterministic inventory/report helpers. It
does not install a Photos connector, an unattended uploader, or a scheduler.
Photos traversal still uses the connected native-app tool and receipt QA uses
the connected receipt MCP tools. If a client lacks Photos access, it can audit
previously exported files and project state but must report library coverage
as partial. A locked Mac blocks native Photos traversal; it does not block
read-only project inventory or report preparation.

## Private state and run ownership

Choose one absolute state directory outside Git and reuse it in both clients,
for example `~/Documents/ReceiptAutomation`. Store:

- `library.json`: enumerated source photos and classification evidence.
- `ledger.json`: source revisions, import attempts, receipt IDs, duplicate
  relationships, QA attestations, and separate backup evidence.
- `runs/<unique-run-id>/`: immutable project snapshots, MCP responses,
  checkpoints, reports, and the exact input manifests for each report.
- Original exports and backups outside the repository, as described in
  [the backup runbook](receipt-photos-backup.md).

Before changing the shared ledger, exporting, or importing, atomically create
`active-run.lock/` inside the state directory with `mkdir` (without `-p` on
the lock). Write the owner/run ID, start time and mode inside it. If it already
exists, report the owner and do no overlapping work. A crashed run leaves its
lock; inspect its owner and outstanding jobs before removing that exact stale
lock. Never steal a lock based only on its age. Release only your own lock when
finished. Pure snapshots/reports can use independent run directories.

Write ledger/checkpoint updates through a temporary file, flush/fsync, then
rename in the same directory. Keep the previous revision and an append-only
run journal. Never replace a valid ledger with an empty one after a read error.
Audit helper outputs use exclusive creation, so a rerun cannot overwrite
previous evidence. Partial or malformed output is not a successful checkpoint.

When changing hosts, stop the current owner before starting another run.
A lock in a cloud-synced folder is not an atomic lock across two machines.
Verify the transferred files by hash, preserve earlier evidence paths, and
record which host owns the writable state. Re-establish the Photos selection
from source evidence on the destination; an open dialog, grid position or
timestamp alone cannot be transferred. Verify its Photos/MCP access separately
from SSH reachability. An online shell does not prove native Photos access.

## Audit sequence

1. Read `AGENTS.md`, the saved checkpoint and ledger. Verify tool availability.
   Use the configured Photos/native-app tool; do not use private Photos SQLite
   files, filesystem guesses about the library, or a lock/privacy bypass.
   Stop a blocked scheduled run after recording its blocker and doing any
   independent inventory work. Do not repeatedly ask for an unlock.
2. Enumerate the requested scope. A full scan covers every accessible photo,
   including screenshots and uncertain or unreadable assets. Count videos,
   hidden/locked content, unavailable iCloud assets, and other exclusions
   separately; unresolved access prevents an unqualified entire-library claim.
   Do not download all full-resolution originals merely to classify candidates.
   Use available previews and bounded batches, recording the resume position,
   observed IDs and the native library count. A keyword search or scrolling a
   recent-date window does not certify a full scan.
   Compare the library footer, filtered Select All count and actual export
   count. Record Personal/Shared Library scope, media filters and whether
   View > Shared with You is included. That display can add Messages attachments
   to Select All without increasing the library footer. Compare the counts
   with it excluded, record those additional photos/videos separately, then
   restore the original scope. Counting may replace the pending export
   selection; update its checkpoint before any later export.
   Record disagreements, inaccessible scope and ambiguous source
   identities in `enumeration_issues`; do not choose whichever denominator
   makes the scan appear complete. Clear an issue only after saving evidence
   that resolves it. Confirm each batch's exported files decode and match its
   selection count before advancing the checkpoint. A closed export dialog
   does not mean the background export has finished.
3. Classify each photo as `receipt`, `not_receipt`, `uncertain`, or `unavailable`
   with a reference to the actual inspection evidence. Do not infer that a
   blank/unreadable image is a non-receipt. Record source identity and revision;
   use Photos identifiers when the authorized interface exposes them. Otherwise
   use an explicitly documented export-derived identity from capture metadata
   and content hashes; do not invent a Photos identifier. Filenames alone are
   insufficient, and ambiguous source identities stay unresolved.
   Preserve separate export occurrences when multiple selected entries produce
   identical bytes and capture metadata. A content hash identifies bytes, not
   necessarily one Photos asset. Record the collision and each occurrence's
   export path; do not silently deduplicate the inventory or invent stable
   library IDs. Keep source multiplicity unresolved in `enumeration_issues`
   until authorized Photos evidence establishes the distinct source assets.
4. Take a complete, paginated dev snapshot, then cross-check its receipt IDs
   with the working MCP summary tool. Investigate missing/extra IDs without
   deleting them. Seven images without receipt rows and four orphan summaries
   were observed in the initial audit; these are historical evidence, not
   constants or proof that seven library receipts are missing.
5. Match existing imports. Exact upload hashes can link the same exported
   bytes. A HEIC original, JPEG export, edited version and crop may have different
   hashes. Use visual comparison and corroborating printed transaction details
   for those candidates. Merchant/date/amount or perceptual similarity alone
   cannot establish identity. Multiple photos of one transaction need a
   reviewed `duplicate_of` link, retaining both source photos.
   A failed date/amount search does not establish absence. Broaden to merchant
   candidates without those filters, including undated records, and inspect
   raw MCP words and candidate images. Summary extraction can mistake a return
   deadline for the purchase date or an item price for the total. Corroborate
   printed transaction references, products and tender details; inspect the
   source and existing crop together. Record an existing import even when its
   summary is wrong, with content QA still pending or failed. Preserve earlier
   evidence when correcting a ledger link. If broader matching is unfinished,
   keep the candidate unresolved and block an automatic upload retry.
6. Reconcile the manifests with the helper below. Report all categories and
   the denominator, including partial enumeration and unresolved assets. A
   missing project hash is unknown evidence, not evidence of a new purchase.
   Do not import anything during an audit-only run.

Use small resumable batches sized for the available time, storage and tool
latency. Stop cleanly at the run budget with `enumeration_complete: false`.
A later incremental scan may reuse the classification of unchanged **verified**
sources, but must refresh receipt MCP QA for each new project snapshot and
revisit unresolved candidates and in-flight jobs. Newly synced or edited assets
invalidate a timestamp-only high-water mark; periodically do a full reconciliation.

## Import and verify an authorized batch

Acquire the shared lock and reconcile every candidate against current project
state before beginning. Export unmodified originals plus all Live Photo and
metadata companions. Create a new verified local backup and test restoration
before uploading. Keep independent off-device recovery evidence separate.

Persist `upload_state: in_flight`, the source revision, export SHA-256, and
request time **before** requesting an upload URL. Record the returned image ID,
OCR job ID and S3 key durably **before** PUT/uploading bytes; do not save signed
URLs or credentials in the ledger. If the request or its response is ambiguous,
mark it `uncertain`, inspect existing jobs/objects, and stop automatic retry.
Never infer failure solely from a timeout, missing summary, or missing Image row.
The current legacy `batch_upload_receipts.py` saves mappings at the end and is
therefore **not an unattended idempotent uploader**. Use an interface that
exposes/persists the stages or do a bounded attended import with immediate
reconciliation; do not wrap that batch script in blind retries.

Follow [the evaluation runbook](receipt-photos-backup.md#evaluate-the-receipt-run).
Use real MCP readbacks to verify crops, full words/sections, merchant location,
dates, item names, quantities, discounts, tax and primary total. Check names and
prices against source images even when arithmetic matches. Keep informational
savings distinct from applied negative adjustments and repeated tender amounts
separate from the receipt's primary total. An unprinted subtotal remains absent.

Before corrections, snapshot affected records. Correct only the reviewed
receipts; refresh their summaries and vectors. Summary changes can trigger
asynchronous item recomputation, so let it settle before final item edits and
perform a second MCP readback. Fetch/decode every advertised CDN variant and
compare uploaded source bytes to the local export. Missing full-size AVIF can
be intentional above the encoder limit; check advertised fields rather than
inventing a fixed variant count. Only then record a passed verification tied
to the source revision and exact receipt IDs, with source/MCP evidence paths.

Pipeline code repairs belong in a reviewed PR; this workflow never deploys or
merges them. Never substitute production when dev is unavailable. Photos cleanup
is outside this automation even when processing and backups pass.

## Deterministic helpers and input contract

From the repository root with the project environment active:

```sh
python scripts/receipt_photo_audit.py snapshot --output /private/state/runs/RUN/project.json
python scripts/receipt_photo_audit.py report \
  --library /private/state/runs/RUN/library.json \
  --ledger /private/state/runs/RUN/ledger.json \
  --project /private/state/runs/RUN/project.json \
  --output /private/state/runs/RUN/report.json
```

Use real paths and a unique run ID. The snapshot pins account `681647709217`,
region `us-east-1`, and dev table `ReceiptsTable-dc5be22`. It performs only reads,
exhausts pagination, unwraps summary records, and records structural orphans.
Update these pins deliberately if dev infrastructure changes. Neither helper
accesses Photos, uploads images, modifies AWS, or authorizes cleanup.

All inputs have `schema_version: 1`. `project.json` is produced by `snapshot`.
For a synthetic, one-photo example, the other inputs are:

```json
{
  "schema_version": 1,
  "scope": "full",
  "enumeration_complete": true,
  "enumeration_issues": [],
  "expected_assets": 1,
  "assets": [{
    "asset_key": "library-id:stable-source-id",
    "revision": "sha256:SOURCE_HASH",
    "classification": "receipt",
    "evidence": ["/private/state/runs/RUN/source-review.json"]
  }]
}
```

```json
{
  "schema_version": 1,
  "photos": [{
    "asset_key": "library-id:stable-source-id",
    "revision": "sha256:SOURCE_HASH",
    "upload_state": "uploaded",
    "image_id": "IMAGE_UUID",
    "receipt_ids": [1],
    "verification": {
      "revision": "sha256:SOURCE_HASH",
      "receipt_ids": [1],
      "result": "passed",
      "checked_snapshot_at": "EXACT_SNAPSHOT_AT_FROM_REVIEWED_PROJECT",
      "project_fingerprint": "HASH_FROM_REVIEWED_REPORT",
      "evidence": ["/private/state/runs/RUN/mcp-final.json"]
    }
  }]
}
```

For a reviewed duplicate, replace the import/verification fields with
`duplicate_of: TARGET_ASSET_KEY` and a nonempty `duplicate_evidence` list;
retain its own current revision. The target must resolve to a verified current
photo. Cycles, changed sources, uncertain uploads, missing receipt/summary rows,
unreviewed classifications, and missing QA evidence prevent a completion claim.
A not-yet-imported candidate has no ledger entry; a recorded attempt without a
returned ID is unresolved and must not be treated as safe to retry.

After fresh MCP QA, save the project's exact `snapshot_at` as
`checked_snapshot_at` and the current report row's `project_fingerprint` in the
verification record. Every new snapshot requires a fresh content review,
including line items and sections, even when image/receipt/summary metadata
matches. Background reprocessing can change items without changing those
records; arithmetic can still balance while names, quantities or discount
rows regress. The fingerprint binds the attestation to image/receipt metadata
and summary fields, so changed project records require review again. Do not
backfill current fingerprints onto historical QA without checking the current
results. The fingerprint is not a digest of all OCR words, items, or CDN bytes;
use fresh MCP/content checks for reprocessed receipts even if metadata matches.

Evidence references are **review attestations**: the reporter checks their
presence and source/version consistency but does not open the files or repeat
semantic QA. The agent must preserve and inspect the referenced artifacts.
Likewise, `enumeration_complete` comes from the actual Photos traversal; the
helper cannot prove that an agent enumerated the library truthfully. Separate
ledger fields for `local_backup` and `independent_backup` should record manifests,
hashes and restore evidence; processing completeness never implies backup safety.

`enumeration_issues` is an optional list of unresolved issue descriptions
(default `[]`). Any entry blocks full coverage even when the declared expected
count matches the manifest. This field preserves observed blockers; it does
not independently discover count discrepancies or validate their resolution.

## Optional schedule

Start on demand and validate a complete attended run before enabling a schedule.
Use **one** scheduler for a shared library. Reuse this prompt in a local Codex
scheduled task or a Claude Code Desktop scheduled task:

> Use the receipt-photos skill in this checkout to audit receipt-photo coverage
> in dev. Resume the shared private ledger and checkpoint. This is read-only:
> do not upload, correct database records, deploy, merge, send messages, or
> delete Photos content. Enumerate a bounded batch, inspect actual MCP results,
> and save a coverage report. If Photos is locked or unavailable, record the
> blocker, finish independent inventory work, and end the run. Report new
> candidates, unfinished imports, unresolved coverage and backup gaps. Never
> call a partial scan complete.

Choose a time when the Mac is normally awake and unlocked. Keep the local app
running and provide the same private state path and approved Photos/receipt
connections to each run. A cloud-only routine cannot inspect this Mac's Photos
library through a local UI connection. A CLI session loop is not a substitute
for a persistent local schedule. Set cadence/model/permissions in the chosen
client, not by hand-editing its private scheduler files.

Official setup references: [Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Codex scheduled tasks](https://learn.chatgpt.com/docs/automations?surface=app),
[Claude Code skills](https://code.claude.com/docs/en/skills), and
[Claude Code Desktop schedules](https://code.claude.com/docs/en/desktop-scheduled-tasks).
