# Transaction-info shadow evidence decision

## Decision

Do not promote either evaluated candidate from the available shadow evidence.

The frozen v4 candidate is ineligible because it can change selected section
states after decoding, so the reported assignments are not necessarily one
authoritative scored sequence path. The corrected candidate removes that
architectural defect, but it did not improve any measured performance metric.
The only observed prediction differences produced a marginal sequence-coherence
regression. This branch therefore publishes the reusable evaluation harness and
sanitized evidence only; it does not approve a model merge or deployment.

## Frozen inputs

| Input | Identifier |
|---|---|
| Frozen v4 candidate | `4190a878966784f0ccd2301669912e41d948dddc` |
| Sequence-invariant correction | `099d1197aea778f32f547cac2fc42ad5f33c600e` |
| Harness source commit 1 | `61ec20174edb1579c590d848a52108e70c0a3fbe` |
| Harness source commit 2 | `6c39bc0e83b7af45b37d61e88f6e53ffcbffd620` |
| Shared priors SHA-256, identical in both candidates | `a10752fd037d9d1951b5195f06253e50b7116a3c39c7001b128802023ef613c4` |

The private evaluation inputs and row-level outputs are intentionally excluded
from this repository. For audit continuity, the two locally retained aggregate
source reports were hashed without copying their contents:

| Aggregate source | SHA-256 |
|---|---|
| `fresh_upload_v1` aggregate report | `d241e307605127f40ac67363fac16e9bef2dca4110c89e7125aed117de433626` |
| `recent20_retrospective` aggregate report | `762cce202b9e5920f53cacbded8496661428e9bad5c81af7bedbc713518b0959` |

## Fresh shadow evidence

The fresh evaluation contained 2 receipts and 70 scored rows. Only 6 scored
rows belonged to the transaction-information class, so its transaction metrics
are smoke evidence rather than promotion-grade estimates. The candidates made
identical predictions on all scored rows.

| Metric | Frozen v4 | Correction | Result |
|---|---:|---:|---|
| Overall agreement | 51/70 = 0.729 | 51/70 = 0.729 | Both below the 0.80 gate |
| ITEMS recall | 10/10 = 1.000 | 10/10 = 1.000 | Both above the 0.70 gate |
| TXINFO recall | 2/6 = 0.333 | 2/6 = 0.333 | Both below the 0.70 gate |
| TXINFO precision | 2/3 = 0.667 | 2/3 = 0.667 | Both below the 0.70 gate |
| Unassigned rows | 0 | 0 | No difference |

Supplemental Wilson 95% intervals were `[0.615, 0.819]` for overall
agreement, `[0.723, 1.000]` for ITEMS recall, `[0.097, 0.700]` for TXINFO
recall, and `[0.208, 0.939]` for TXINFO precision. Both candidates produced 27
section runs and 9 single-row runs across the cohort.

This was the first post-freeze cohort, but its two-receipt size, six positive
TXINFO rows, single upload batch, and narrow capture conditions make it
insufficient for promotion. Three of the four preregistered numerical gates
failed for both candidates.

## Retrospective development evidence

The retrospective run covered 23 receipts from 20 recent development uploads.
It produced 753 row predictions, of which 298 rows had scorable development
annotations. The cohort overlaps model-development and already-spent evidence,
so none of these results are independent confirmation.

| Metric | Frozen v4 | Correction |
|---|---:|---:|
| Overall agreement | 0.8154 | 0.8154 |
| ITEMS recall | 0.7705 | 0.7705 |
| TXINFO recall | 18/20 = 0.900 | 18/20 = 0.900 |
| TXINFO precision | 18/25 = 0.720 | 18/25 = 0.720 |
| Unassigned rows | 0 | 0 |

Agreement was 0.9086 on the likely-training stratum and 0.6829 on the spent
development-cohort stratum. That gap is consistent with development overlap
and selection effects; neither number estimates future production performance.

The corrected candidate differed from frozen v4 on only 2 of 753 predicted
rows. Those changes produced no improvement in any reported metric. Aggregate
section runs increased from 342 to 343, and single-row runs increased from 163
to 164. This is a marginal coherence regression where the correction diverged.

## Limitations and next boundary

- The fresh evidence is underpowered, especially for TXINFO precision and
  recall.
- The retrospective evidence uses development annotations and contaminated
  cohorts; it cannot serve as a promotion gate.
- The fresh candidates behaved identically, so that cohort did not exercise
  the corrected code path.
- The two retrospective divergences are too few to estimate a stable effect,
  but they provide no positive evidence for the correction.
- Aggregate point estimates do not establish merchant-disjoint or production
  generalization.

A future decision requires one preregistered, untouched shadow evaluation with
enough positive TXINFO rows and meaningful merchant-disjoint coverage. Neither
candidate should be tuned or rerun in response to the evidence summarized
here. Until that boundary is met, frozen v4 remains architecturally ineligible
and the correction remains unproven.

## Privacy boundary

This memo contains aggregates, code commit identifiers, and cryptographic
digests only. It intentionally excludes source images, merchant or receipt
identifiers, OCR text, row-level labels, cohort membership records, row-level
predictions, and private evaluation inputs.
