# Codex Handoff — Upload Determinism, Round 2

**Branch:** `handoff/codex-determinism-round2`
**Origin conversation (full Claude Code transcript, local disk):**
`/Users/tnorlund/.claude/projects/-Users-tnorlund-vegas-26/8e37906b-7da3-4cc8-b685-505d5d59c685.jsonl`
(JSONL, ~26 MB — the complete session that produced this packet, including every
agent eval, the review round, and the tail repair. Grep it for specifics.)
**Date:** 2026-07-15

This branch is a self-contained work packet. It carries no code changes — only
the state, evidence, and work orders needed to continue the upload-determinism
epic with codex as the sole implementer. Everything a prior Claude session
learned by evaluating your six PRs against real dev data is written down here so
you can pick up where we left off.

---

## TL;DR — what to do, in order

1. **Prereq (human + main-session):** merge `#1154 → #1146 → #1152` to unbreak
   `main` CI and land the gold baselines. Render work (#1155) needs #1152's
   baselines on `main` first. *This is the only step not owned by codex.*
2. **[Work Order 1](work_orders/1_merge_ready_and_d3.md)** — merge the 3 ready
   PRs (#1140, #1150, #1151) and close out D3 (#1149)'s two P2 blockers.
3. **[Work Order 2](work_orders/2_rework_d0_d2.md)** — the two PRs that
   **empirically fail** and need real rework: D2 sections (#1145) and D0
   fingerprint (#1148).
4. **[Work Order 3](work_orders/3_render_calibration.md)** — issue #1155:
   render-quality calibration (Vons/Costco/Sprouts) + `derive_scratch_profile()`
   auto-calibration for long-tail merchants.

Full per-PR review feedback (every inline comment, verbatim) is in
[REVIEW_FEEDBACK.md](REVIEW_FEEDBACK.md). Supporting evidence is in
[evidence/](evidence/).

---

## Context: the two-stream model that produced this

Two agents worked in parallel:

- **This session (Claude)** built and QA'd the **ground truth**: sections, the
  ReceiptRow spine, face maps, refpacks, and a final tail repair.
- **Codex** built the **runtime** that consumes it at upload time (D0–D4 in
  `docs/UPLOAD_DETERMINISM_PLAN.md`).

The ground-truth stream is now **done**. As of this handoff the dev corpus is:

| Fact | Value |
|---|---|
| Dev table | `ReceiptsTable-dc5be22` (prod is `ReceiptsTable-d7ff76a` — **never touched**) |
| ReceiptSections | 5,593 total, **99.70% VALID** (5,576/5,593) after tail repair |
| ReceiptRows | ~29,574, `row_id == primary_line_id`, 100% row-aligned |
| Chroma | lines collection joins sections/rows on `row_id` |
| Face map | regenerated on the healed corpus (#1146) |
| Gold baselines | re-recorded 2026-07-14 (#1152) |

Going codex-only drops nothing — there is no remaining parallel ground-truth
work. Every open item below is codex-executable.

---

## The six PRs at a glance

| PR | Scope | Verdict | Blocking findings |
|---|---|---|---|
| #1140 | D1 rows at ingest | **merge-ready** | 2 P2 (amount regex, re-OCR orphan idempotency) — fix-forward OK |
| #1150 | D4 ReceiptDetails | **merge-ready** | 4 P2 (merchant shape, provenance determinism, double-D3-write) |
| #1151 | Merchant expansion | **merge-ready** | 2 P2 (Target unification no-op, anti-copy predicate too broad) |
| #1149 | D3 reconcile | **merge after 2 P2** | PIL `--no-deps` import crash; hardcoded merchant templates |
| #1145 | D2 sections | **REWORK** | Item body collapses — 58.7% agreement on real uploads (see evidence) |
| #1148 | D0 fingerprint | **REWORK** | Reimplemented canonical primitives; thresholds fit, not derived |

"merge-ready" means no correctness/data-loss blocker; the P2s can be fixed
forward. "REWORK" means the approach itself doesn't hold up against real data.

---

## Standing constraints (non-negotiable)

- **Never write to prod** `ReceiptsTable-d7ff76a`. Dev is `ReceiptsTable-dc5be22`.
- **Never destroy LayoutLM outputs.** D3's invariant: corrections flip
  `validation_status` (INVALID/NEEDS_REVIEW) and append a *new* label row with
  provenance; they never call `delete_receipt_word_label`. This was verified to
  hold in #1149 — keep it that way.
- **No `.npz` committed.** JSON sources only. The anti-copy publish gate is
  mandatory (see the #1151 P2 about not weakening it).
- **The full origin transcript is on local disk** (path at top of this file) if
  you need the reasoning behind any decision here — it is the authoritative source,
  not a claude.ai link.
- **Derive thresholds from measurement, not from the acceptance metric.** Both
  rework PRs failed this. A threshold fit so all training examples pass by
  construction (min-of-winners) bounds nothing; derive from the separation
  between a genuine-match and an impostor distribution.
- **Reuse canonical glyph machinery** in `tools/glyph-studio/py/glyphstudio`
  (`typography.py`, `family_cluster.py`, `glyph_score.py`). Do not re-vendor it —
  #1111 was de-vendored specifically to stop this, and re-implementations don't
  inherit the #1142/#1147 tolerance fixes.
