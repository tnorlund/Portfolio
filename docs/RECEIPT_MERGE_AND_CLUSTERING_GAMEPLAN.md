# Receipt merge batch + clustering optimization gameplan

**Date:** 2026-07-15  
**Env:** dev (`merge-receipt-dev-merge-receipt`)  
**Code:** [PR #1160](https://github.com/tnorlund/Portfolio/pull/1160) (place clone + upsert) deployed to dev via CodePipeline (surgical rebuild; full `pulumi up` avoided due to stack drift).  
**Raw results:** `/tmp/merge_batch_results.json`

---

## 1. Goal

Clean **over-segmented** receipts: when OCR/detection splits **one physical slip** into multiple `receipt_id`s on the same image, merge them into a single receipt with:

1. Warped combined image (CDN + raw)
2. Migrated labels + place
3. Embeddings + compaction → Chroma
4. Original fragments deleted

Then capture what we learned into a **clustering optimization gameplan**.

---

## 2. Deploy confirmation (prerequisite)

| Check | Result |
|--------|--------|
| Lambda | `merge-receipt-dev-merge-receipt` |
| Image digest | `sha256:e84bb5e2…` (was `9ef2c019…`) |
| LastModified | 2026-07-15T22:46:46Z |
| Table / Chroma | `ReceiptsTable-dc5be22` / `chromadb-dev-shared-buckets-vectors-c239843` |
| Place path | `clone_receipt_place_for_receipt` + `upsert_receipt_place` in image |

**Note:** Full-stack Pulumi preview against main wanted ~173 deletes (stack drift from async-docker branch). Dev deploy used **merge-receipt CodePipeline only** (upload context → build → `update-function-code`).

---

## 3. Candidate taxonomy (important)

From `merge_candidates_live.json` (~48 two-rid images), most are **not** classic over-seg:

| Class | Meaning | Action |
|--------|---------|--------|
| **MERGE** | Same physical slip split (header/body, body/amounts, itemized/EMV, seat stub, near-duplicate copy) | `merge_receipts` dry_run → apply |
| **MULTI_SLIP** | Two real transactions on one photo (different totals/times/dates) | Leave alone (or future multi-receipt UX) |
| **JUNK_FRAGMENT** | Promo/QR/bag text falsely detected as receipt | `delete_receipt` (not merge) |
| **ALREADY_MERGED** | Orphan residual after prior partial merge | Hygiene only |

**Heuristic that worked for MERGE:** complementary content, **same merchant + same total or same auth/txn id**, often one fragment with few words (stub) and one with body.

**Anti-pattern (skip):** same merchant but **different grand totals / different times** (e.g. Twisted Oak $76.68 vs $9.12).

---

## 4. Merges applied (this session)

All via MCP `merge_receipts` with **dry_run first**, then `dry_run=false`. Every apply returned `status: success`, `compaction_run_id`, and `deleted_receipts`.

| Merchant | Image | From → To | Words | Compaction | Pattern |
|----------|-------|-----------|-------|------------|---------|
| Black Tap (Nashville) | `5195dba0-…` | 1+2 → **3** | 92 | `d1c77bdd-…` | Item body + amount column |
| The Novo | `ec9ced37-…` | 1+2 → **3** | 59 | `fa797f77-…` | Ticket body + seat stub |
| Carousel | `c137dfd9-…` | 1+2 → **3** | 89 | `194b3c6f-…` | Partial draft + full sales draft |
| Italia Deli | `74a4f6ee-…` | 1+2 → **3** | 111 | `c77d1a09-…` | EMV payment + itemized order |
| Sushi Planet | `4efce3f8-…` | 1+2 → **3** | 141 | `52f57ee9-…` | Payment slip + itemized ticket |
| Zen Leaf ATM | `3404eeb0-…` | 1+2 → **3** | 88 | `442da9b6-…` | Duplicate ATM slip |

### Spot-check post-merge

- **Black Tap r3:** Combined Tailgate item + $8.82 total + EMV text.
- **Novo r3:** Ticket + seat/GA IDs + $20.00.
- **Italia r3:** Sandwiches/salads + tip $4.62 + total $35.43 + EMV block.

### Related hygiene (not merge)

| Action | Image | Note |
|--------|-------|------|
| Prior Roosterfish | `4d2c992c-…` | Content already on r3; pre-#1160 place failure; r1/r2 removed manually |
| **Delete** rid 2 | `ed3f3909-…` (Sprouts) | Vons “Schedule & Save” promo fragment mislabeled as Vons place |

### Explicit skip

| Image | Why |
|-------|-----|
| Twisted Oak `06772a51-…` | Multi-slip: $76.68 vs $9.12, different times |

---

## 5. What we proved end-to-end

After #1160 + dev deploy, a clean merge now returns:

```json
{
  "status": "success",
  "new_receipt_id": 3,
  "compaction_run_id": "<uuid>",
  "deleted_receipts": [2, 1],
  "place": "<merchant>"
}
```

That is the full intended pipeline:

`warp → Dynamo write → place upsert → embeddings/compaction → delete originals`

Place “already exists” no longer aborts before Chroma/delete.

---

## 6. Remaining backlog (ops)

1. Re-scan multi-rid images; classify MERGE vs MULTI_SLIP vs JUNK with the rules above.
2. Batch remaining **true MERGEs** only (estimate: small fraction of ~48 two-rid images).
3. Roosterfish r3: confirm embeddings left `NONE` → re-embed or re-merge path if needed.
4. Orphan summaries for deleted rids (GSI/summary lag).
5. Prod deploy of #1160 (same surgical pipeline for `merge-receipt-prod-merge-receipt`).
6. Do **not** full `pulumi up` on dev from main until stack drift is reconciled.

---

## 7. Clustering optimization gameplan

### 7.1 Problem statement

Receipt **detection/clustering** (layout → receipt regions) is over-eager: one slip becomes multiple receipts when:

- Payment strip / tip line is spatially separated
- Seat/barcode stubs sit outside the main box
- Customer + merchant copies of the same txn
- Amount column OCR boxes drift from item names
- Promo/footer panels get their own cluster

Re-OCR / re-segmentation made this more visible (multi-rid rate high on some uploads).

### 7.2 North-star metrics

| Metric | Definition | Target direction |
|--------|------------|------------------|
| **Multi-rid rate** | Images with ≥2 receipts / all images | ↓ for single-slip photos |
| **Over-seg precision** | Human-labeled MERGE pairs / auto-flagged pairs | ↑ |
| **False merge rate** | MULTI_SLIP wrongly merged | → 0 |
| **Post-merge embed success** | New rid words with embedding SUCCESS | → 100% |
| **Orphan place/summary rate** | Places/summaries for deleted rids | → 0 |

### 7.3 Near-term (1–2 weeks)

1. **Classifier on multi-rid images (no model training)**  
   Features: word-count ratio, shared merchant, shared grand_total/auth, bbox IoU/adjacency, time/date equality.  
   Labels: MERGE / MULTI_SLIP / JUNK.  
   Ship as offline script + MCP-assisted review (this batch is the seed labels).

2. **Operator playbook**  
   - Always dry_run  
   - Require complementary content + identity signal  
   - Prefer delete for promo/QR junk  
   - Never merge different totals

3. **Merge pipeline hardening** (follow-ups from #1160)  
   - Idempotent `add_receipt` / lines / words on retry  
   - Stable `new_receipt_id` (don’t allocate N+1 on partial retry)  
   - Consistent read when computing max rid  
   - Optional: auto-reembed if place upserted on orphaned rid

4. **Feedback into detection**  
   Export MERGE pairs as training/regression fixtures: original multi-box outputs should become one box.

### 7.4 Medium-term (detection / clustering)

1. **Post-cluster merge pass in the upload pipeline**  
   After initial clustering, run the same feature classifier; if MERGE score high, call merge lambda (or in-process combine) before user sees data.

2. **Cluster graph features**  
   - Vertical adjacency of payment strip under items  
   - Shared EMV auth / last-4 across boxes  
   - Single continuous paper edge (edge detection / Hough)  
   - Text-line continuity across bbox boundary

3. **Stub-aware clustering**  
   Explicit “payment stub” / “seat stub” modes: attach small right/bottom fragments to nearest large receipt if merchant/total match.

4. **Duplicate-copy collapse**  
   Near-duplicate OCR (edit distance + same total + same time) → keep one, delete other (Zen Leaf pattern).

5. **Re-OCR policy**  
   Re-OCR must not freely re-shard without a merge reconciliation step (known #827 family issues).

### 7.5 Longer-term

1. **Learned merger**  
   Train a small model: pair of receipt regions → merge/not (features + layout embeddings). Supervise with this merge log + future labels.

2. **Active learning**  
   Surface uncertain pairs in glyph-studio / MCP for one-click merge/delete.

3. **Eval harness**  
   Frozen set of multi-rid images with gold MERGE/MULTI/JUNK; CI checks false-merge = 0 and recall on MERGE.

4. **Prod parity**  
   Same merge lambda + place upsert in prod; batch historical over-seg after eval is green.

### 7.6 Recommended sequencing

```text
[done] Place upsert + clone (#1160) + dev deploy
[done] Prove full merge path (compaction_run_id + deleted_receipts) ×6
[next] Offline multi-rid classifier + remaining MERGE batch (conservative)
[next] Prod deploy #1160
[next] In-pipeline post-cluster merge pass (high precision only)
[later] Stub-aware detector + learned merger + eval harness
```

---

## 8. Ops checklist for next merge session

1. Confirm lambda digest is current #1160 build.  
2. Load multi-rid inventory (refresh from Dynamo).  
3. For each candidate: `get_receipt` both rids → classify.  
4. `merge_receipts` dry_run → inspect words/place.  
5. Apply; require `deleted_receipts` + `compaction_run_id`.  
6. Spot-check merged `get_receipt`.  
7. Append to `merge_batch_results.json` (or Dynamo merge audit table).

---

## 9. Summary

- **Dev merge lambda is good:** place path fixed; full success payload observed.  
- **6 classic over-seg merges completed** this session + 1 junk delete + clear multi-slip skips.  
- **Largest remaining problem is not the merge tool** — it is **clustering/detection over-segmentation and multi-slip confusion**.  
- **Gameplan:** label → classify → harden merge retries → post-cluster auto-merge (high precision) → stub-aware detection → learned merger with eval.

---

*Generated 2026-07-15 as part of over-seg cleanup after PR #1160.*
