# D0 exact-runtime typeface calibration v2

## Outcome

The v2 registry enables no source. Runtime D0 therefore records evidence and
support counts but abstains from proposing a merchant or typeface.

This is the measured result, not a product threshold choice. Every source with
an independent calibration cohort has overlapping genuine and impostor score
distributions. Sources without such a cohort are disabled separately. Choosing
any non-null acceptance threshold from these observations would fit the
acceptance result that the threshold is supposed to test.

The originally requested real Costco positive is empirically blocked. On the
frozen six-receipt Costco calibration split, every receipt lost top-1 and the
Costco genuine scores overlapped foreign receipts. The committed Costco fixture
therefore asserts safe abstention rather than a false positive.

## Measurement protocol

- Remote access is read-only and the evaluator hard-refuses every table except
  `ReceiptsTable-dc5be22`. It explicitly refuses the production table.
- Crops come from the exact upload function
  `receipt_upload.typeface_fingerprint.extract_letter_crops`.
- Glyph cleanup, normalization, and shifted IoU are canonical glyphstudio
  primitives; no copy is carried by receipt_chroma.
- Each character contributes one vote: median shifted IoU across that
  character's crops. A receipt score is the median across distinct characters.
- Receipt crop multisets are hashed and deduplicated before a score-blind,
  merchant-stratified alternating calibration/holdout split.
- A source can be enabled only if `max(calibration impostor) < min(calibration
  genuine)`. Its threshold is then the midpoint of those values. An untouched
  holdout must have positive genuine support and zero impostor crossings.
- Holdout distributions are measured and frozen for every covered source, even
  when calibration fails. They are report-only in that case: they cannot
  rescue, retune, or otherwise influence the calibration threshold.
- Six distinct characters is independent of the observed acceptance scores:
  five unanimous paired signs have two-sided exact `p=0.0625`, while six reach
  `p=0.03125` at alpha 0.05.

The frozen privacy-safe report is
`synthesis_loop/rom_font_manifests/typeface_runtime_calibration_v2.json`.
It contains hashed case identifiers and numeric scores only. The fixture file
contains every cleaned binary crop mask and its exact shape for three frozen
receipts, so replay exercises the same population statistic without a second
normalization or an exemplar approximation. It contains no receipt image,
UUID, S3 path, or raw OCR geometry. No `.npz` is used or committed.

The independent Costco cohort is pinned by 12 privacy-safe case hashes. The
evaluator fails closed if any pinned case disappears or if an unpinned receipt
would silently replace it; the crop-multiset and cohort-manifest hashes record
the measured population in the report.

When a future source passes both gates, its confidence is explicitly
source-relative: the runtime reports the empirical midrank of that receipt's
score within the selected source's genuine calibration distribution. It is not
a global merchant probability and is never used as the acceptance threshold.

## Calibration result

The gap below is `min(genuine) - max(impostor)`. A source requires a positive
gap before holdout can enable it, although the report still freezes every
available holdout score.

| Source | Genuine n | Impostor n | Genuine range | Max impostor | Gap |
|---|---:|---:|---:|---:|---:|
| `MERCHANT:costco` | 6 | 18 | 0.477610–0.618243 | 0.584057 | -0.106448 |
| `MERCHANT:cvs` | 3 | 15 | 0.354545–0.536254 | 0.558984 | -0.204438 |
| `MERCHANT:homedepot` | 2 | 16 | 0.149676–0.448348 | 0.562318 | -0.412642 |
| `MERCHANT:target` | 4 | 14 | 0.461466–0.584908 | 0.563591 | -0.102125 |
| `MERCHANT:vons` | 3 | 14 | 0.465201–0.605429 | 0.568264 | -0.103063 |
| `ROM:bitMatrix-C1-heavy` | 7 | 11 | 0.349673–0.596850 | 0.593239 | -0.243566 |
| `ROM:bitMatrix-D1` | 4 | 14 | 0.137577–0.560749 | 0.615546 | -0.477969 |
| `ROM:pixCrog` | 9 | 9 | 0.147729–0.545402 | 0.574847 | -0.427118 |

The Vons merchant-atlas cohort excludes the receipt named in that font's review
provenance. Home Depot treats D1 and pixCrog as the already-documented #1110
tie, so neither is mislabeled as a Home Depot foreign negative.

The original #1110 manifest listed 38 receipts. Two no longer have a Receipt
plus ReceiptLetters in dev, and three Whole Foods entries collapse to one exact
runtime crop multiset, leaving 34 unique examples. The independent Costco
supplement has 12 unique examples after excluding the documented font-review
and gold/render-calibration receipts.

On the three committed real-crop fixtures on an Apple Silicon development
machine, registry load and read-only atlas decode took 139 ms cold. Thirty warm
matches had 121 ms median, 124 ms p95, and 125 ms maximum latency. The OCR
container was also built and smoke-tested for its production `linux/arm64`
target; these local timings are regression evidence, not a production SLO.

## Reproduction

```bash
AWS_REGION=us-east-1 \
DYNAMODB_TABLE_NAME=ReceiptsTable-dc5be22 \
PYTHONPATH=receipt_chroma:receipt_dynamo:receipt_upload:tools/glyph-studio/py \
python3.12 scripts/evaluate_typeface_registry.py

PYTHONPATH=receipt_chroma:receipt_dynamo:receipt_upload:tools/glyph-studio/py \
python3.12 scripts/build_typeface_registry.py
```

The registry builder pins every external specimen SHA-256, applies an exact
glyph-copy gate with no provenance exception, verifies the measurement's atlas
hash, and rejects any enabled source whose distributions, midpoint threshold,
holdout, or split manifests violate the runtime schema.

The generated asset also binds the decoded atlas data with
`atlas_registry_sha256` and the complete persisted runtime configuration with
`registry_sha256`. Runtime validation rechecks both on every match, including
after the cached read-only atlas arrays have been created, so in-process JSON
mutation cannot bypass validation.

## Future enablement

A future positive attempt needs new, independently sourced hardware/typeface
truth and a preregistered stronger feature family. It must freeze reference,
calibration, and holdout cohorts before scoring and pass the same strict
calibration-gap plus untouched-holdout protocol. The current acceptance set
must not be reused to select another feature combination.
