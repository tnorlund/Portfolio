# Work Order 1 — Merge the ready PRs + close D3

**Goal:** land the four PRs that work (or nearly do) so the D-pipeline is on
`main`. This is fast, mechanical, and unblocks everything downstream.

**Prerequisite (not codex):** `#1154 → #1146 → #1152` must be merged first —
`#1154` is the black-formatting fix that unbreaks `main` CI, and every open PR
inherits that red check until it lands. Do not fight red `receipt_dynamo` format
checks until #1154 is on `main`.

## 1. Merge #1140, #1150, #1151 (fix-forward the P2s)

These have no correctness/data-loss blocker. Either fix the P2s on-branch before
merge or open immediate follow-ups — your call, but **#1140's re-OCR orphan
idempotency (P2) should be fixed before merge**, because orphaned rows corrupt
the `row_id` join that D2/D3/D4 all depend on. Fix: delete the receipt's prior
`ROW#*` items before the batch put in all three call sites (`refine_receipt`,
`process_native`, the OCRProcessor Swift path).

The rest (#1150's merchant-shape + provenance-determinism P2s, #1151's Target
unification + anti-copy predicate P2s) are safe to fix-forward but cheap to do
now. See [REVIEW_FEEDBACK.md](../REVIEW_FEEDBACK.md) for each, verbatim.

## 2. Close D3 (#1149) — 2 P2 blockers

**P2a — PIL import crash (the real blocker).** The evaluator image builds
`receipt_upload` with `--no-deps`, so Pillow is missing. Importing any
`receipt_upload.*` submodule runs `__init__.py` → `font_analysis` → `from PIL
import Image` → `ModuleNotFoundError` at runtime, so **D3 never runs in the built
image** — it always hits the `except`. Fix options, in order of preference:
  1. Make the D3 import path not transitively import PIL (move the D3 entry point
     out from under the `font_analysis` import chain, or lazy-import PIL inside
     `font_analysis`).
  2. Install Pillow in the evaluator image.
  Then **verify end-to-end in the built Docker image**, not just unit tests — the
  unit tests pass today while the image fails.

**P2b — hardcoded merchant templates.** Replace the inline `"costco"`/`"vons"`/
`"smith" in merchant` substring checks with a declarative template registry keyed
by the normalized merchant token (`_merchant_key` already exists), mapping to
`(row-pattern, target-label, provenance)`. This kills the Smithfield/locksmith
false-match and makes adding a merchant a data change, not a code edit.

Also confirm with the team that `label_evaluator_step_functions` is the intended
post-LayoutLM harmonization step — the spec named two infra dirs that don't
exist, and the PR substituted this one.

The five P3s on #1149 are fix-forward.

## Definition of done

- #1154/#1146/#1152 on `main` (human prereq).
- #1140, #1150, #1151 merged, #1140's orphan-idempotency fixed.
- #1149 green in the **built image**, PIL crash gone, merchant templates
  declarative, merged.
- Never-destroy invariant still holds (no `delete_receipt_word_label`).
