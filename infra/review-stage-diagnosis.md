# LLM Review Stage Diagnosis

**Branch:** `feat/financial-math-chroma-llm-fallback`
**Date:** 2026-02-23
**Execution:** `764ae388-171d-43ab-878d-a6828d16a11f`

## Overview

The LLM review stage runs AFTER currency/metadata evaluation and AFTER financial validation.
It processes geometric anomalies detected by `evaluate_word_contexts()` in `issue_detection.py`.
It is the final stage and runs **sequentially**, directly extending the critical path.

## Trigger Condition

```python
if geometric_issues_found > 0:
    # run review
```

Review does NOT trigger from currency/metadata NEEDS_REVIEW decisions (2,450 total).
It ONLY triggers from geometric anomaly detection.

## Active Detection Rules (3 of 6)

| Rule | Issues Found | True Positive Rate |
|------|-------------|-------------------|
| `missing_label_cluster` | 1,006 | 46% |
| `missing_constellation_member` | 329 | 22% |
| `text_label_conflict` | 213 | ~50% |

Three rules are disabled due to high false positive rates (>89%):
`check_position_anomaly`, `check_unexpected_label_pair`, `check_geometric_anomaly`

## Key Files

| File | Purpose |
|------|---------|
| `receipt_agent/agents/label_evaluator/issue_detection.py` | 6 geometric detection rules |
| `receipt_agent/prompts/label_evaluator.py` | `build_receipt_context_prompt()` for review |
| `receipt_agent/prompts/structured_outputs.py` | `BatchedReviewResponse` Pydantic model |
| `infra/label_evaluator_step_functions/lambdas/unified_receipt_evaluator.py` | Review orchestration (lines 1466-1870) |

## Statistics (719 receipts)

| Metric | Value |
|--------|-------|
| Receipts that triggered review | 362 / 719 (50.3%) |
| Receipts that skipped review | 357 (49.7%) |
| Total review decisions | 1,501 |
| **VALID** | **1,119 (74.6%)** |
| **INVALID** | **280 (18.7%)** |
| **NEEDS_REVIEW** | **102 (6.8%)** |

### Receipt-Level Impact

| Outcome | Count | % of reviewed |
|---------|-------|---------------|
| ALL decisions VALID (no changes) | 198 | 54.7% |
| At least one INVALID applied | 153 | 42.3% |
| Zero DynamoDB changes made | 209 | 57.7% |

**57.7% of receipts that run review get zero label changes.** The LLM just confirms
the geometric detector's false positives as VALID.

### Duration

| Stat | Value |
|------|-------|
| Total review time | 19,188s (5.3 hours) |
| **Review as % of total pipeline** | **24.8%** |
| Mean per receipt | 53.0s |
| Median | 22.6s |
| P90 | 165.2s |

## Critical Finding: ChromaDB evidence was completely absent

All 1,501 review decisions had **zero evidence items** and 0.0 consensus scores.
The ChromaDB consensus pre-check (threshold 0.75, min 4 items) could never fire.
Every single issue went to the LLM.

If ChromaDB evidence were available, some fraction of the 1,119 VALID confirmations
could be auto-resolved without an LLM call.

## What Labels Were Changed (280 INVALID)

| Transition | Count | % |
|-----------|-------|---|
| unlabeled -> ADDRESS_LINE | 78 | 27.9% |
| unlabeled -> STORE_HOURS | 34 | 12.1% |
| unlabeled -> MERCHANT_NAME | 30 | 10.7% |
| unlabeled -> PRODUCT_NAME | 27 | 9.6% |
| unlabeled -> PAYMENT_METHOD | 19 | 6.8% |
| unlabeled -> COUPON | 15 | 5.4% |
| unlabeled -> WEBSITE | 10 | 3.6% |
| Various -> (remove label) | 15 | 5.4% |
| LINE_TOTAL -> UNIT_PRICE | 6 | 2.1% |
| Other corrections | 46 | 16.4% |

**87% of INVALID decisions (243/280) add labels to previously UNLABELED words.**
Only 37 decisions (13%) correct an existing wrong label.

## Key Patterns

### 1. Review solves a different problem than currency/metadata

Currency/metadata evaluate words that ALREADY have labels. Review addresses:
- **Missing labels** detected by geometric clustering (unlabeled word surrounded by labeled neighbors)
- **Missing constellation members** (expected label position has no labeled word)
- **Text conflicts** (same text with different labels at different positions)

Only 197 of 1,501 review words overlap with currency words, and 71 with metadata words.

### 2. The labels added are non-financial

Top additions: ADDRESS_LINE (28%), STORE_HOURS (12%), MERCHANT_NAME (11%), PRODUCT_NAME (10%).
These improve labeling completeness but do NOT affect financial accuracy.

### 3. High confidence on changes

268 of 280 INVALID decisions (95.7%) were "high" confidence. The LLM is fairly certain
when it does make changes.

## Recommendations

### 1. Fix ChromaDB evidence (biggest win)

If ChromaDB evidence were available, the consensus pre-check could auto-resolve many of
the 1,119 VALID confirmations without an LLM call. Investigate why evidence was empty:
- Was ChromaDB client unavailable?
- Were the words/lines collections empty?
- Was the query failing silently?

### 2. Tighten geometric detection thresholds

`missing_label_cluster` (1,006 issues, 46% TP rate) and `missing_constellation_member`
(329 issues, 22% TP rate) produce many false positives. Raising confidence thresholds
would reduce the number of issues sent to the LLM.

### 3. Make review optional for financial-only use cases

If downstream only cares about financial accuracy, review can be skipped entirely.
The 280 label corrections are ADDRESS_LINE, STORE_HOURS, MERCHANT_NAME, etc. --
none affect equation balancing.

### 4. Run review in parallel with financial validation

Currently the pipeline is:
```
[currency + metadata + geometric] (parallel) -> financial (sequential) -> review (sequential)
```

Review doesn't depend on financial validation results. It could run in parallel:
```
[currency + metadata + geometric] (parallel) -> [financial + review] (parallel)
```

This would save ~53s mean per receipt on the critical path.

### 5. Skip LLM for low-confidence geometric issues

Only send issues to the LLM when the geometric detector has high confidence.
`missing_constellation_member` at 22% TP rate means 78% of its flags waste LLM time.

## What Would We Lose by Removing Review?

- **Time saved:** 5.3 hours (24.8% of pipeline)
- **Labels lost:** 280 corrections across 153 receipts
  - 243 missing label additions (ADDRESS_LINE, STORE_HOURS, MERCHANT_NAME, etc.)
  - 37 label corrections
  - All non-financial -- no impact on equation balancing
- **2,450 NEEDS_REVIEW decisions from currency/metadata remain unresolved regardless**
  (review never processed these)

## Fix Applied: Top-K Label-Aware Consensus (2026-02-24)

**Commit:** `4ac9f2e7b`
**Execution:** `6ae90b47-34dc-4721-af72-70bd2965b1c7`

### What changed

Replaced `query_label_evidence()` + `compute_label_consensus()` (two-query approach that
always returned balanced results) with `query_top_k_word_evidence()` (single unfiltered
query + post-hoc label-aware vote). Default thresholds lowered from 0.75/4 to 0.60/2.

### Measured results

| Metric | Before Fix (Feb 22) | After Fix (Feb 24) | Change |
|--------|---------------------|---------------------|--------|
| Receipts entering LLM review | 380 | 367 | **-3.4%** |
| Total review decisions applied | 4,764 | 6,244 | **+31%** |
| Labels confirmed | 2,956 | 4,796 | **+62%** |
| Labels invalidated | 996 | 584 | **-41%** |
| Labels created | 812 | 864 | +6% |

The consensus fix means:
- **More decisions made** (6,244 vs 4,764) — consensus now actually resolves words
- **Fewer LLM calls** (367 vs 380) — some receipts skip LLM entirely
- **+62% confirmed labels** — consensus validates labels without needing LLM
- **-41% invalidations** — fewer false positives reach the LLM

Previously, all 1,501 review decisions had zero ChromaDB evidence. Now the consensus is
working and resolving words that previously all fell through to the LLM.

## Bottom Line

The review stage spends 24.8% of pipeline time to make 280 non-financial label corrections.
57.7% of reviewed receipts get zero changes. ~~ChromaDB evidence being absent forces all 1,501
issues through the LLM when many could be auto-resolved.~~ **Fixed:** Top-K consensus now
resolves a significant portion of review decisions without LLM. Remaining improvements:
tighten geometric detection thresholds to reduce false positive rates, and consider running
review in parallel with financial validation.
