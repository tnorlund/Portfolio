# Label Harmonizer V2 - Similarity Validation Progress

## Overview

The similarity validation feature adds a second-layer validation step that uses ChromaDB to find semantically similar words with validated labels, then uses an LLM to confirm if a suggested label type is appropriate based on those examples.

## Implementation Status: ✅ WORKING

### Current Behavior (as of 2025-11-27)

Based on production logs from execution `2e795d51-032f-475d-8bb0-3b093048ca54`:

#### ✅ Success Cases

1. **'Main:' -> PHONE_NUMBER**: VALIDATED
   - Found similar words with validated `PHONE_NUMBER` labels
   - LLM validated: "Main:" appears directly before the store's main telephone number
   - **Result**: Suggestion accepted and applied

2. **'Vons.' -> MERCHANT_NAME**: VALIDATED
   - Found similar words with validated `MERCHANT_NAME` labels
   - LLM validated: "Vons." appears as a standalone line at the end of the receipt
   - **Result**: Suggestion accepted and applied

#### ⚠️ Rejection Cases (Working as Designed)

1. **'Store' -> ADDRESS_LINE**: REJECTED
   - **Reason**: No similar words with validated label 'ADDRESS_LINE' found
   - **Result**: Marked as `NEEDS_REVIEW` (correct behavior - no validated examples to compare against)

### Statistics from Latest Run

- **Outliers detected**: 16 for Vons MERCHANT_NAME
- **Validated suggestions**: 2 (Main: -> PHONE_NUMBER, Vons. -> MERCHANT_NAME)
- **Rejected suggestions**: 14 (marked as NEEDS_REVIEW)
- **Success rate**: 2/16 = 12.5% validated (rest need human review)

## How It Works

### Step 1: Initial LLM Suggestion
When an outlier is detected, the LLM suggests a new label type (e.g., "PHONE_NUMBER" for "Main:").

### Step 2: Similarity Validation
1. **Query ChromaDB**: Find semantically similar words using the outlier's embedding
   - Optionally filters by `merchant_name` if available
   - Gets top 50 similar words

2. **Filter for Validated Labels**: In Python, filter results to only include words where:
   - `validated_labels` contains the suggested label type (e.g., `",PHONE_NUMBER,"`)
   - Uses comma-delimited pattern matching: `f",{suggested_label_type}," in validated_labels_str`

3. **Enrich with Context**: For each similar word found:
   - Fetch full receipt context (±3 lines)
   - Include surrounding words, line context, merchant name
   - Calculate similarity score

4. **LLM Validation**: Present the similar words and their context to the LLM:
   - Ask: "Does this word appear in a similar context to these validated examples?"
   - LLM responds with `is_valid` (boolean) and `reasoning` (string)

### Step 3: Apply or Reject
- **If VALID**: Suggestion is accepted, new label is created
- **If INVALID or no similar words found**: Original label is marked as `NEEDS_REVIEW`

## Technical Details

### ChromaDB Query Pattern

```python
# Query for similar words (same pattern as _identify_outliers)
merchant_filter = merchant_name.strip().title() if merchant_name else None
where_clause = {"merchant_name": {"$eq": merchant_filter}} if merchant_filter else None

query_results = self.chroma.query(
    collection_name="words",
    query_embeddings=[target_embedding],
    n_results=50,  # Get more results to filter in Python
    where=where_clause,
    include=["documents", "metadatas", "distances"],
)

# Filter in Python for validated_labels containing the suggested label type
label_pattern = f",{suggested_label_type},"
for doc, metadata, distance in zip(...):
    validated_labels_str = metadata.get("validated_labels", "")
    if label_pattern in validated_labels_str:
        # Include this word in validation
```

### Why Filter in Python?

ChromaDB's `where` clause doesn't support substring matching (`$contains` only works in `where_document` for document text, not metadata). The supported operators are:
- `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$and`, `$or`

Since `validated_labels` is stored as a comma-delimited string (e.g., `",MERCHANT_NAME,PHONE_NUMBER,"`), we must:
1. Query ChromaDB for similar words (optionally filtered by merchant)
2. Filter in Python to check if the label pattern exists in `validated_labels`

## Key Fixes Applied

1. **Removed incorrect `label_status` filter**: ChromaDB metadata doesn't have a `label_status` field
2. **Fixed ChromaDB query pattern**: Now matches `_identify_outliers` pattern (query, then filter in Python)
3. **Fixed numpy array handling**: Properly check for embeddings using `len()` instead of truthiness
4. **Added `is_noise` check**: Skip validation for noise words (not embedded in ChromaDB)

## Future Improvements

1. **Increase validation success rate**: Currently only 12.5% of suggestions are validated
   - May need more validated examples in ChromaDB
   - Could adjust similarity threshold
   - Could improve LLM validation prompt

2. **Better handling of edge cases**:
   - Words with no similar validated examples (currently marked NEEDS_REVIEW - correct)
   - Words with low similarity scores to validated examples

3. **Metrics**: Track validation success/failure rates in CloudWatch

## Related Files

- `receipt_agent/receipt_agent/tools/label_harmonizer_v2.py`: Main implementation
- `infra/label_harmonizer_step_functions/lambdas/harmonize_labels.py`: Lambda handler
- `receipt_chroma/receipt_chroma/embedding/metadata/word_metadata.py`: ChromaDB metadata structure

