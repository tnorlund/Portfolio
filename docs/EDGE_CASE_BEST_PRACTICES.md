# Edge Case Development Best Practices

## Overview

Edge cases should only be created from **high-confidence, stable invalid patterns** to avoid creating rules from temporary inconsistencies during active harmonization.

## Principles

### 1. **Stability Over Speed**
- Don't create edge cases from labels that might be corrected soon
- Wait for patterns to stabilize before codifying them
- Prefer manual review over automatic edge case generation during active harmonization

### 2. **High Confidence Thresholds**
- Require **higher occurrence counts** (5-10 minimum, not 3)
- Prefer patterns that appear across **multiple merchants** (global patterns)
- Avoid merchant-specific edge cases unless they're very clear (e.g., OCR errors)

### 3. **Validation Source Matters**
- **ChromaDB conflicts** (similar words have different VALID labels) = high confidence
- **LLM rejections** during harmonizer = medium confidence (could be wrong)
- **Manual review** = highest confidence (if available)
- **CoVe validation** = high confidence

### 4. **Temporal Stability**
- Only use labels that have been INVALID for **at least 7 days**
- Avoid labels recently marked INVALID (might be part of active harmonization)
- Prefer labels with `timestamp_added` older than recent harmonizer runs

### 5. **Review Before Loading**
- **Always review** edge cases before loading into DynamoDB
- Start with **dry-run mode** to see what would be created
- Load edge cases in **batches** (one label type at a time)
- Monitor edge case performance after loading

## Recommended Workflow

### Phase 1: Generate Candidates (Review Only)
```bash
# Generate with high thresholds
python scripts/generate_edge_cases.py \
  --label-type MERCHANT_NAME \
  --min-occurrences 10 \
  --output merchant_name_candidates.json

# Review the candidates
python scripts/review_edge_cases.py merchant_name_candidates.json --label-type MERCHANT_NAME
```

### Phase 2: Manual Review
- Review each edge case candidate
- Check examples to ensure they're truly invalid
- Remove false positives
- Group similar patterns

### Phase 3: Load in Batches
```bash
# Load only after review
python scripts/load_edge_cases_to_dynamo.py \
  --input merchant_name_candidates.json \
  --dry-run  # First, see what would be loaded

# Then load for real
python scripts/load_edge_cases_to_dynamo.py \
  --input merchant_name_candidates.json
```

### Phase 4: Monitor Performance
- Track edge case usage statistics
- Monitor false positive rate
- Remove or refine edge cases that perform poorly

## Conservative Thresholds During Active Harmonization

| Pattern Type | Min Occurrences | Min Merchants | Notes |
|-------------|-----------------|--------------|-------|
| **Global patterns** | 10+ | 3+ | Safe to use across all merchants |
| **Merchant-specific** | 5+ | 1 | Only if clearly an OCR error or obvious mistake |
| **Punctuation-only** | 3+ | Any | Safe - clearly invalid |
| **Common words** ("The", "AND") | 10+ | 5+ | Safe - clearly not merchant names |

## Red Flags (Don't Create Edge Cases For)

1. **Labels proposed by harmonizer** (`label_proposed_by == "label-harmonizer"`)
   - These are actively being corrected
   - Wait until harmonization completes

2. **Recently added labels** (within last 7 days)
   - Might be part of active harmonization
   - Wait for stability

3. **Labels with `label_consolidated_from`**
   - These were corrected from another label
   - The correction process is working, don't block it

4. **Low occurrence counts** (< 5)
   - Might be one-off errors
   - Not a pattern worth codifying

5. **Merchant-specific patterns that are part of merchant name**
   - "Decor" from "Floor & Decor" - this is a splitting issue, not an edge case
   - Better to fix the splitting logic

## When to Create Edge Cases

✅ **Good candidates:**
- Punctuation-only words ("-", ".", ":")
- Common English words that are clearly not the label type ("The", "AND", "Store")
- Words that appear across many merchants with the same invalid pattern
- OCR errors that are consistent (e.g., "EWHOLESALE" from "Wholesale")

❌ **Bad candidates:**
- Parts of merchant names that got split ("Decor" from "Floor & Decor")
- Labels that are actively being harmonized
- Patterns that only appear once or twice
- Merchant-specific quirks that might be valid in context

## Iterative Refinement

1. **Start conservative**: High thresholds, manual review
2. **Monitor usage**: Track which edge cases are used most
3. **Measure accuracy**: Calculate false positive rate
4. **Refine over time**: Lower thresholds as system stabilizes
5. **Remove poor performers**: Delete edge cases with high false positive rates

## Example: MERCHANT_NAME Edge Cases

From our analysis, here are the patterns:

### ✅ Safe to Use (High Confidence)
- **"-"** (punctuation) - 9 occurrences, clearly invalid
- **"AND"** (common word) - 11 occurrences, clearly not a merchant name
- **"The"** (article) - 7 occurrences, clearly not a merchant name

### ⚠️ Review Carefully (Medium Confidence)
- **"Westlake"**, **"Village"**, **"Oaks"** - These are location names, might be valid in some contexts
- **"BAKERY"**, **"CLOSET"** - Business type words, might be part of merchant name

### ❌ Don't Use (Low Confidence)
- **"Decor"** from "Floor & Decor" - This is a splitting issue, not an edge case
- **"DIY"** from "DIY Home Center" - Part of merchant name
- Most merchant-specific patterns - These are splitting/OCR issues, not true edge cases

## Conclusion

During active harmonization, **be conservative**. It's better to:
- Review manually before loading
- Use higher thresholds
- Focus on clear, global patterns
- Wait for system to stabilize before creating merchant-specific rules

As the system matures and harmonization completes, you can:
- Lower thresholds
- Add more merchant-specific patterns
- Automate more of the process

