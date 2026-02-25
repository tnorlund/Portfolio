# Metadata Evaluation Stage Diagnosis

**Branch:** `feat/financial-math-chroma-llm-fallback`
**Date:** 2026-02-23
**Execution:** `764ae388-171d-43ab-878d-a6828d16a11f`

## Overview

The `evaluate_metadata_labels()` stage validates non-line-item labels: MERCHANT_NAME,
ADDRESS_LINE, PHONE_NUMBER, WEBSITE, STORE_HOURS, DATE, TIME, PAYMENT_METHOD, COUPON,
and LOYALTY_ID. It uses a 4-layer resolution pipeline before resorting to the LLM.

## Pipeline Flow (4 Resolution Layers)

1. **Collect metadata words** -- words with metadata labels, regex matches, or ReceiptPlace matches
2. **Auto-resolve** -- if label matches Google Places data or regex pattern, mark VALID (no LLM)
3. **ChromaDB consensus** -- query similar words across receipts for consensus voting
4. **LLM call** -- only for words not resolved by layers 1-3

## Key Files

| File | Purpose |
|------|---------|
| `receipt_agent/agents/label_evaluator/metadata_subagent.py` | Main evaluation logic + prompt building |
| `receipt_agent/prompts/structured_outputs.py` | `MetadataEvaluationResponse` Pydantic model |

## Statistics (719 receipts)

| Metric | Value |
|--------|-------|
| Total words evaluated | 14,866 |
| Receipts with decisions | 719 (100%) |
| **VALID** | **13,725 (92.3%)** |
| **INVALID** | **709 (4.8%)** |
| **NEEDS_REVIEW** | **432 (2.9%)** |

### Resolution Sources

| Source | Count | % |
|--------|-------|---|
| Auto-resolve (Google Places match) | 7,211 | 48.5% |
| Auto-resolve (regex pattern match) | 3,721 | 25.0% |
| **LLM-evaluated** | **3,580** | **24.1%** |
| ChromaDB-resolved | 354 | 2.4% |

**73.5% of decisions are already deterministic.** Only 3,580 words (24.1%) actually need the LLM.

### Duration

| Stat | Value |
|------|-------|
| Mean | 48.8s |
| Median | 19.0s |
| P90 | 169.6s |
| P99 | 322.1s |
| Receipts that skipped LLM | 63 (8.8%) |

## Label-Level Breakdown

| Label | Total | VALID % | INVALID % | NEEDS_REVIEW % |
|-------|-------|---------|-----------|----------------|
| ADDRESS_LINE | 4,875 | 94.5% | 2.4% | 3.1% |
| MERCHANT_NAME | 2,662 | 91.8% | 7.8% | 0.4% |
| PAYMENT_METHOD | 1,972 | 96.0% | 1.9% | 2.1% |
| TIME | 991 | 97.1% | 1.2% | 1.7% |
| DATE | 874 | 95.9% | 1.6% | 2.5% |
| PHONE_NUMBER | 835 | 95.4% | 2.3% | 2.3% |
| WEBSITE | 826 | 99.3% | 0.6% | 0.1% |
| STORE_HOURS | 742 | 78.3% | 13.1% | 8.6% |
| unlabeled | 586 | 70.0% | 20.1% | 9.9% |
| COUPON | 266 | 77.4% | 15.0% | 7.5% |
| LOYALTY_ID | 176 | 88.6% | 3.4% | 8.0% |

## Critical Finding: 33% of INVALID decisions have hallucinated suggested_label

| Category | Count | % |
|----------|-------|---|
| suggested=None (correct removal) | 204 | 28.8% |
| **suggested=label but reasoning says "remove"** | **234** | **33.0%** |
| suggested=label and reasoning agrees | 271 | 38.2% |

The LLM's structured output fills `suggested_label` with `MERCHANT_NAME` as a default
even when the reasoning says the word should be unlabeled. This is a significant quality
issue that affects 33% of all corrections.

## INVALID Decision Analysis (709 total)

### Top Label Transitions

| From | To | Count | Description |
|------|----|-------|-------------|
| MERCHANT_NAME | (none) | 60 | Mislabeled non-merchant text |
| STORE_HOURS | MERCHANT_NAME | 43 | Mostly hallucinated suggested_label |
| ADDRESS_LINE | MERCHANT_NAME | 40 | Mostly hallucinated |
| ADDRESS_LINE | (none) | 36 | OCR noise / non-address text |
| unlabeled | MERCHANT_NAME | 36 | Real: missing merchant name words |
| unlabeled | TIME | 22 | Real: "12:57" correctly identified |
| unlabeled | PAYMENT_METHOD | 19 | Real: "Credit", "VISA" identified |

### What the LLM handles that auto-resolve cannot

1. **OCR error tolerance** (key differentiator):
   - "Bivd" (Blvd), "OArS" (Oaks), "THILLAND" (Thousand)
   - Fuzzy matching against Google Places data
   - Too corrupted for exact string matching

2. **Contextual disambiguation**:
   - Is "25" a TIME or DATE component?
   - Is "Store" a heading or part of merchant name?
   - Is "AL" a state abbreviation or URL fragment?

3. **Unlabeled word detection**:
   - "Credit" -> PAYMENT_METHOD
   - "12:57" -> TIME
   - "Santa" -> MERCHANT_NAME (part of multi-word name)

## Recommendations

### 1. Expand deterministic patterns (saves ~730 LLM calls per batch)

| Pattern | Est. savings | Risk |
|---------|-------------|------|
| STORE_HOURS regex (day ranges, time ranges, "Hours") | ~581 | Low |
| Extended PAYMENT_METHOD keywords | ~100 | Low |
| TVR/terminal code exclusion | ~50 | Low |

### 2. Fix the suggested_label hallucination

Post-processing rule: if reasoning contains "should be unlabeled" / "no appropriate label" /
"does not match", override `suggested_label` to `None`. This fixes 234 incorrect suggestions.

### 3. Pre-filter LLM VALID confirmations

Of 2,793 LLM-evaluated VALID words:
- 758 ADDRESS_LINE words -- most are just confirming OCR-corrupted but correct addresses
- 581 STORE_HOURS words -- catchable by regex
- 410 unlabeled words -- LLM confirms they should stay unlabeled

Many of these could be handled by fuzzy matching (Levenshtein distance < 3 against Places data)
instead of an LLM call.

### 4. Consider skipping metadata evaluation entirely for financial accuracy

If the goal is financial accuracy (LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL), metadata
labels (MERCHANT_NAME, ADDRESS_LINE, etc.) don't affect it. The metadata stage could be
made optional or run with lower priority.

## Levenshtein Distance Experiment

**Date:** 2026-02-23
**Script:** `scripts/test_levenshtein_metadata.py`
**Dataset:** 727 receipt places (663 with metadata words)

### Approach

Applied `difflib.SequenceMatcher.ratio()` (already used in `receipt_agent/tools/places.py`)
on the 3,973 unresolved (LLM-bound) words against Google Places data:
- **Merchant name**: word-by-word comparison against `place.merchant_name`
- **Address**: word-by-word comparison against `place.formatted_address` + address components
- **Store hours**: word-by-word comparison against `place.hours_summary` entries
- Minimum word length: 3 characters (to skip noise like "a", "of", etc.)

### Results

| Threshold | Fuzzy Matches | % of LLM Words | Agrees w/ Current | Fills Unlabeled | Disagrees (FP) |
|-----------|--------------|-----------------|-------------------|-----------------|----------------|
| 0.7 | 219 | 5.5% | 115 | 43 | 61 |
| **0.8** | **143** | **3.6%** | **59** | **39** | **45** |
| 0.9 | 64 | 1.6% | 9 | 24 | 31 |

### Matched Words by Suggested Label (threshold=0.8)

| Label | Count |
|-------|-------|
| ADDRESS_LINE | 85 |
| MERCHANT_NAME | 48 |
| STORE_HOURS | 10 |

### Concrete Examples -- True Positives (OCR Corrections)

| OCR Text | Matched Against | Ratio | Label |
|----------|----------------|-------|-------|
| `WESILAKE,` | westlake | 0.875 | ADDRESS_LINE |
| `NESTLAKE,` | westlake | 0.875 | ADDRESS_LINE |
| `MABKET` | market | 0.833 | MERCHANT_NAME |
| `OKS` | oaks | 0.857 | ADDRESS_LINE |
| `Viflage,` | village | 0.857 | ADDRESS_LINE |
| `VilTage` | village | 0.857 | ADDRESS_LINE |
| `ClWestlake` | westlake | 0.889 | ADDRESS_LINE |
| `BIvdWestlake` | westlake | 0.800 | ADDRESS_LINE |
| `HEATHERCLIFT` | heathercliff | 0.917 | ADDRESS_LINE |

### Concrete Examples -- False Positives (Disagrees with Current Label)

| OCR Text | Matched Against | Ratio | Fuzzy Says | Current Label | Why Wrong |
|----------|----------------|-------|------------|---------------|-----------|
| `Chatsworth,` | chatsworth | 1.000 | MERCHANT_NAME | ADDRESS_LINE | Merchant name is "Hardwoods - Chatsworth" but word is address |
| `Card` | care | 0.750 | MERCHANT_NAME | PAYMENT_METHOD | "Neighbor Care" merchant vs "Card" payment |
| `WESTLAKE` | westlake | 1.000 | MERCHANT_NAME | ADDRESS_LINE | "Gelson's Westlake Village" -- word is address, not merchant |
| `Friday,` | friday | 1.000 | STORE_HOURS | DATE | Day name in date context, not hours |
| `91360` | 91362 | 0.800 | ADDRESS_LINE | STORE_HOURS | Zip code similar to another receipt's zip |

### Key Insight: Ambiguity Problem

The high false-positive rate reveals a fundamental issue: **words that appear in both
merchant name and address are ambiguous**. For example, "WESTLAKE" in "Gelson's Westlake
Village" matches the merchant name, but the word on the receipt is part of the address line.
Similarly, "Chatsworth" in "Hardwoods - Chatsworth" matches the merchant, but is an address
component.

The fuzzy matcher can identify *that a word relates to Places data*, but cannot determine
*which field* it belongs to without positional/contextual reasoning -- exactly what the LLM
provides.

### Recommendation

**Fuzzy matching alone is not a viable LLM replacement** for metadata words. At every
threshold:
- The false-positive rate is high (31-45% of matches disagree with current labels)
- Most disagreements are label-type confusion (MERCHANT_NAME vs ADDRESS_LINE), not
  wrong matches
- Only 3.6-5.5% of LLM words are matched, far less than the ~10% estimated

**Fuzzy matching could work as a _confirmation signal_** (not a label assigner):
- When current_label is ADDRESS_LINE and fuzzy matches address → auto-VALID (saves ~59
  LLM calls at t=0.8)
- When current_label is MERCHANT_NAME and fuzzy matches merchant → auto-VALID (included
  in the 59 above)
- This is safe because it only confirms existing labels, never assigns new ones

**Better ROI approaches** (from original recommendations):
1. STORE_HOURS regex expansion (~581 LLM calls saved vs ~59 from fuzzy)
2. Extended PAYMENT_METHOD keywords (~100 saved)
3. Fix suggested_label hallucination (234 incorrect suggestions)

## Potential Improvements

### 1. Fix ChromaDB consensus where-clause for words collection — DONE

**Impact:** Unlocks consensus resolution for STORE_HOURS and all other labels on the words collection.

**Status:** Fixed in `receipt_agent/utils/chroma_helpers.py`. New helper
`_build_label_where_clause()` selects the correct filter per collection: `$contains` on
`valid_labels_array` / `invalid_labels_array` for words, boolean `label_STORE_HOURS: True`
for lines. Uses existing `build_label_membership_clause()` from `label_metadata.py`.

**Investigation findings** (`scripts/test_chroma_store_hours.py`):

The words collection has **both** boolean flags and array fields, but coverage differs:

| Query style | STORE_HOURS matches |
|-------------|---------------------|
| `label_STORE_HOURS: True` (boolean) | 168 |
| `valid_labels_array $contains` | 200+ (hit limit) |
| `invalid_labels_array $contains` | 200+ (hit limit) |

The `$contains` approach finds substantially more words. More importantly, consensus works
**cross-merchant**: STORE_HOURS words from Amazon Fresh, CVS, Trader Joe's, and Wild Fork
all find high-similarity evidence (0.80-1.00) from other merchants' validated STORE_HOURS
words. Example cross-merchant matches:

| Query word | Merchant | Top cross-merchant match | Similarity |
|-----------|----------|--------------------------|------------|
| "24" | CVS | "24" / CVS Pharmacy | 1.000 |
| "HOURS" | CVS | "HOURS" / CVS Pharmacy | 1.000 |
| "Open" | Amazon Fresh | "Hours" / Sprouts | 0.827 |
| "8" | Amazon Fresh | "TO" / Trader Joe's | 0.827 |
| "daily" | Amazon Fresh | "DAILY" / Trader Joe's | 0.893 |

This confirms ChromaDB consensus can resolve STORE_HOURS labels across merchants once the
where-clause is fixed — complementing regex patterns for edge cases regex cannot catch.

### 1b. Fix consensus algorithm: top-K label-aware vote — DONE

**Impact:** Makes ChromaDB consensus effective — resolves ~55-60% of regex-missed words with 97-100% precision.

**Problem discovered:** Even with the correct `$contains` where-clause (fix #1), the original
two-query consensus approach is fundamentally broken. It queries for N valid neighbors and N
invalid neighbors separately, which **always returns balanced results** regardless of the true
valid/invalid ratio. For example, "MOM-SUN" (OCR variant of MON-SUN) gets 15 valid and 15
invalid neighbors, producing a near-zero consensus — even though the true ratio in the top-30
unfiltered neighbors is 28 valid vs 5 invalid.

**Solution:** Single unfiltered nearest-neighbor query + post-hoc label classification. For
each neighbor, check whether the target label appears in `valid_labels_array` or
`invalid_labels_array`. Ignore neighbors with neither (they have no opinion on this label).
Take the top-K most similar label-aware neighbors and vote.

**Threshold sweep results** (tested 9 strategies × 9 thresholds × 2 min-evidence values):

| Strategy | MinEv | Thresh | STORE_HOURS TP/FP | PAYMENT_METHOD TP/FP | Precision |
|----------|-------|--------|-------------------|----------------------|-----------|
| Weighted consensus | 2 | 0.60 | 18/1 | 29/2 | 94% |
| Simple ratio | 2 | 0.60 | 18/1 | 29/2 | 94% |
| **Top-5 vote (label-aware)** | **2** | **0.60** | **17/0** | **28/1** | **98%** |
| Top-10 vote (label-aware) | 2 | 0.60 | 18/1 | 29/2 | 94% |
| Ratio, label-aware, cross-merchant | 2 | 0.60 | 14/0 | 27/1 | 98% |

**Winner: Top-5 vote, label-aware** — at threshold=0.60, min_evidence=2:
- STORE_HOURS: 17/31 TP, 0 FP (55% recall, 100% precision)
- PAYMENT_METHOD: 28/50 TP, 1 FP (56% recall, 97% precision)
- Combined F1: 0.72

At lower threshold=0.30: recall improves to 61% (19/31 + 30/50) with same precision. The
threshold=0.60 default is conservative — requires 4/5 or 5/5 agreement among label-aware
neighbors.

**Status:** Implemented in `receipt_agent/utils/chroma_helpers.py`. New function
`_compute_top_k_word_consensus()` does a single unfiltered query, filters to label-aware
neighbors, takes top 5, and votes. `chroma_resolve_words()` updated to use this approach
with defaults threshold=0.60, min_evidence=2 (previously 0.75, 4).

### 2. STORE_HOURS regex expansion (~581 LLM calls saved)

Add day/time patterns to `detect_pattern_type()` to auto-resolve STORE_HOURS words. Common
patterns like "MON-SUN", "9AM-10PM", "Hours:" appear 233+ times and are deterministic.

### 3. Extended PAYMENT_METHOD keywords (~100 LLM calls saved)

Add additional payment method keywords ("Credit", "VISA", "Mastercard", "Debit", "Cash") to
the auto-resolve layer.

### 4. Fix suggested_label hallucination (234 incorrect suggestions)

Post-processing rule: if reasoning contains "should be unlabeled" / "no appropriate label" /
"does not match", override `suggested_label` to `None`. Currently 33% of INVALID decisions
have a hallucinated `suggested_label` (typically defaulting to MERCHANT_NAME).

### 5. Pre-filter LLM VALID confirmations (skip already-correct labels)

Of 2,793 LLM-evaluated VALID words, many are simply confirming labels that are already correct.
When the current label matches ChromaDB consensus or fuzzy-matches Places data, skip the LLM
call entirely. Targets: 581 STORE_HOURS (with regex fix), 758 ADDRESS_LINE (with fuzzy
confirmation), 410 unlabeled words the LLM confirms should stay unlabeled.

## Measured Results (2026-02-24)

**Execution:** `6ae90b47-34dc-4721-af72-70bd2965b1c7`
**LangSmith project:** `label-eval-topk-consensus-all-stages`
**Fixes deployed:** Top-K consensus (#1b), STORE_HOURS regex, expanded PAYMENT_METHOD regex (#2/#3)

### Comparison: Feb 22 baseline vs Feb 24 with fixes

| Metric | Baseline (Feb 22) | With Fixes (Feb 24) | Change |
|--------|-------------------|---------------------|--------|
| **Total Duration** | **2h 10m** | **~40m** | **-70%** |
| Receipts | 716 | 714 | ~same |
| LLM Timeouts | 46 | 24 | **-48%** |
| Failures | — | 0 | clean |

### Metadata auto-resolution (CloudWatch logs)

| Metric | Baseline | With Fixes | Change |
|--------|----------|------------|--------|
| Total metadata words found | 15,023 | 17,036 | +13% (more detected by regex) |
| Auto-resolved without LLM | 10,598 (70.6%) | 12,102 (71.0%) | +1,504 more resolved |
| Words sent to metadata LLM | 4,425 | 4,934 | +509 |
| Receipts fully skipping LLM | 28 | 24 | -4 |

The regex expansion detected ~2,000 more metadata words (STORE_HOURS, PAYMENT_METHOD patterns)
and auto-resolved 1,500 more. But the LLM still received ~500 more words because the detected
pool grew. The auto-resolve *rate* is nearly identical (~71%).

### Currency evaluation (removed)

| Metric | Baseline | With Fixes | Change |
|--------|----------|------------|--------|
| Currency issues evaluated by LLM | 9,768 | 0 | **-100%** |

Currency LLM removal was the **single biggest time saver** — ~9,800 fewer LLM evaluations.

### Review stage (top-K consensus)

| Metric | Baseline | With Fixes | Change |
|--------|----------|------------|--------|
| Receipts entering LLM review | 380 | 367 | **-3.4%** |
| Total review decisions applied | 4,764 | 6,244 | +31% more decisions |
| Labels confirmed | 2,956 | 4,796 | **+62%** |
| Labels invalidated | 996 | 584 | **-41%** |

The top-K consensus fix makes **more decisions** (6,244 vs 4,764) with **fewer LLM calls**
(367 vs 380). More words get resolved by consensus alone. The +62% confirmed labels means
the consensus is now actually working — previously all 1,501 review decisions had zero
ChromaDB evidence.

### Key takeaway

The ~90 minute speedup is dominated by **currency LLM removal** (9,768 fewer evaluations).
The metadata regex and ChromaDB consensus contributed modest improvements. Reduced LLM
pressure also cut timeouts in half (46→24), compounding the savings.

## Bottom Line

73.5% of metadata decisions are already deterministic. Of the remaining 24.1% sent to the
LLM, the genuine value-add is OCR fuzzy matching (~10%) and contextual disambiguation (~5%).
Expanding the auto-resolve layer with fuzzy matching and extended patterns could reduce LLM
usage to ~10-15% of words, saving ~50-60s per receipt.
