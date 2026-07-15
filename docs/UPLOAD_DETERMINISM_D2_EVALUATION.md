# D2 section assignment: held-out dev evaluation

## Result

The schema-v2 section model passes the work-order gate on the same 22 recent
dev receipts used to diagnose schema v1:

| Metric | Schema v1 | Schema v2 holdout | Required |
|---|---:|---:|---:|
| Overall row agreement | 252/429 (0.587) | 364/429 (0.848) | >= 0.80 |
| ITEMS row recall | 5/58 (0.086) | 47/58 (0.810) | >= 0.70 |

Schema v2 also reached 0.88 SUMMARY recall, 0.87 PAYMENT recall, 0.96 ADDRESS
recall, and 0.84 FOOTER recall. The cohort contained 429 rows with
non-conflicting QA-VALID section evidence; all 429 were scored and none were
unassigned.

## Holdout integrity

The 22 `(image_id, receipt_id)` keys were excluded before model training. The
committed artifact records only their canonical manifest hash, not the keys:

`16fbda1597fe69060a65154b8ddc9205c4a4cd739085409dc31286431263c741`

The model was trained from 820 other dev receipts. No inference weight or
threshold was selected from the 22-receipt result, and the model was frozen
after its first complete holdout run. The acceptance numbers above are gates,
not inputs to the decoder.

## Measured rework

Schema v1 failed for two concrete reasons:

1. Recent persisted rows predated D1's amount-pair metadata, so every
   `has_amount` feature was false even when OCR text contained an amount.
2. Row-level self transitions and globally monotone state order rewarded
   ADDRESS, PAYMENT, and FOOTER mega-sections while forbidding observed
   repeated/non-monotone section layouts.

Schema v2 therefore:

- derives amount evidence from canonicalized row text when D1 metadata is
  absent;
- models binary evidence as Bernoulli variables rather than Gaussians;
- adds receipt-local amount density and quantity/unit-price evidence;
- sums factor log likelihoods instead of averaging them against full-strength
  transitions;
- uses global lexical log-odds when a merchant prior lacks token evidence;
- learns transitions between distinct QA section runs and a separate log-run
  duration distribution; and
- uses a semi-Markov decoder that permits measured repeated/non-monotone runs
  and scores the terminal transition.

The builder rejects every table except the exact dev table, supports an
exclusion manifest, fails if any exclusion lacks QA-VALID evidence, paginates
the section corpus, and omits ambiguous QA row labels. The evaluator is
read-only, has the same unconditional dev-table guard, and counts missing
predictions as mismatches rather than removing them from the denominator.

## Reproduction

```bash
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 \
DEPLOYMENT_ENVIRONMENT=dev \
PYTHONPATH=receipt_upload:receipt_dynamo:receipt_chroma \
python3.12 scripts/build_section_order_priors.py \
  --environment dev \
  --exclude-manifest /private/tmp/d2_targets.json

DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 \
PYTHONPATH=receipt_upload:receipt_dynamo \
python3.12 scripts/evaluate_section_assignment.py \
  --targets /private/tmp/d2_targets.json
```

Neither command performs a DynamoDB write. The manifest remains local because
it contains full image identifiers; regression fixtures commit only normalized
features, generic lexical evidence, eight-character case prefixes, and QA
section types.
