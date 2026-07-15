# Upload determinism evaluation

Date: 2026-07-14

Model source: `upload-determinism-v1`

Environment: dev only

## Decision

The resolver is safe to continue behind review gating, but it is not ready to
auto-validate uploads.  The strongest result is source/provenance safety: the
20-receipt audit found no silent LayoutLM override, and every proposed D3
change was preserved as a reviewable event.  The principal blocker is D2
generalization.  Cross-fitted line accuracy is 62.35%, which causes excessive
D3 conflicts and leaves every D4 artifact in `NEEDS_REVIEW`.

## Snapshot and safeguards

The evaluator read the DynamoDB table named by `DYNAMODB_TABLE_NAME` only after
requiring `DEPLOYMENT_ENVIRONMENT=dev` and verifying the table's
`Environment=dev` and `Pulumi_Stack=dev` tags.  It used the pure, non-persisting
D2, D3, and D4 callables.  It did not call a write API and did not access prod.

The captured snapshot contained:

- 5,593 `ReceiptSection` entities: 5,126 `VALID`, 467 `PENDING`.
- 842 processed receipts with sections; 841 had unique `VALID` gold usable for
  scoring.
- Section snapshot SHA-256:
  `9a69c1c31141d97fc03a448fddb3af4c9fcebb7597fca53c3a7cecc577f313e7`.

The plan's earlier 5,712-section count has therefore drifted.  This report uses
the captured, hashed dev snapshot rather than silently mixing snapshots.
Receipt images, OCR, IDs, and field values stayed in `/tmp`; only aggregate,
redacted findings are recorded here.

## Method

D2 was evaluated with deterministic, merchant-stratified five-fold cross
validation, grouped by source image so no source receipt appeared in both
training and validation.  Merchant and global priors were relearned in each
training fold.  The committed full-corpus prior is reported separately as
in-sample apparent fit.

D3 and D4 received the cross-fitted D2 sections.  Corpus-wide D3/D4 figures are
section-consistency measurements, not field-value truth: `ReceiptSection`
validates section membership but is not independent gold for word labels or
structured values.

The visual audit was preregistered before resolver output was viewed: five
merchant sentinels (Sprouts, Costco, Vons, Smith's, and Italia Deli), plus five
receipts each from common, mid-frequency, and long-tail merchant strata.  The
sampling seed was `20260714`; all five sentinels were found.  The non-sentinel
population sizes were 315 common, 249 mid-frequency, and 273 long-tail
receipts.  Results from these 20 receipts are an audit sample, not a population
estimate.

## D2: row-to-section assignment

| Metric | Cross-fitted result |
| --- | ---: |
| Eligible gold lines | 29,809 |
| Prediction coverage | 100.00% |
| Line micro accuracy | 62.35% |
| Receipt-cluster bootstrap 95% CI | 60.78%-64.05% |
| Receipt macro accuracy | 60.23% |
| Row accuracy | 62.54% |
| Macro F1 | 51.46% |
| Boundary F1 | 39.16% |
| Eligible-gold exact-receipt rate | 1.66% |
| Confidence Brier score | 0.3024 |
| 10-bin ECE | 0.3228 |

The frozen committed model reaches 66.00% line accuracy, 65.61% row accuracy,
and 55.22% macro F1 on its training corpus.  That gap is expected optimism from
in-sample scoring and is not the deployment estimate.

## D3: label reconciliation

Against held-out `VALID` section footprints:

| Metric | Result |
| --- | ---: |
| Footprint violations | 978 |
| Violation action recall | 58.69% |
| Footprint-consistent labels | 9,423 |
| False-action rate | 34.67% |
| Non-conflict correction section precision | 94.44% |

The resolver emitted 5,166 `label-section-footprint` conflicts.  Other major
review sources were arithmetic role cardinality (742), subtotal/tax/total
reconciliation (195), line totals to subtotal (152), and direct-total checks
(108).  Corpus-wide template rules added 26 Vons member-savings labels and 12
Costco member-number labels.

In the 20-receipt visual audit, D3 emitted 94 events: 72 section-footprint, 19
arithmetic-cardinality, and three line-total/subtotal events.  All 94 were
conflicts, all retained an event ID, rule, reason, provenance, and original
proposer, and all routed to review; there were zero silent overrides.  The
sentinel audit also exposed a coverage gap: the visible Costco member number,
Vons savings evidence, and Smith's fuel-points evidence produced no template
`ADD` event in their three sentinel artifacts (0/3 expected activations).
Several visually valid total or tender labels were nevertheless flagged after
D2 put their rows in the wrong section.  This is safe but substantially
over-reviews.

## D4: structured ReceiptDetails

Across all 842 receipts, every artifact was `NEEDS_REVIEW`.  D4 emitted 6,050
LayoutLM-provenance fields and six arithmetic-provenance fields.  Section
conformance was high once a field was emitted: 97.71% over 3,492 assessable
fields and 97.44% over 1,799 assessable item fields.  This measures placement,
not value accuracy.

The 20-receipt visual audit used exact normalized values for scalar fields and
required the complete visible item-name/line-total set for the item result.
Unreadable or genuinely absent slots were excluded rather than counted as true
negatives.

| Field | Exact | Visually legible support | Exact recall |
| --- | ---: | ---: | ---: |
| Merchant | 16 | 20 | 80.0% |
| Date | 12 | 19 | 63.2% |
| Time | 8 | 19 | 42.1% |
| Complete item set | 6 | 18 | 33.3% |
| Subtotal | 3 | 14 | 21.4% |
| Tax | 6 | 13 | 46.2% |
| Total | 3 | 19 | 15.8% |
| Tender method | 1 | 17 | 5.9% |
| **Micro** | **55** | **139** | **39.6%** |

The dominant error mode was omission, not fabrication.  For example, the
Shake Shack receipt had correct merchant, date, time, two items, subtotal, and
tax, but omitted the visibly printed total and card tender.  Floor & Decor
captured date, time, tax, and total but produced an extra/incomplete item.
Hardwoods captured only the address despite a legible date, time, payment
amount, and tender.  The upside-down Vons sentinel and damaged Marufuku receipt
show why review gating remains necessary.

Corpus-wide D4 review pressure was also high: 1,732 role-cardinality conflicts,
1,193 required-field conflicts, 1,132 incomplete-item conflicts, and 115
unverified-merchant conflicts.  These conflicts are exposed in the artifact;
none is silently resolved.

## Reproduction

Run the unit checks without AWS access:

```bash
PYTHONPATH=receipt_upload:receipt_dynamo:receipt_chroma:receipt_places:\
tools/glyph-studio/py \
  python -m pytest -q \
    receipt_upload/tests/test_upload_determinism_evaluation.py
```

Run the read-only corpus evaluation with an explicitly supplied dev table:

```bash
DEPLOYMENT_ENVIRONMENT=dev \
  DYNAMODB_TABLE_NAME="$DEV_RECEIPTS_TABLE" \
  PYTHONPATH=receipt_upload:receipt_dynamo:receipt_chroma:receipt_places:\
tools/glyph-studio/py \
  python scripts/evaluate_upload_determinism.py \
    --output /tmp/upload-determinism-evaluation.json
```

Render the private, local audit sheets from that result:

```bash
DEPLOYMENT_ENVIRONMENT=dev \
  DYNAMODB_TABLE_NAME="$DEV_RECEIPTS_TABLE" \
  UPLOAD_DETERMINISM_AUDIT_S3_BUCKETS="$DEV_RECEIPT_BUCKETS" \
  PYTHONPATH=receipt_upload:receipt_dynamo:receipt_chroma:receipt_places:\
tools/glyph-studio/py \
  python scripts/render_upload_determinism_audit.py \
    --report /tmp/upload-determinism-evaluation.json \
    --output-dir /tmp/upload-determinism-audit
```

Do not commit either `/tmp` artifact: both contain receipt evidence.

## Follow-up gates

Before enabling automatic validation:

1. Improve cross-fitted D2 boundary F1 and calibrate confidence; the current
   score makes section-based D3 rules too noisy.
2. Re-run the three merchant-template sentinels until the expected template
   actions are present and provenance-complete.
3. Raise D4 recall for totals and tender methods while preserving the current
   conflict and provenance behavior.
4. Repeat the frozen snapshot and the preregistered audit after each change;
   do not treat the in-sample D2 score as a release gate.
