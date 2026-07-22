# Nightly Report 2026-07-21

## Verdict
All good tonight.

## Budget
- wall clock: 6120s
- turns: 41 of 80
- subagents: 2 of 3

## Fleet
| slug | version | gate | staleness |
|------|---------|------|-----------|
| costco | 1 | PASS | 0d |
Delta vs yesterday: +13 ACTIVE (first post-mint night).

## Tonight's Work
- costco: eval GREEN (7/7 metrics PASS), sheet at ~/.nightly-loop/2026-07-21/eval/costco.sheet.png
- vons: eval RED (columns FAIL, h=1.6 defect), finding recorded
- sprouts: SKIPPED - golden receipt lookup exceeded 30min budget

## Awaiting Owner
None.

## Failures & Anomalies
- receipt-tools MCP unauthenticated; ran DEGRADED via direct AWS reads.

## Tomorrow's Top 3
1. vons - columns metric RED (h=1.6)
2. sprouts - retry with cached golden receipt id
3. smiths - next in rotation
