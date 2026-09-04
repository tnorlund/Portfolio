# receipt_upload/ (OCR post-processing, line-item decode)

Deltas to the root `AGENTS.md`. The decode path is
`OCR → LayoutLM furniture labels → sections → extract_items / decode_band_blocks → persist`,
and cloud recompute on summary is canonical.

- Line-item hard constraints (these have each been violated before):
  no LayoutLM product labels into the section finder; no merchant `if`s or
  merchant regexes in `line_items/geometry.py`; never mint a price for a
  missing amount column (two names + one amount is one item); never lower a
  golden floor in `tests/fixtures/line_items_golden.json`; discounts use the
  existing `is_discount` + negative `price`, no new schema.
- Python↔Swift parity: any change under `line_items/`, `section_assignment.py`,
  or `section_verifier.py` needs the matching change in
  `receipt_ocr_swift/Sources/ReceiptOCRCore/{LineItems,Sections}/` and must
  keep `tests/test_swift_line_item_parity_fixture.py` and
  `tests/test_line_item_golden_regression.py` green.
- One decode mechanism per PR, smallest safe change, with fixture-receipt
  tests. Stacked geometry PRs follow the `sprouts-line-item-stack` skill,
  including the "do not chase 187/187" rule.
- Tests: `pytest receipt_upload/tests` from the repo root. The package imports
  `receipt_dynamo`, `receipt_agent`, and `receipt_places`; CI installs the full
  local stack first, so do the same before blaming a failure on your change.
- mypy is soft here (`ignore_errors = true`); do not rely on it to catch type
  regressions, write the test instead.
