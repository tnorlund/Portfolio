# Work Order: Salvage from the closed D-series stack (2026-07-18)

**Context:** The codex D-series draft PRs — #1148 (D0 typeface fingerprint),
#1149 (D3 label reconciliation), #1150 (D4 structured details), #1151
(merchant typeface expansion), #1153 (upload determinism eval) — were closed
on 2026-07-18 after archaeology + mechanics review. Reasons: D0 refuted by
its own evaluator (all 15 fingerprint sources abstain, genuine/impostor
separation −0.10 to −0.48); D3 superseded by the fresh section-label gate
stack (#1178); D4/eval measured not-ready (39.6% strict field recall, all
842 artifacts NEEDS_REVIEW) and built pre-TRANSACTION_INFO against a D2 the
2026-07-18 merges replaced; all five carried the superseded
`section_order_priors_v1.json` and a 17-conflict collision with main's D2.
Branches survive; heads pinned below. Two pieces are worth re-cutting fresh.

## Salvage 1 — Exact-cent arithmetic reconciler (from #1149)

**Source:** branch `codex/d3-label-reconciliation` @ `aab260413`, file
`receipt_upload/receipt_upload/label_reconciliation.py` (978 lines). The
piece to keep is the arithmetic core, not the section/template logic (that
role is now #1178's): `_cents()` parsing, the exact-cent constraint checks
(Σ LINE_TOTAL → SUBTOTAL, SUBTOTAL + TAX → GRAND_TOTAL), and the
`EXCLUDED_FROM_ARITHMETIC` status plumbing.

**Why re-cut, not revive:** the file assumes pre-TXINFO sections and the
pre-merge D2 shape; on current main the same arithmetic belongs on top of
`section_assignment.py`'s outputs and the v2 priors.

**When:** after the TRANSACTION_INFO relabel sweep → priors rebuild → fresh
≥60-row holdout revalidates D2 (see `4_transaction_info.md` §3). Arithmetic
identities are label-taxonomy-stable, so nothing rots meanwhile.

## Salvage 2 — "Target Grocery" → "Target" merchant unification (from #1151)

**Source:** #1151's corpus scan found ~9-10 receipts under merchant
"Target Grocery" that belong with "Target" (same banner; the registry draft
carried `"merchant_aliases": {"target grocery": "Target"}`).

**Why a data fix, not code:** `normalize_merchant_key()`
(`receipt_upload/receipt_upload/section_assignment.py:94`) is deliberately
alias-free, and `scripts/normalize_merchant_names.py` only unifies casing
within a `place_id` group — different names never merge. The unification is
a one-time dev-data operation: repoint the "Target Grocery" ReceiptPlace
records (fix_place MCP tool or a small script), then let the next priors
rebuild absorb the merged corpus. Follow with the standard dev→prod mirror.

**When:** any time; cheapest bundled into the relabel-sweep session above.

## Closed-branch pin list (for future cherry-picking)

| PR | branch | head |
|---|---|---|
| #1148 | codex/d0-typeface-fingerprint | 66c4b888f |
| #1149 | codex/d3-label-reconciliation | aab260413 |
| #1150 | codex/d4-structured-receipt-details | 2347c2243 |
| #1151 | codex/merchant-typeface-expansion | b18dc62f9 |
| #1153 | codex/upload-determinism-evaluation | f4f38a962 |

Other reusable-if-ever-needed modules (no current consumer, listed for
completeness): `structured_details.py` + `ReceiptResolvedDetails` entity
(#1150 @ 2347c2243), the D0 typeface evaluator/registry builder (#1148 @
66c4b888f — remember: its measured verdict was *abstain*).
