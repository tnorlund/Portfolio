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

## Independent benchmark synthesis

Claude completed the independent audit in
`/Users/tnorlund/Portfolio_artifacts/txinfo-recall-2026-07-17/independent_benchmark_claude/`.
The audit gives an explicit **PASS on all five frozen gates**, but expressly
does not recommend promotion. The performance result applies only to frozen
commit `4190a878966784f0ccd2301669912e41d948dddc` and its post-decoder
overrides; it does not measure the experimental correction.

### Independent provenance verification

The supplied identities were checked directly after the report became final:

| Item | Verified value |
|---|---|
| Frozen candidate HEAD | `4190a878966784f0ccd2301669912e41d948dddc` |
| Frozen priors SHA-256 | `a10752fd037d9d1951b5195f06253e50b7116a3c39c7001b128802023ef613c4` |
| Holdout manifest file SHA-256 | `21049bb1a91e1fd6fd569a9776ae090cf9984acf4d6d592fe4d8558ee7125020` |
| Holdout manifest canonical SHA-256 | `7f907afe08b146a76d932206ca9540ecc5de6273f451415fac0bec62eceea02c` |
| Training exclusions file SHA-256 | `7cae46850db02cfd928063fecde278750af5aa0c682082f46282732d6cb39b17` |
| Training exclusions canonical SHA-256 | `4c06cd83a56f51c0142803c488628a930013bb65b16c0ebf099e813741ab7854` |
| Excluded receipts recorded in priors | 132 |
| Independent evaluation SHA-256 | `342b77c75d4d0c08ebd3055c437a4fab04c86f62737c97220fa9e8d288f32f9a` |

The priors embed the verified canonical exclusion hash and record 132 excluded
receipts. Claude's detached audit worktree and the original frozen worktree
both remained at `4190a8789` with empty porcelain status. `EVALUATION.json` and
`evaluator_stdout.json` are byte-identical copies of the same output. The audit
contains one evaluator artifact, an empty evaluator error stream, and Claude's
explicit statement that the evaluator exited successfully on its only run.
The report-recorded command, with its declared paths expanded, was:

```bash
cd /private/tmp/claude-501/-Users-tnorlund-vegas-26/68339a82-9f05-4b21-8a50-b62889043cba/scratchpad/txinfo_v4_audit
PYTHONDONTWRITEBYTECODE=1 /Users/tnorlund/Portfolio/.venv/bin/python \
  scripts/evaluate_section_assignment.py \
  --targets /Users/tnorlund/Portfolio_artifacts/txinfo-recall-2026-07-17/independent_benchmark_claude/inputs/HOLDOUT_MANIFEST.json \
  --table ReceiptsTable-dc5be22 \
  --output /Users/tnorlund/Portfolio_artifacts/txinfo-recall-2026-07-17/independent_benchmark_claude/EVALUATION.json
```

This confirms exactly one **Claude audit** evaluation and no candidate change.
It does not mean the cohort received only one prediction pass over its
lifetime. Before freeze, development used the same 20 receipts for a seven
trial `txinfo_bias` calibration sweep and a developer final evaluation. The
selected bias was `0.0`, so the artifact remained numerically unchanged, but
the decision to freeze it was made after seeing the cohort. The independent
run therefore confirms identity and determinism, not fresh generalization.

### Frozen v4 benchmark performance

| Gate | Preregistered threshold | Frozen v4 result | Outcome |
|---|---:|---:|---|
| Overall QA row agreement | >= 0.80 | 349/406 = **0.8596** | PASS |
| ITEMS recall | >= 0.70 | 81/88 = **0.9205** | PASS |
| TRANSACTION_INFO recall | >= 0.70 | 18/22 = **0.8182** | PASS |
| TRANSACTION_INFO precision | >= 0.70 | 18/23 = **0.7826** | PASS |
| Determinism and frozen goldens | green | 10/10 pass | PASS |

The supplemental Wilson 95% intervals were `[0.822, 0.890]` for overall
agreement, `[0.845, 0.961]` for ITEMS recall, `[0.615, 0.927]` for TXINFO
recall, and `[0.581, 0.903]` for TXINFO precision. Both TXINFO lower bounds are
below the 0.70 gates. The point estimates pass; the small TXINFO denominators
do not resolve whether population precision or recall clears 0.70.

For context, the available baseline-versus-v4 reruns are listed below. They
are **retrospective development evidence, not independent confirmation**:

| Spent cohort | Baseline agreement | v4 agreement | v4 TXINFO recall / precision |
|---|---:|---:|---:|
| Original blind 20 (240 VALID rows) | 0.8625 | 0.8000 | n/a |
| Pinned goldens 2 (48 rows) | 0.7917 | 0.8125 | n/a |
| Fresh holdout 1 (593 rows) | 0.8516 | 0.8499 | 0.821 / 0.744 |
| Fresh holdout 2 (609 rows) | 0.8571 | not stored | not stored |
| Fresh holdout 3 (553 rows) | 0.8246 | not stored | not stored |
| `precision_calibration_v1` (406 rows) | 0.8744 | 0.8596 | 0.818 / 0.783 |

The evidence suggests that v4 adds TXINFO coverage while sometimes reducing
overall agreement and ITEMS recall. Missing v4 reruns must remain missing; no
new evaluation on these spent cohorts should be created to fill the table.

### Architectural validity and correction status

Frozen v4 is **not architecturally valid as one authoritative semi-Markov
decode**. Claude independently observed three anchor flips on the calibration
cohort: section runs increased from 255 to 260, single-row islands increased
from 119 to 123, and receipts with split section types increased from 18 to
19. One anchor created an isolated PAYMENT row inside ITEMS and another split
a SURVEY type into disjoint runs. These post-decoder transitions and durations
were not scored by the DP. The pure decoder also permits repeated section
types, so authoritative decoding does not by itself guarantee one contiguous
run per type; it guarantees that every returned transition and duration came
from one scored path.

Experimental correction `2cb6f4ee26b1a17002f89c7791171546d0042fa9`
is a direct child of the frozen commit. It removes post-decoder state mutation,
keeps the DP path authoritative, removes the merchant phrase hack and tuned
local override, and expresses the remaining generic semantic rules as scored
feasibility constraints before decoding. Seventeen focused, existing, and
golden tests passed, including synthetic demonstrations of the frozen
violations. No protected receipt was used to construct or tune those tests.

The correction has **no untouched performance evaluation**. Its tests prove
the implementation-level invariant and deterministic fallback behavior; they
do not prove QA accuracy, merchant-disjoint generalization, semantic-rule
validity across production, confidence calibration, or acceptable long-run
fragmentation.

## Final decision

1. **Do not promote frozen v4 (`4190a8789`).** Although all point-estimate
   gates pass, the candidate returns post-decoder-mutated labels that violate
   its claimed path semantics, and its gate cohort was viewed during a
   seven-trial calibration sweep before freeze. The independent audit is a
   strong reproducibility check, not an untouched promotion test.
2. **Use the correction (`2cb6f4ee2`) as the sole shadow candidate.** It is
   suitable for review and for a read-only, non-production shadow path. Do not
   merge it into a production-affecting branch or deploy it until the untouched
   evaluation below passes. If the target branch auto-deploys, leave the PR
   unmerged while shadow evidence is collected.
3. **Do not adapt either candidate further.** The correction must retain its
   current code and `a10752fd...` priors. A failed future evaluation is a
   reported failure, not permission to tune and recycle the cohort.

The smallest safe next step is a preregistered shadow evaluation on receipts
ingested after `2cb6f4ee2` was frozen. Produce predictions read-only before QA
labels exist, collect a fixed window, then score exactly once after labels are
locked. Prefer enough receipts to obtain at least 60 TXINFO truth rows so the
TXINFO gates have useful resolution. If future shadowing is unavailable, use
one truly untouched merchant-disjoint cohort whose merchants have zero
training receipts and whose receipts have never influenced labels, fixtures,
rules, or model choices.

### Branch and PR integration order

1. Preserve `4190a8789` as an immutable benchmark reference; do not open a
   promotion PR for it.
2. Review `codex/txinfo-sequence-invariants` at `2cb6f4ee2` plus this separate
   documentation commit. Do not rebase, regenerate priors, or refresh fixtures
   as part of review.
3. Open the correction PR as draft. Its allowed disposition is review plus
   shadow-only integration; production merge remains blocked.
4. Freeze the exact reviewed correction commit and unchanged priors hash in a
   shadow preregistration. Run it read-only on future receipts; do not write
   labels or external data from model predictions.
5. After the one locked evaluation, either report failure and stop or open a
   separate production-promotion PR containing the result and explicit
   approval. Never fold metric-driven fixes into that promotion PR.

### Draft PR description

**Title:** Keep semantic section constraints inside the semi-Markov decoder

**Summary**

- Makes the dynamic-programming path authoritative by removing post-decode
  label mutation.
- Moves three merchant-independent semantic compatibility rules into segment
  feasibility before decoding.
- Removes the merchant-specific `FRESH VALUE` rule and the tuned `1.25` local
  ITEMS override.
- Adds synthetic regression tests for isolated rows, unscored transitions,
  and misleading overridden confidence.
- Leaves frozen priors, thresholds, fixtures, holdouts, AWS, DynamoDB, and
  Chroma unchanged.

**Evidence**

- Based directly on frozen commit `4190a8789`.
- Correction commit: `2cb6f4ee2`.
- 17 focused, existing, and golden tests pass; deterministic output and
  no-LayoutLM/no-merchant fallback are preserved.
- Claude's independent benchmark of frozen v4 passed all five point-estimate
  gates, but the benchmark does not cover this correction and the cohort was
  used during pre-freeze calibration.

**Risk and rollout**

- This PR restores the path invariant but changes assignments wherever frozen
  post-decoder rules fired.
- No untouched accuracy claim is made for the correction.
- Review/shadow only. Production merge and deployment remain blocked pending
  one preregistered evaluation on future shadow receipts or a truly untouched
  merchant-disjoint cohort.

**Out of scope**

- No new model version, priors rebuild, threshold sweep, fixture refresh,
  holdout creation, protected evaluation, or external-data write.
- LayoutLM row probabilities and Chroma disagreement verification remain
  possible later projects; neither is a forced label source in this change.
