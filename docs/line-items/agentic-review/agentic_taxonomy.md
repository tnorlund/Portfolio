# What actually needs a human eye

Counts recomputed today from `ReceiptsTable-dc5be22`: **654 receipts** with line items — match 467, near 40, **mismatch 102**, no-baseline 45. Bank joined on 364; bank agrees on 347; **PROVEN (match+bank) = 278**. Split-receipt duplicates: 39 across 19 groups. Recent ingest ≈ **35 receipts/month**.

Surplus modes C/D/F and most of A are **gone** (24+13+2+1 in the old report → 2+0+0+0 now): the merged band filter closed them in code. The residue is shortfall.

## Per mode
| mode | dev count | adjudicable by | residual human need |
|---|---:|---|---|
| A-total-line-absorbed | 2 | arithmetic (drop band = printed figure, ≥2 items) | **none** — closes to the cent |
| B-baseline-ocr-broken | 10 | vision (read the printed figure off the image) + bank | **none** — probe below: 2/2 resolved in one look |
| C-tender / D-promo / F-mixed | 0 | band filter (shipped) | **none** — mode extinct |
| G-phantom-item | 11 | arithmetic (delta == one item price) + vision to confirm the row isn't a product | **none** |
| H-clean-extension | 3 | arithmetic, strict-improvement guard | **none** |
| H-ambiguous-overshoot | 23 | **vision** — arithmetic can't pick which outside row is a product; the image says outright | **none**; probe below: missing row was item #1 under a section header, `Regular Price $44.99` was the decoy |
| H-insufficient-outside | 7 | vision (rows exist on paper, OCR partially dropped them) → re-OCR | **none** |
| H-no-ocr-text | 19 | vision first (is text legible?); if yes → re-OCR; if no → reshoot | **audit only**; reshoot needs the *paper*, not a judgment |
| H-split-receipt | 12 (of 39) | cross-receipt: same merchant+date+total, disjoint items | **none per receipt**; one policy call: merge vs retire-inferior |
| J-unknown | 15 | vision, by construction — "no arithmetic story" is exactly where the image is the only evidence | **audit-sample** until a vision pass shows what they are |
| no-baseline | 45 | vision + bank (the figure exists on paper; extraction missed it) | **none** |

## Cross-cutting classes
| class | count | adjudicable by | residual human need |
|---|---:|---|---|
| OK-controls (match, bank-confirmed) | 278 | vision vs decoded list — an agent checks a control as well as a person, and more consistently | **audit-sample** (~10/batch) to keep the tolerance ladder honest |
| Golden promotion | 175 match-without-bank + 278 PROVEN | arithmetic + bank + vision select the candidates | **always, as sign-off** — gap is *authority*, not perception: a golden entry is a permanent CI floor and a bad one is expensive to un-ring. Sample, don't review all. |
| Bank vs printed conflict | 17 | 13 are **tip-shaped** (positive gap, 5–20%, food merchant) → machine rule, not a conflict. 4 non-tip → vision | **none.** This refutes review_workflow.md §b item 2 ("only a human can say which one lies") |
| Image quality / crop | 41 severe under-extraction; 5 flagged image_suspect | vision — legibility and box-drift are visual calls agents already make | **none**; reshoot is an *action* a human takes, not a call they make |

## Evidence: three live vision probes (this session)
- `75ef30e5` Sparkling Image — stored total 59.95, bank 39.99. Image reads `TOTAL: 39.99`. Bank right, stored figure wrong. **Seconds.**
- `750e0675` Costco — stored total 67.00, bank 40.37. Image reads `TOTAL 40.37`, `EFT/Debit 40.37`, items 8.99+15.89+15.49=40.37. Stored figure is corrupt; 67.00 appears nowhere on the receipt.
- `b3ff79e6` Target — delta −9.89, 3 items vs printed 4. Image: `VICKS THERM $9.89` is the first row under the `HEALTH AND BEAUTY` header, dropped by the zone start. Arithmetic alone was stuck (74.36 of outside money for a 9.89 gap); the image named the row.

## Aggregate
Machine-adjudicable today: **~95 of 102 mismatches**, all 17 bank conflicts, all 41 under-extractions. If agents adjudicate everything above, the human queue at 35 receipts/month is:

- **One-time, ~45 min**: four policy calls that are preference, not capability — is a bag fee / bottle deposit / `Regular Price` line an item; does a tip belong to the receipt total; merge-or-retire for duplicate scans; what tolerance counts as "proven".
- **Steady state, ~10–20 min/month**: sign off a ~10-receipt golden sample (the only genuine always-human item, and it's accountability not perception), plus ~1–2 receipts/month where the *photograph* is unrecoverable and the paper must be reshot.

Not 30 receipts a session. The pre-session dossier scout abstained on **31 of 31** receipts — but every abstention reason was arithmetic-only ("|delta| shrinks but status does not improve", "closes by coincidence, not because those rows are products"). Those are exactly the calls vision makes. **Give the scout the image and the human queue collapses by an order of magnitude.**
