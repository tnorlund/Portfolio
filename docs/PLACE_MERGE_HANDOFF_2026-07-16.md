# Place identity + merge cleanup handoff

**Date:** 2026-07-16  
**Env:** AWS `us-east-1`, DynamoDB table **`ReceiptsTable-dc5be22`** (dev)  
**Lambda:** `merge-receipt-dev-merge-receipt` (image digest `sha256:e84bb5e2…` after PR #1160 deploy)  
**Author session:** Grok Build / place-confirm + merge workstream  

This doc is a pass-to-another-agent review of what was done, artifacts on disk, residual risks, and recommended next work.

---

## 1. Goals completed

1. Fix merge-receipt place write so merges complete embeddings + delete originals (PR #1160).
2. Deploy merge-receipt to **dev** (surgical CodePipeline, not full-stack Pulumi).
3. Merge true over-segmented receipt pairs.
4. Audit place identity; fix hard wrongs; backfill empty place fields; clean orphan places.
5. Document results for handoff.

---

## 2. Code / deploy

| Item | Detail |
|------|--------|
| PR | https://github.com/tnorlund/Portfolio/pull/1160 — **merged** to `main` (`735f2d99…`) |
| Fix | `clone_receipt_place_for_receipt` + `upsert_receipt_place`; merge lambda uses them |
| Full `pulumi up` | **Avoided** — preview wanted ~173 deletes (stack drift). Used CodePipeline only |
| Dev pipeline | `merge-receipt-img-pipeline-8a31e19` → ECR `merge-receipt-img-repo-621260e` → Lambda update |
| First build fail | Incomplete context zip (missing `docs/README.md`); second full package upload succeeded |

---

## 3. Merge work

### 3.1 Successful merges (full path: warp → place → embeddings → delete)

| Merchant | Image ID | From → To | Notes |
|----------|----------|-----------|--------|
| Black Tap Nashville | `5195dba0-2b41-4f10-ac24-8ca555883865` | 1+2 → **3** | body + amounts |
| The Novo | `ec9ced37-d4de-4815-b055-d99d96abc253` | 1+2 → **3** | ticket + seat |
| Carousel | `c137dfd9-351c-4d4e-9dae-7950ab7db753` | 1+2 → **3** | draft halves |
| Italia Deli | `74a4f6ee-ac21-4d42-a2bf-e1414b954ae4` | 1+2 → **3** | EMV + items |
| Sushi Planet | `4efce3f8-4370-49a4-a38c-98b8db6c5fb7` | 1+2 → **3** | payment + ticket |
| Zen Leaf ATM | `3404eeb0-531f-49dc-806c-9ec2b18bf640` | 1+2 → **3** | duplicate slip |
| **Neighborly** | `d5a15b22-d73e-4cec-b3bd-18ebb79a19b3` | **1+3 → 4** | amount column + body (see §5) |

Each success returned `compaction_run_id` + `deleted_receipts`.

### 3.2 Not merges

- **Multi-slip** photos (two real receipts) — left alone (e.g. Twisted Oak different totals).
- **Kruse / Imperial hard places** — place-only issues, not over-seg merges.
- Roosterfish was partially merged earlier (r3); r1/r2 cleaned manually pre-#1160.

### 3.3 Artifact

- `/tmp/merge_batch_results.json` (earlier batch; Neighborly merge may not be in that file)
- Gameplan: `docs/RECEIPT_MERGE_AND_CLUSTERING_GAMEPLAN.md`

---

## 4. Place identity work

### 4.1 Coverage (final after orphan cleanup)

| Metric | Value |
|--------|------:|
| Receipts | ~836 |
| Places | ~835 |
| **Orphan places** | **0** |
| Receipts missing place | **1** (`abaec508-…` empty Unknown receipt) |

### 4.2 Hard place fixes (step 2) — written

| Case | Result |
|------|--------|
| Cafe Nouveau ×2 | → **Café Nouveau**, 1497 E Thompson Blvd, Ventura, (805) 648-1422 |
| In-N-Out Barstow | → **2821 Lenwood Rd, Barstow** |
| CVS Agoura | → **2791 Agoura Rd**, (805) 495-4938 |
| WF stub (initial) | Copied WF from sibling — **later corrected** via Neighborly merge (§5) |

**Not written (by design):**

| Case | Why |
|------|-----|
| Kruse ×2 | Palisades shop burned; keep company `place_id` (SM). User confirmed keep old place_id |
| Imperial parking r2 | No good Google POI for 1625 E Thousand Oaks parking |

Artifacts: `/tmp/place_step2_dry_run_final.json`, `/tmp/place_step2_write_results.json`

### 4.3 Field backfill — written

- **154** live places with empty address and/or phone, **79** unique `place_id`s.
- Places **details** API fill only; **did not** change `place_id` / `merchant_name`.
- Post-state: **832/835** live places have both address and phone; **0** empty addresses.
- Residual no phone (Google has none): 614 Gravier St ×2, Huntington Beach House.
- Log attempt: `/tmp/place_backfill_summary.json` (script hit a late `Counter` NameError after writes; state re-verified by re-query).

### 4.4 Orphan place cleanup — written

**Pass 1 (SAFE_DELETE, 58):** places with no receipt entity but other receipts on same image (incl. merge leftovers).  
Artifact: `/tmp/place_orphan_delete_results.json`

**Pass 2 (KEEP policy, 36 + 1 rescan):**

```
Policy: delete RECEIPT_PLACE when RECEIPT entity is missing.
Skip when RECEIPT entity still exists.
Do not delete receipts, images, or residual LINE/WORD rows.
```

| Bucket deleted | n |
|----------------|--:|
| only_place_identity | 12 |
| residual_lines | 6 |
| multi_or_no_entity | 18 |
| rescan extra | 1 |
| skipped (receipt exists) | 1 (then rescan handled CDN-thin case) |

**Final: 0 orphan places.**  
Artifact: `/tmp/place_orphan_policy_results.json`, `/tmp/place_orphan_audit.json`

---

## 5. Special case: `d5a15b22` (Sushi / WF / Neighborly)

**Image:** `d5a15b22-d73e-4cec-b3bd-18ebb79a19b3`

| Rid | Role | Outcome |
|-----|------|---------|
| 1 | Amount-only `$5.50 / $0.40 / $5.90` | Merged into 4 |
| 2 | Full **Whole Foods** yogurt $5.29 | **Unchanged** |
| 3 | **Neighborly** cookie (amounts clipped) | Merged into 4 |
| 4 | Merged Neighborly body + amounts | **Live** |

**Visual review** of crops showed rid1 was Neighborly’s amount column, **not** WF. Initial step-2 copy of WF place onto rid1 was wrong; merge 1+3 fixed it.

**Final image state:**

- rid2: Whole Foods, 740 N Moorpark  
- rid4: Neighborly, 4000 E Thousand Oaks Blvd, cookie $5.90  
- Orphan places for rid1/3 deleted after merge  

---

## 6. Important operational notes for reviewers

1. **MCP `get_receipt` merchant** sometimes shows `Unknown` even when `RECEIPT_PLACE.merchant_name` is set — verify Dynamo place row, not only MCP label.
2. **Full-stack Pulumi from main** is dangerous on this stack (large delete preview). Prefer targeted pipelines.
3. **Merge pipeline gap:** merge lambda should delete source-rid places when writing survivor (we cleaned leftovers manually twice).
4. **Multi-rid ≠ merge:** always check totals/merchants; amount-column over-seg can look like a foreign stub (this WF case).
5. **Toll-free phones** are not location identity (In-N-Out 800# is fine for brand, not store).

---

## 7. Residual backlog (not done)

| Item | Notes |
|------|--------|
| Empty Unknown receipt | `abaec508-6730-4d75-9d48-76492a26a168` rid1 — no place |
| 3 places phone still empty | Google POI has no phone (Gravier ×2, HB House) |
| Imperial TO r2 | Manual place or leave |
| Kruse | Keep SM company place_id; optional OCR Palisades note only |
| Residual LINE rows under deleted rids | Streams/compactor; places gone |
| Merchant name sprawl | Trader Joe’s / Roast / CVS variants |
| Prod deploy #1160 | Dev only so far for merge lambda |
| In-pipeline post-cluster merge | Clustering gameplan — not implemented |

---

## 8. Key local artifacts (`/tmp`)

| File | Contents |
|------|----------|
| `place_remaining_state.json` | Pre-cleanup residual snapshot |
| `place_orphan_audit.json` | SAFE vs KEEP classification |
| `place_orphan_delete_results.json` | 58 SAFE deletes |
| `place_orphan_policy_results.json` | KEEP policy + final 0 orphans |
| `place_fail49_verify.json` | Old FAIL49 FIXED/STILL_WRONG |
| `place_uncertain_reaudit.json` | UNCERTAIN re-verdicts |
| `place_soft_validation_audit.json` | UNSURE / no-validation audit |
| `place_step2_dry_run_final.json` | Hard-fix dry-run decisions |
| `place_step2_write_results.json` | 5 hard writes |
| `place_backfill_candidates.json` | Backfill inputs |
| `place_backfill_summary.json` | Backfill outcome summary |
| `merge_batch_results.json` | Early merge batch |
| `wf_img_review/` | Cropped PNGs for d5a15b22 review |

Repo docs:

- `docs/RECEIPT_MERGE_AND_CLUSTERING_GAMEPLAN.md`
- `docs/PLACE_MERGE_HANDOFF_2026-07-16.md` (this file)

---

## 9. Suggested review checklist for next agent

1. Confirm orphan count still 0: GSITYPE `RECEIPT_PLACE` vs `RECEIPT` key set difference.  
2. Spot-check Dynamo places for Café Nouveau, Barstow In-N-Out, CVS Agoura, Neighborly r4, WF r2.  
3. Open CDN crops for `d5a15b22` rid2 + rid4; confirm two merchants, one Neighborly with $5.90.  
4. Sample 10 random places that were empty-address; confirm address/phone filled and `place_id` unchanged.  
5. Do **not** full `pulumi up` without reviewing delete preview.  
6. If continuing: Imperial manual, empty Unknown receipt, prod merge-lambda deploy, merge pipeline place cleanup in code.

---

## 10. Policy summary (orphans) — what we chose

| Situation | Action taken |
|-----------|----------------|
| Place without receipt entity | **Delete place** |
| Place with receipt entity | **Keep** |
| Place-only image (no receipts) | **Delete places** (lose merchant tag on empty image shell) |
| Residual LINE/WORD under dead rid | **Delete place only**; leave child rows for streams |
| Receipts / images | **Never bulk-deleted** in this pass |

Rationale: limited win keeping zombie places; merchant identity without a receipt is noise for analytics; residual lines should compact via existing pipeline.

---

*End of handoff.*
