# Re-OCR Migration Runbook (apply #1095 to existing labeled images)

Status as of 2026-07-11: **G1 gate PASSED** on a full local rehearsal.
New uploads already get the new segmentation (merged #1095 + rebuilt worker).
This runbook covers migrating the ~633 EXISTING labeled dev images, then prod.

## What's proven (G1, local DynamoDB Local rehearsal)

616 images / 58,586 labels via `scripts/ocr_migration_apply.py`:

| moved (audit trail verbatim) | parked NEEDS_REVIEW | pre-orphans preserved | absorbed dead rows | lost |
|---|---|---|---|---|
| 55,213 | 1,869 | 1,494 | 99 | **0** |

Blast radius 0 · new orphans 0 · wiped 0 · rollback = `restore` (byte-exact
verified). Toolkit lives in `scripts/ocr_migration_rehearsal.py` +
`scripts/ocr_migration_apply.py` (43 tests). PR #1109.

## Dev waves (~50 images each)

Per wave, IN ORDER:

1. **Backup** (rollback source, from live dev — full partitions, never
   `--exclude-types`):
   `ocr_migration_rehearsal.py backup --endpoint-url <real> --allow-aws --table-name ReceiptsTable-dc5be22 --images wave.txt --out waveN_backup.sqlite3`
2. **Apply**: `ocr_migration_apply.py --allow-aws --endpoint-url https://dynamodb.us-east-1.amazonaws.com ... --backup waveN_backup.sqlite3`
   — run on the SAME Mac as the rehearsal (Vision reads differ across macOS
   builds). Raw images: fetch per image (17 of 633 have NO raw object — skip
   list in the apply report).
3. **Crops + CDN**: regenerate warped crops, upload raw `receipts/{image}/...`
   + `upload_all_cdn_formats` (12 objects/receipt), update Receipt cdn keys.
4. **CloudFront invalidation** (dev distribution) — `/assets/{image_id}/*` per
   wave image. NOTE: minTtl=24h/defaultTtl=30d on `/assets/*` and NO
   invalidation code exists anywhere in the repo outside prod-deploy CI
   (`main.yml:274`). Also delete orphaned `assets/{image}/{old_receipt_id}*`
   keys when receipt ids disappear.
5. **Harness diff** vs the wave backup (`--expect-parked <apply_report>`).
   Verdict must be PASS.
6. **Streams drain, then RECEIPT_SUMMARY repair**: the updater fires on label
   REMOVEs and will re-upsert EMPTY summaries for OLD receipt ids (and INSERTs
   never create new-id summaries). Sweep orphaned summaries, then
   `scripts/backfill_receipt_summaries.py --env dev`.
7. **Chroma**: VERIFY FIRST (open question below) whether stream deletion
   covers word/line vectors; delete old-id vectors explicitly if not. Kick
   embedding SFs (`start_ingestion_dev.sh both` — they have NO schedule),
   confirm NONE→SUCCESS drains, spot-check old ids gone / new present.
   Wave 1 doubles as the embedding-pipeline canary (words leg has the #990
   stuck-PENDING history).
8. **Derived caches**: label-evaluator viz SF (manual, old-id-keyed);
   force image_details/word+address-similarity generators AFTER Chroma is
   consistent (else they bake stale vectors); LABEL_COUNT_CACHE self-heals.
9. **Local cache re-sync**: `make analytics-cache ENV=dev`.

### Before/after E2E check (wave 1 especially)
Fixed set: Twin Peaks `01024cab`, cream `4e180507`, the 7 June22 uploads,
2 untouched controls. Capture BEFORE and AFTER: API responses
(`receipts`, `images`, counts, `label_validation_count`,
`image_details_cache`) + frontend screenshots of receipt pages. Diff:
counts/bounds/cdn keys change as expected, labels preserved (status+audit),
no crop 404s, no stale crops post-invalidation, controls byte-identical.
Sign-off before wave 2.

## Prod (after dev soak, 2–7 days)

Existing mirror: `reconcile_dev_to_prod.py` — fingerprints flip on every
migrated image → REPLACE (delete-then-recopy; correct for re-keying), Chroma
leg intra-env, embedding_status reset → `start_ingestion_prod.sh both`.
- **Dry-run first**: `guard_replaces` SILENTLY SKIPS prod partitions holding
  non-`RESTORABLE_TYPES` (sections/analyses) — inspect skips, extend the set
  in a reviewed change if needed.
- Promote in slices (`--protect` the rest); prod compaction DLQs empty + the
  pre-apply-only health gate means the flood is on you between slices.
- Nothing syncs raw `receipts/` crops to prod (known: prod raw 404s;
  `cdn_s3_key` is the reliable surface). No prod CloudFront invalidation
  outside deploy CI — invalidate `/assets/*` explicitly after slices.

## Parked-label review (1,869)

Every parked label is `validation_status=NEEDS_REVIEW` with a reasoning note:
`[PARKED by re-OCR migration: no confident word match; original status=...;
original word '...' @ (r,l,w)]` — they flow through the normal labeling
workflow. Concentrated: 370/616 images parked ZERO; top ~20 images carry the
bulk (duplicate-copy merges, garbage-OCR images). Review packet:
`~/.claude/jobs/ce5f2402/tmp/review_packet.html` (worst-first).

## Open questions / deferred

- **Chroma stream deletion conflict**: one audit read
  `receipt_dynamo_stream/parsing/parsers.py` as handling RECEIPT_WORD/LINE
  REMOVEs; another read `chromadb_compaction/lambdas/stream_processor.py` as
  PLACE+LABEL-only. Reconcile before wave 1 — decides explicit vector deletion.
- **1,494 pre-orphaned labels** (pre-existing dev breakage; 586 on image
  `13da1048`): separate cleanup pass, NOT part of migration diffs.
- **RECEIPT_PLACE re-keying on splits** (apply preserves but doesn't re-key;
  re-run `fix_place` for split receipts — dev Places key expired 2026-07-07,
  renew first).
- **Geometry tier for wrong-word relabeling** (same-text swaps invisible to
  the harness; position scoring makes it rare).
- **Crop uploads set no Cache-Control** → browsers heuristically cache old
  crop bytes even after invalidation. Add `CacheControl` at upload later.
- **Worker LayoutLM download crash** (`HTTPClientError.deadlineExceeded` on
  the CoreML bundle) — auto-restart wrapper in place; fix Soto timeout if it
  loops.
- **Mini**: staging leftovers `~/ce5f2402_*` (~6 GB) can be deleted; rescue
  patches from deleted worktrees in `~/worktree_rescue_patches/`; hosts the 4
  GitHub Actions runners (mac2-runner-1..4) — that's its real job.
