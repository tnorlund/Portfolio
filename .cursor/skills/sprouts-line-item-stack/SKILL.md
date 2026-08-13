---
name: sprouts-line-item-stack
description: >-
  Stacked line-item decode PRs toward Sprouts match, then remaining-error
  EDA and other-merchant uplift. Use when stacking receipt decode fixes,
  running Sprouts A/B evals, investigating remaining mismatches/nears,
  or measuring lift on other merchants after a geometry/summary layer.
---

# Sprouts line-item stack

Continue the **constraint → pairing → fragment join → qty-echo/ITEMS-tail → zone-gap** stack. Do not chase 187/187: bottle-return refunds stay no-baseline.

## Pipeline (do not fork)

`Image → Mac Vision OCR → LayoutLM furniture (not product labels) → sections → extract_items / decode_band_blocks → persist → stream recompute on summary.`

Cloud recompute is canonical. Mac first-pass without a summary is a no-op for constraint.

## Hard constraints

- No LayoutLM **product** labels into the section finder.
- No merchant `if`s / Sprouts regexes in `geometry.py`.
- No new Dynamo discount schema (`is_discount` + negative `price` already exist).
- Never mint a missing column price (two names + one amount stay one item).
- Do not lower golden floors.
- Do not run `pulumi`. Never write prod `ReceiptsTable-d7ff76a`. Dev evals: `ReceiptsTable-dc5be22` read-only.
- Never commit to `main`. Never force-push.

## Stacking

Geometry/Swift decode layers stack on **pairing** (`feat/two-column-pairing`, #1416), not on TOTAL_LINE (#1415). #1415 is `receipt_dynamo` / summary and stays a sibling.

1. Create a worktree from the previous layer tip.
2. One mechanism per PR. Smallest safe change. Tests for the fixture receipts.
3. `gh pr create --base <previous-layer-branch>`. Then `gh stack link` onto the geometry stack.
4. PYTHONPATH must point at the **worktree** packages; the venv editable install is `/Users/tnorlund/Portfolio`.

```bash
PYTHONPATH="$WT/receipt_upload:$WT/receipt_dynamo" \
  /Users/tnorlund/Portfolio/.venv/bin/pytest \
  receipt_upload/tests/test_baseline_constraint.py \
  receipt_upload/tests/test_two_column_pairing.py \
  receipt_upload/tests/test_line_item_golden_regression.py
```

Format only files you touch.

## Eval

Reuse `/tmp/sprouts_stack_eval.py` (pairing `receipt_upload` + baseline-recovery `receipt_dynamo` for TOTAL_LINE). Unset `HTTP_PROXY`. After each layer, report Sprouts match/near/mismatch/no-baseline vs 168/187 (89.8%) full stack.

Then sweep **other merchants** the same way (GSI1 by merchant, same decode path). Report match-rate delta vs main-style (no constrain/split) and vs current stack. A layer that regresses another merchant does not ship.

## Remaining error loop

After a layer lands, spawn EDA subagents on remaining Sprouts misses (mismatch / near / no-baseline). Cluster mechanisms. Next PR is the smallest generic decode/summary fix, not a merchant special case. Leave as near: CRV-vs-tax pennies, missing OCR amounts, refunds.

Known unrecoverable without new OCR: cropped `2c9b770c` (−$9.99), `dbc78ee2` RAW CREAM (no amount token), digit/glyph misreads (`7.99`→`1.99`) — those are re-OCR on the **reconcile baseline**, not `subtotal`-only.

## Do not

Ungated `merge_price_fragments` (Costco `5,`+`90` → 5.90). Treat `1.` or `)` as a decimal. Mint footer coupons as line discounts. Feed product labels into sections.
