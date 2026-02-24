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

## Bottom Line

73.5% of metadata decisions are already deterministic. Of the remaining 24.1% sent to the
LLM, the genuine value-add is OCR fuzzy matching (~10%) and contextual disambiguation (~5%).
Expanding the auto-resolve layer with fuzzy matching and extended patterns could reduce LLM
usage to ~10-15% of words, saving ~50-60s per receipt.
