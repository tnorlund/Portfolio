# Section geometry and embedding experiment

## Result

A receipt-grouped holdout shows that Chroma row embeddings materially improve
the semi-Markov section decoder. The useful signal comes from cross-receipt KNN
votes plus a smaller centroid-projection term. The additional geometry and
arithmetic emission model did not add validation lift and was assigned weight
zero.

| Metric | Fair baseline | Chroma hybrid | Delta |
|---|---:|---:|---:|
| Row agreement | 3,622/4,214 (85.95%) | 3,828/4,214 (90.84%) | +4.89 pp |
| Macro section recall | 83.45% | 88.17% | +4.72 pp |
| ITEMS recall | 83.94% | 89.53% | +5.60 pp |
| PAYMENT recall | 85.16% | 92.12% | +6.96 pp |
| SUMMARY recall | 83.25% | 90.05% | +6.81 pp |
| TRANSACTION_INFO recall | 83.14% | 87.79% | +4.65 pp |

The paired test has 236 rows fixed by the hybrid versus 30 regressed, for a
net 206-row improvement. The exact two-sided McNemar p-value is
`7.42e-41`. A 5,000-sample receipt bootstrap puts the row-agreement delta at
`[+3.87, +5.94]` percentage points (95% interval).

## Design

The experiment reads the repository's local analytics cache and performs no
AWS writes. It uses 783 receipts with QA-VALID section evidence and splits by
receipt, never row, into 478 train, 138 validation, and 167 test receipts.
The test partition is untouched during weight selection. After selection, the
baseline and evidence models are refit on train plus validation and evaluated
once on test.

The comparison is fair to the existing decoder: both arms learn the same
lexical, layout, duration, and transition priors from the same split. The
hybrid adds section-aligned evidence before the existing semi-Markov decode:

1. **Geometry and arithmetic likelihood** over normalized row position,
   extents, height, vertical gaps, price-column offset, amount density,
   normalized amount magnitude, sign, and running-sum reconciliation.
2. **Embedding projection** onto L2-normalized section centroids.
3. **Embedding KNN** using non-negative cosine-weighted votes from 15 labeled
   rows in other receipts.

Validation selected weights `geometry_math=0.0`,
`embedding_projection=0.5`, and `embedding_knn=1.0`. KNN alone reached 90.65%
test agreement; the centroid term supplied a small further lift to 90.84%.
Fine-grained geometry/arithmetic did not improve on the decoder's existing
layout evidence, so it should not be integrated on the strength of this pass.

Embedding coverage was 28,172/28,191 corpus rows and 6,152/6,160 test rows
(99.87%). Missing vectors receive no embedding evidence and retain the normal
decoder behavior.

## Snapshot issue found

The documented cache validator reported the freshly downloaded Chroma
components as valid because it only ran SQLite `quick_check`. Chroma 1.3.6
could not execute `count()` on either native snapshot:

```text
Error sending backfill request to compactor: Failed to pull logs from the log store
```

For the line snapshot, the HNSW vector checkpoint trailed the metadata
checkpoint by 218 records. Every skipped record was a contiguous operation-1
UPDATE with a NULL vector, so none could affect the vector segment. The
experiment clones the snapshot locally, proves those predicates in one SQLite
transaction, advances only the copied vector checkpoint, and then requires a
successful Chroma `count()` (34,045 vectors). It refuses any vector write,
delete/non-UPDATE operation, gap, or concurrent checkpoint change.

This guarded copy is an experiment workaround, not the deployment fix. The
snapshot producer should flush/advance metadata-only vector checkpoints before
publishing, and local cache validation should execute a real Chroma operation
rather than equate SQLite integrity with collection readability.

## Reproduction

```bash
python3.13 scripts/local_analytics_cache.py sync \
  --env dev --components dynamodb,chroma

PYTHONPATH=receipt_dynamo:receipt_chroma:receipt_upload \
python3.13 scripts/evaluate_section_geometry.py \
  --output .cache/section-geometry/report.json
```

The measured run used DynamoDB mirror time
`2026-07-29T20:41:05.751096+00:00`, line snapshot
`20260729_203457_486164_849816c8`, and word snapshot
`20260729_203556_291391_09b2715f`.

## Recommended integration

Feed cross-receipt VALID-neighbor log votes into the deterministic decoder's
per-section emission evidence, using the row embeddings already produced by
the upload pipeline. Preserve the current sequence/duration decoder and its
additive PENDING-only persistence contract. Keep the existing asynchronous
verifier as independent provenance rather than using it as the sole consumer
of embeddings.

Before changing runtime assignment, repeat the shadow evaluation on receipts
created after this snapshot and add abstention/calibration gates for missing or
low-consensus neighbors. Do not add the experimental geometry/arithmetic
features unless a boundary-specific model demonstrates out-of-time lift.
