# Label Suggestion Agent - Test Script

## Status

✅ **Pulumi Integration**: Working - successfully loads table name and API keys
✅ **Receipt Selection**: Working - can list and pick random receipts
⚠️ **ChromaDB Import**: Python 3.14 compatibility issue with pydantic v1 (dependency issue)

## What the Script Does

1. **Loads Environment from Pulumi**
   - DynamoDB table name
   - OpenAI API key
   - Ollama API key (if using LLM)
   - LangSmith API key (for tracing)

2. **Lists Receipts**
   - Queries DynamoDB for receipts
   - Picks a random receipt

3. **Runs Label Suggestion Agent**
   - Finds unlabeled words
   - Uses ChromaDB to find similar words with VALID labels
   - Suggests labels with confidence scores
   - Creates PENDING labels (or dry-run mode)

## Example Output (When Working)

```
🔧 Loading environment from Pulumi...
📊 DynamoDB Table: ReceiptsTable-dc5be22
✅ OpenAI API key loaded
✅ Ollama API key loaded
📋 Listing receipts from ReceiptsTable-dc5be22...
✅ Found 50 receipts

🎲 Selected random receipt:
   Image ID: b800a56a-ee4b-4315-a241-48ccf5e6237c
   Receipt ID: 2

🚀 Running label suggestion agent (dry-run)...
================================================================================
Testing label suggestion agent for receipt 2
Image ID: b800a56a-ee4b-4315-a241-48ccf5e6237c
Table: ReceiptsTable-dc5be22
Use LLM: False
Dry run: True

Creating DynamoDB client...
Loading settings...
Creating ChromaDB client...
Creating embedding function...
Running label suggestion workflow...

================================================================================
LABEL SUGGESTION RESULTS
================================================================================
Status: success
Unlabeled words: 15
Suggestions made: 12
LLM calls: 0

Created labels:
  - Word 5 (line 3): GRAND_TOTAL (confidence: 0.92)
  - Word 8 (line 3): DATE (confidence: 0.88)
  - Word 12 (line 1): MERCHANT_NAME (confidence: 0.75)
  ... (9 more)
================================================================================
```

## Usage

```bash
# Test with a specific receipt
python scripts/test_label_suggestion_agent.py <image_id> <receipt_id> --dry-run

# Pick a random receipt and test
python scripts/pick_and_test_receipt.py

# With LLM for ambiguous cases
python scripts/test_label_suggestion_agent.py <image_id> <receipt_id> --use-llm --dry-run
```

## Known Issues

1. **Python 3.14 + ChromaDB**: ChromaDB uses pydantic v1 which has compatibility issues with Python 3.14
   - **Workaround**: Use Python 3.12 or 3.13
   - **Alternative**: Wait for ChromaDB to update to pydantic v2

2. **ChromaDB Snapshots**: Need to have ChromaDB snapshots available
   - Default location: `.chroma_snapshots/lines` and `.chroma_snapshots/words`
   - Or set `--chroma-path` to point to snapshot directory

## What It Would Do (If ChromaDB Import Works)

For a receipt with 15 unlabeled words:

1. **High Confidence Words (10 words)**
   - Finds similar words in ChromaDB with VALID labels
   - Confidence ≥ 0.75, ≥5 matches
   - Suggests directly (NO LLM call)

2. **Medium Confidence Words (2 words)**
   - Finds similar words with VALID labels
   - Confidence ≥ 0.60, ≥3 matches
   - Suggests directly (NO LLM call)

3. **Ambiguous Words (2 words)** - if `--use-llm` is set
   - Multiple candidates or low confidence
   - Uses LLM to choose between candidates
   - 2 LLM calls

4. **Skipped Words (1 word)**
   - Insufficient evidence in ChromaDB
   - No suggestion made

**Result**: 12 labels suggested, 0-2 LLM calls (vs 15 if using LLM for all)

