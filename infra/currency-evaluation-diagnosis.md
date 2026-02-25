# Currency Evaluation Stage Diagnosis

**Branch:** `feat/financial-math-chroma-llm-fallback`
**Date:** 2026-02-23
**Execution:** `764ae388-171d-43ab-878d-a6828d16a11f`

## Overview

The `evaluate_currency_labels()` stage validates labels on line-item rows of receipts
(LINE_TOTAL, UNIT_PRICE, QUANTITY, PRODUCT_NAME, DISCOUNT, etc.). It runs an LLM call
per receipt via OpenRouter (`gpt-oss-120b`) with structured output.

## Pipeline Flow

1. **Identify line-item rows** -- lines containing any of `{PRODUCT_NAME, LINE_TOTAL, UNIT_PRICE, QUANTITY}`
2. **Collect currency words** -- labeled currency words + unlabeled words that look like currency on line-item rows
3. **ChromaDB pre-check** -- attempt merchant-level consensus voting to auto-resolve
4. **LLM call** -- send full receipt text + numbered word table, get back `CurrencyEvaluationResponse`
5. **Format decisions** -- map LLM output to `{VALID, INVALID, NEEDS_REVIEW}` per word

## Key Files

| File | Purpose |
|------|---------|
| `receipt_agent/agents/label_evaluator/currency_subagent.py` | Main evaluation logic + prompt building |
| `receipt_agent/prompts/structured_outputs.py` | `CurrencyEvaluationResponse` Pydantic model |
| `receipt_agent/agents/label_evaluator/financial_subagent.py` | Deterministic math checks (already exists) |

## Statistics (719 receipts)

| Metric | Value |
|--------|-------|
| Receipts evaluated | 545 / 719 (75.8%) |
| Total words evaluated | 9,568 |
| Mean words per receipt | 17.6 (median 10.0, max 300) |
| **VALID** | **7,315 (76.5%)** |
| **NEEDS_REVIEW** | **2,018 (21.1%)** |
| **INVALID** | **235 (2.5%)** |

### Duration

| Stat | Value |
|------|-------|
| Mean | 65.2s |
| Median | 24.7s |
| Max | 441.9s |
| Total LLM time | 592 min (9.9 hours) |

## Critical Finding: NEEDS_REVIEW is 98% system failure

**1,983 of 2,018 NEEDS_REVIEW decisions (98.3%)** have the reasoning:
> "No decision from LLM (ChromaDB fallback also inconclusive)"

These are NOT genuine ambiguity. The LLM returned fewer evaluations than words sent
(structured output truncation on large receipts). The padding logic fills missing words
with NEEDS_REVIEW at low confidence.

Receipts with >50 words have near-100% NEEDS_REVIEW rates. Only 35 NEEDS_REVIEW
decisions represent genuine LLM uncertainty.

## INVALID Decision Breakdown (235 total)

| Category | Count | % | Deterministic? |
|----------|-------|---|----------------|
| Non-numeric text with currency label | 56 | 23.8% | Yes -- `not any(c.isdigit() for c in text)` |
| Summary label swaps (ST<->GT) | 39 | 16.6% | Partially -- keyword scan on adjacent words |
| LINE_TOTAL <-> UNIT_PRICE confusion | 29 | 12.3% | No -- requires layout context |
| Unlabeled currency needing labels | 26 | 11.1% | Partially -- position-based rules |
| Negative amounts -> DISCOUNT | 7 | 3.0% | Yes -- `text.startswith('-')` |
| Product codes mislabeled | 7 | 3.0% | No -- requires context |
| Other (context-dependent) | 71 | 30.2% | No |

### Examples

**Deterministically replaceable:**
- `"-7.66"` LINE_TOTAL -> DISCOUNT: "Negative amount represents a discount"
- `"ricing"` DISCOUNT -> PRODUCT_NAME: "Part of 'Pricing', not a discount"
- `"$10.00"` unlabeled -> LINE_TOTAL: "Currency amount on line-item row"

**Requires LLM judgment:**
- `"3.00"` LINE_TOTAL -> UNIT_PRICE: "3.00 is the unit price; 6.00 on the same line is the line total"
- `"5@1.65"` LINE_TOTAL -> QUANTITY: "Combines quantity and unit price notation"
- `"712723"` UNIT_PRICE -> PRODUCT_NAME: "Product code, not a price"

## What the LLM actually does per receipt

1. Receives full receipt text (up to 50 lines) with `text[label]` annotations
2. Receives a numbered table of words to evaluate with position zone (left/center/right)
3. Optionally receives merchant pattern context (expected label positions)
4. Returns structured `{index, decision, reasoning, suggested_label, confidence}` per word

## Recommendations

### 1. Chunk large receipts (fixes 98% of NEEDS_REVIEW)

Receipts with >50 currency words overwhelm the structured output. Split into batches
of 30-40 words. This eliminates ~1,983 bogus NEEDS_REVIEW decisions.

### 2. Skip obvious VALID confirmations (cuts LLM volume 60-70%)

Pre-filter words where the label is trivially correct:
- PRODUCT_NAME on non-numeric text
- LINE_TOTAL on `$XX.XX` format in the right position zone
- QUANTITY on small integers on line-item rows

Only send genuinely questionable words to the LLM.

### 3. Add deterministic pre-correction rules (~85 of 235 INVALID)

- Negative values labeled LINE_TOTAL -> auto-correct to DISCOUNT
- Non-numeric text with currency labels -> auto-correct to PRODUCT_NAME or unlabel
- "Total"/"Balance Due"/"Amount Due" adjacent words with SUBTOTAL -> auto-correct to GRAND_TOTAL

### 4. Run financial math BEFORE currency evaluation

The financial subagent's equation checks (QTY x UNIT_PRICE = LINE_TOTAL) can confirm
or correct labels when math balances. Currently financial runs AFTER currency, but the
deterministic math could pre-resolve LINE_TOTAL vs UNIT_PRICE confusion.

## Resolution (2026-02-23)

**Decision: Removed entirely.** Commit `d2d29e0a3`.

Rather than optimizing the currency LLM (chunking, pre-filtering, deterministic rules),
we removed the call from the pipeline completely:

- Financial validation already re-fetches fresh labels from DynamoDB and runs
  deterministic math checks independently -- zero overlap with currency corrections.
- Currency data was not surfaced on the `receipt.tsx` page -- no user-facing impact.
- The 2.5% INVALID rate (~150 genuinely useful decisions out of 9,568) did not justify
  9.9 hours of LLM compute and 922 rate limit errors per batch.

`currency_subagent.py` is kept intact for potential future use as an async agent for
ambiguous cases. The pipeline now runs metadata + geometric + financial only.

## Original Bottom Line

The LLM did genuine useful work on ~150 of 9,568 decisions (1.6%). The remaining
98.4% were trivial confirmations, system failures, or rule-catchable cases.
