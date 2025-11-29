# Label Suggestion Agent - Summary

## What It Does

The label suggestion agent finds unlabeled words on a receipt and suggests appropriate CORE_LABEL types using ChromaDB similarity search, minimizing LLM calls.

## Example Workflow

### Input
- **Receipt**: Image ID `img_abc123`, Receipt ID `42`
- **Unlabeled words**: 15 words that don't have any labels yet
- **Existing labels**: 8 words already have labels (MERCHANT_NAME, DATE, etc.)

### Process

1. **Get Receipt Context**
   - Fetches all words (excluding `is_noise=True`)
   - Identifies 15 unlabeled words
   - Gets merchant name: "Vons"
   - Checks merchant receipt count: 25 receipts (≥10, so will use merchant filter)

2. **For Each Unlabeled Word**

   **Example Word 1: "TOTAL"**
   - Searches ChromaDB for similar words with VALID labels
   - Finds 12 similar words with VALID `GRAND_TOTAL` labels
   - Average similarity: 0.87
   - Confidence: 0.92 (high)
   - **Decision**: Suggest `GRAND_TOTAL` directly (NO LLM call)
   - Reasoning: "Found 12 similar words with VALID GRAND_TOTAL labels (avg similarity: 0.87)"

   **Example Word 2: "12/25/2023"**
   - Searches ChromaDB
   - Finds 8 similar words with VALID `DATE` labels
   - Average similarity: 0.91
   - Confidence: 0.88 (high)
   - **Decision**: Suggest `DATE` directly (NO LLM call)
   - Reasoning: "Found 8 similar words with VALID DATE labels (avg similarity: 0.91)"

   **Example Word 3: "Stand"** (ambiguous)
   - Searches ChromaDB
   - Finds candidates:
     - `MERCHANT_NAME`: 5 matches, confidence 0.65
     - `PRODUCT_NAME`: 4 matches, confidence 0.58
   - Multiple candidates with similar confidence
   - **Decision**: Use LLM to choose (1 LLM call)
   - LLM analyzes context: word appears in header line, merchant is "The Stand"
   - LLM suggests: `MERCHANT_NAME` with reasoning

   **Example Word 4: "xyz"** (low confidence)
   - Searches ChromaDB
   - Finds 2 similar words with `PRODUCT_NAME` labels
   - Average similarity: 0.45
   - Confidence: 0.42 (low)
   - **Decision**: Skip (insufficient evidence)

3. **Submit Suggestions**
   - Creates 12 PENDING labels:
     - 10 high-confidence suggestions (ChromaDB only)
     - 2 LLM-assisted suggestions
   - Total LLM calls: 2 (out of 15 unlabeled words)

### Output

```
Status: success
Unlabeled words: 15
Suggestions made: 12
LLM calls: 2

Created labels:
  - Word 5 (line 3): GRAND_TOTAL (confidence: 0.92)
  - Word 8 (line 3): DATE (confidence: 0.88)
  - Word 12 (line 1): MERCHANT_NAME (confidence: 0.75)
  ... (9 more)
```

## Key Features

1. **Chroma-First**: Uses ChromaDB similarity search as primary method
2. **Minimal LLM**: Only 2 LLM calls out of 15 words (87% reduction)
3. **Merchant Filtering**: Uses merchant filter when merchant has ≥10 receipts
4. **No Word Length Filter**: Considers all words, including short ones like "$0.00"
5. **Conservative**: Skips words with insufficient evidence

## Decision Logic

| Confidence | Matches | Candidates | Action |
|------------|---------|------------|--------|
| ≥0.75 | ≥5 | 1 | Suggest directly (NO LLM) |
| ≥0.60 | ≥3 | 1 | Suggest directly (NO LLM) |
| ≥0.50 | ≥2 | Multiple | Use LLM to choose |
| ≥0.40 | ≥2 | 1 | Use LLM for context |
| <0.40 | Any | Any | Skip |

## Integration

The suggested labels are created with `validation_status="PENDING"` and will be:
1. Validated by the Label Validation Agent
2. Harmonized by the Label Harmonizer
3. Refined based on merchant patterns and consistency

