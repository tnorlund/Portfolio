# TRANSACTION_INFO sequence-invariant decision memo

Date: 2026-07-17  
Frozen benchmark candidate: `4190a878966784f0ccd2301669912e41d948dddc`  
Experimental branch: `codex/txinfo-sequence-invariants`

## Executive recommendation

Do not promote the frozen v4 decoder solely from a benchmark result. Its
returned assignments are not necessarily the semi-Markov path that it scored:
four semantic anchors and one local heuristic mutate individual states after
backtracking. Claude's independent benchmark remains useful performance
evidence for the exact frozen candidate, but it cannot establish the missing
sequence invariant.

The clean experimental direction is to keep the DP authoritative. Three
merchant-independent semantic invariants (`subtotal`, `fuel points earned`,
and a card brand paired with an amount) become compatibility terms inside the
DP objective. The merchant-specific `FRESH VALUE` rule and the post-decoder
`1.25` local override are removed. No new empirical weight is introduced.
This experiment passes the existing unit and golden tests, but it has not been
evaluated on any protected cohort and requires a future validation boundary.

## Chronology

| Stage | Priors SHA-256 | Training boundary | Result and consequence |
|---|---|---|---|
| Pre-TXINFO schema-v2 baseline | `9e6e828a...` | 820 trained receipts; original 22 excluded; no `TRANSACTION_INFO` state | The later blind taxonomy study measured 0.761 overall agreement and 0.846 on emittable rows; 45/518 rows formed the transaction-metadata gap. This motivated the enum and VALID-label cleanup, not a decoder threshold. |
| TXINFO label correction | n/a | 499 receipts corrected; the final dev corpus contained 643 VALID TXINFO sections, 1,567 visual rows, and 2,651 lines | Regex candidates were independently adjudicated and spatially reconciled. These labels were manually established, then consumed as ordinary VALID training data. |
| v1 priors | `7c09fc6b...` | 812 trained; fresh-1's 30 receipts excluded, but the original spent 22 were accidentally allowed back into training | Fresh-1: 0.858 overall, 0.789 ITEMS recall, 0.762 TXINFO precision, 0.821 TXINFO recall. Metric gates passed, but known Smith's/golden outputs regressed. The leaked old cohort and golden failures rejected v1. |
| v2 priors | `50b82923...` | 760 trained; original 22 + fresh-1 30 + fresh-2 30 excluded (82 total) | Known goldens exposed sequence smoothing over strong local evidence. Four semantic anchors and the `1.25` local override were manually added after decoding. |
| v3 provenance rebuild | `50b82923...` | Same 760 and same 82 exclusions | Rebuilding with canonical upload rows produced byte-identical priors, proving persisted-vs-reconstructed rows were not the cause. Fresh-2 then measured 0.844 overall, 0.853 ITEMS recall, 0.575 TXINFO precision, and 0.821 TXINFO recall. The precision gate failed. |
| v4 frozen candidate | `a10752fd...` | 710 trained; all 132 spent/reserved receipts excluded | Frozen at `4190a8789` with the manual post-decoder rules intact. Claude owns the independent benchmark. This architectural branch does not run or interpret the protected calibration cohort. |

## Spent and excluded receipts

The v4 training-exclusion manifest contains 132 unique receipts:

| Cohort | Receipts | QA rows | Canonical manifest SHA-256 | Status |
|---|---:|---:|---|---|
| Original blind cohort | 20 | 518 | Part of `16fbda15...` | Spent by the taxonomy/blind study |
| Pinned real goldens | 2 | fixture rows | Part of `16fbda15...` | Spent; committed only as privacy-safe features |
| Fresh holdout 1 | 30 | 593 | `daaf6de9...` | Spent by v1 |
| Fresh holdout 2 | 30 | 609 | `d98cf063...` | Spent by v2/v3 |
| Fresh holdout 3 | 30 | 553 | `61191319...` | Reserved/spent; never eligible for training |
| `precision_calibration_v1` | 20 | 406 | `7f907afe...` | Spent; Claude benchmark boundary |

The exact v4 exclusion hash is
`4c06cd83a56f51c0142803c488628a930013bb65b16c0ebf099e813741ab7854`.
None of these receipts may return to training or be used to choose another
rule, threshold, or weight.

## Learned evidence versus hardcoded behavior

Learned from QA-VALID rows:

- global and eligible-merchant priors;
- Gaussian position, span, alpha-ratio, and amount-density distributions;
- Bernoulli amount and quantity features;
- lexical token log-odds;
- run-duration distributions;
- start, inter-section, and terminal transition probabilities; and
- the full eleven-state ordering, including `TRANSACTION_INFO`.

Manually supplied in frozen v4:

- `subtotal -> SUMMARY`;
- `fuel + points + earned -> FOOTER`;
- `VISA`/`Mastercard`/`Amex` with an amount -> `PAYMENT`;
- the global, merchant-specific phrase rule `FRESH VALUE -> STOREFRONT` near
  the top of a receipt;
- a `1.25` local-emission margin that changes early `PAYMENT` rows to `ITEMS`;
- a hardcoded list of payment-action tokens that blocks that local override;
  and
- support for a model-wide emission bias (v4 carries no selected nonzero
  bias).

The experimental correction retains the first three generic compatibility
rules, removes the phrase hack and local threshold, and places compatibility
inside the decoder objective as a `0 / -infinity` feasibility term. It is
still manual semantic evidence, not a learned parameter.

## Frozen v4 invariant audit

| Question | Finding |
|---|---|
| Are returned assignments one coherent semi-Markov path? | **No.** The DP backtracks a path, then mutates `states` row by row. The returned state vector need not correspond to any stored backpointer chain. |
| Can mutation create isolated rows? | **Yes.** A middle anchor or local winner can turn `A,A,A` into `A,B,A` without reconsidering either neighbor. A synthetic test reproduces both anchor- and local-override cases. |
| Can it create unscored transitions or durations? | **Yes.** The new `A->B`, `B->A`, and singleton `B` duration are never evaluated. The learned artifact Laplace-smooths transitions, so they are not literally zero-probability, but they may be extremely unlikely and remain absent from the chosen DP score. |
| Can confidence disagree with the overridden label? | The field is computed for the returned label, but only as a row-emission softmax. An anchor can force a label whose confidence is below 0.001; a local override can report high local confidence even though path evidence rejected it. It is not a path posterior. |
| Can section confidence mislead? | **Yes.** It is the arithmetic mean of row-emission confidences. `sections_from_assignments` combines all rows of a type, including disjoint runs, so the mean neither represents run coherence nor a section posterior. This limitation also exists for legitimate repeated semi-Markov runs. |

## Option comparison

| Option | Strengths | Risks / evidence |
|---|---|---|
| Frozen v4 as-is | Exact candidate under Claude benchmark; preserves frozen outputs | Violates the authoritative-path invariant; includes a merchant phrase hack and a tuned local threshold. Do not promote on benchmark metrics alone. |
| Revert all post-decoder rules | Restores one coherent path with no new code | On unit/golden-only replay, both real goldens remain stable, but the Smith's `FUEL POINTS EARNED` row regresses from `FOOTER` to `ITEMS`. |
| Pre-decoder semantic compatibility (experiment) | DP remains authoritative; transitions/durations are re-optimized; no new weight; deterministic; works without merchant identity or LayoutLM | The three compatibility rules remain manual and need future untouched validation. Constrained rows can have low local-emission confidence, which should be documented rather than treated as a posterior. |
| Later LayoutLM row probabilities | Can add learned semantic/layout evidence per section before decoding; missing probabilities can contribute zero evidence, preserving today's fallback | Requires a separately trained/calibrated model and provenance. Do not invent a mixing coefficient; learn it from training/calibration data before freezing a new candidate. |
| Chroma independent verification | Useful as a disagreement detector, audit sampler, or abstention/review trigger | Do not force Chroma labels into the DP or make it a hidden source of truth. Keep verification independent and measure agreement/error modes separately. |

## Experimental correction

The experiment makes semantic compatibility part of segment feasibility:

1. Determine an optional generic constraint for each row before decoding.
2. For each candidate state, prefix-count rows that conflict with that state.
3. Reject any segment spanning a conflicting constraint.
4. Run the unchanged transition, duration, emission, terminal, and deterministic
   tie-breaking logic over the remaining segments.
5. Backtrack once and return that path without state mutation.

Unreachable DP cells are represented explicitly as negative infinity and are
never used as predecessor states. If no complete constrained path exists, the
decoder fails explicitly rather than returning a fabricated path.

## Next legitimate validation boundary

Wait for Claude's independent report before making any recommendation about
the frozen v4 benchmark. Regardless of that result, this experimental decoder
needs a new boundary because it is a different implementation.

Preferred boundary: future shadow-mode receipts collected after the code and
model commit are frozen. A merchant-disjoint untouched cohort is acceptable
only if every merchant is absent from training, all 132 spent receipts, model
development, and golden construction.

Pre-register once:

1. exact decoder commit and unchanged priors SHA;
2. cohort construction and merchant-disjoint proof;
3. row/section label protocol performed after prediction freeze;
4. overall agreement, ITEMS recall, TXINFO precision/recall, deterministic
   replay, and sequence-coherence gates;
5. confidence diagnostics clearly labeled as local-emission measures; and
6. one evaluation with no code, rule, fixture, or weight adaptation after the
   report is viewed.

If the one evaluation fails, report the failure. Do not tune and retest on the
same cohort.
