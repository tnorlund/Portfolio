# Pinecone-Enhanced Structure Analysis

## Problem Statement

The current receipt structure analysis implementation suffers from non-deterministic outputs due to:
- GPT returning different section categories for similar content
- Limited to 5 fixed categories (`business_info`, `transaction_details`, `items`, `payment`, `footer`)
- No merchant context awareness (treats all receipts the same)
- No learning from previously analyzed receipts
- Each receipt is analyzed in isolation

## Core Insight: Same Merchant = Same Structure

Receipts from the same merchant (McDonald's, Home Depot, etc.) follow consistent structural patterns. Instead of asking GPT to analyze each receipt from scratch, we should:
1. Find previous receipts from the same merchant
2. Use their structure as a template
3. Let MCP or other ML models learn patterns from line embeddings

## Simplified Architecture

```mermaid
flowchart TD
    Start([New Receipt<br/>from McDonald's]) --> FindSame[Query Pinecone:<br/>"Find all McDonald's receipts"]

    FindSame --> Found{Found Same<br/>Merchant?}

    Found -->|Yes| GetStructures[Retrieve Their<br/>Structure Patterns]
    Found -->|No| FirstTime[First Receipt<br/>from this Merchant]

    GetStructures --> ApplyPattern[Apply Known<br/>Structure]
    FirstTime --> GPTAnalyze[GPT Analysis<br/>One Time Only]

    ApplyPattern --> Done[Structure Applied]
    GPTAnalyze --> SavePattern[Save New Pattern<br/>for Future Use]
    SavePattern --> Done

    style FindSame fill:#e1e5f5
    style GetStructures fill:#e1f5e1
    style ApplyPattern fill:#e1f5e1
```

## Key Questions

### 1. Finding Receipts from Same Merchant

```python
def find_same_merchant_receipts(merchant_metadata):
    """
    Simple: Find all receipts from the same place_id
    """
    return pinecone.query(
        namespace="receipt_structures",
        filter={"place_id": merchant_metadata.place_id},
        include_metadata=True
    )
```

### 2. Using Line Embeddings for Structure Detection

Instead of complex pattern matching, we can leverage the existing line embeddings:

```python
def get_receipt_line_embeddings(receipt_id):
    """
    Get all line embeddings for a receipt, already stored in Pinecone
    """
    return pinecone.query(
        namespace="receipt_lines",
        filter={"receipt_id": receipt_id},
        include_metadata=True
    )
```

### 3. MCP as the ML Model for Structure Learning

MCP (Model Context Protocol) or similar ML models can learn from the line embeddings to identify structural patterns:

```python
# Example: Using line embeddings to identify sections
line_embeddings = get_receipt_line_embeddings(receipt_id)

# Group similar lines based on embedding similarity
# Lines with similar embeddings likely belong to same section
sections = cluster_lines_by_embedding_similarity(line_embeddings)

# Learn section boundaries from historical data
section_classifier = train_section_classifier(
    training_data=previous_receipts_with_labels,
    features=line_embeddings
)
```

## Practical Implementation

### Step 1: Check if Merchant Exists

```python
def process_receipt_structure(receipt, merchant_metadata):
    # Simple lookup - do we have this merchant?
    existing = find_same_merchant_receipts(merchant_metadata)

    if existing:
        # Use the most common structure from previous receipts
        structure = get_most_common_structure(existing)
        return apply_structure(receipt, structure)
    else:
        # First time seeing this merchant - use GPT once
        structure = gpt_analyze_structure(receipt, merchant_metadata)
        save_structure_for_merchant(merchant_metadata, structure)
        return structure
```

### Step 2: Structure Storage (Simple)

```python
{
    "merchant_name": "McDonald's",
    "place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
    "sections": [
        {"name": "header", "lines": [0, 1, 2, 3]},
        {"name": "order_info", "lines": [4, 5, 6]},
        {"name": "items", "lines": [7, 8, 9, 10, 11, 12]},
        {"name": "totals", "lines": [13, 14, 15]},
        {"name": "payment", "lines": [16, 17, 18]}
    ]
}
```

### Step 3: Using Line Embeddings for ML

The key insight is that line embeddings already contain rich information:
- Semantic content (what the line says)
- Positional context (where it appears)
- Visual patterns (how it's formatted)

ML models can learn to classify lines into sections based on these embeddings:

```python
# Training data: Line embeddings + their section labels
training_data = [
    (line_embedding_1, "header"),
    (line_embedding_2, "header"),
    (line_embedding_3, "items"),
    (line_embedding_4, "items"),
    (line_embedding_5, "totals"),
    ...
]

# Simple classifier (could be MCP, sklearn, etc.)
section_classifier = train_classifier(training_data)

# For new receipts, classify each line
new_receipt_lines = get_line_embeddings(new_receipt)
predicted_sections = [
    section_classifier.predict(line_embedding)
    for line_embedding in new_receipt_lines
]
```

## Benefits of This Approach

1. **Truly Deterministic**: Same merchant always gets same structure
2. **No Complex Scoring**: Either we've seen the merchant or we haven't
3. **Leverages Existing Data**: Uses line embeddings we're already creating
4. **ML-Ready**: Line embeddings are perfect features for ML models
5. **Simple Implementation**: No complex pattern matching or validation

## What About Edge Cases?

**Q: What if a merchant changes their receipt format?**
A: The ML model trained on line embeddings can detect anomalies. If the new format is too different, it triggers a re-analysis.

**Q: What about chain stores with different formats?**
A: Use place_id for exact matching. McDonald's #1234 might have different format than McDonald's #5678.

**Q: How does MCP fit in?**
A: MCP can be the ML model that:
- Learns section boundaries from line embeddings
- Detects when a receipt doesn't match expected structure
- Suggests section classifications for new merchants

## Next Steps

1. **Implement Simple Merchant Lookup**: Just check if we've seen this place_id before
2. **Store Basic Structure**: Save which lines belong to which sections
3. **Train Line Classifier**: Use existing line embeddings to train section classifier
4. **Let ML Handle Complexity**: Don't hand-code patterns; let models learn from data
