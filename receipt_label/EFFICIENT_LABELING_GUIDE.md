# Efficient Receipt Word Labeling Guide

## Overview
<!-- Brief description of the efficient labeling approach and its benefits -->

## Architecture
### System Components
<!-- List of key components and their responsibilities -->

### Data Flow
<!-- High-level flow from receipt input to labeled output -->

## Prerequisites
### 1. Merchant Metadata
<!-- Requirements for ReceiptMetadata entity -->
<!-- How to handle missing metadata -->

### 2. Pinecone Embeddings
<!-- Requirement for embedded lines and words -->
<!-- Embedding schema and metadata structure -->

### 3. CORE_LABELS Definition
<!-- Reference to constants.py -->
<!-- List of 18 core labels -->

## Implementation Steps

### Step 1: Metadata Retrieval
<!-- Query ReceiptMetadata from DynamoDB -->
<!-- Extract merchant_name and category -->
<!-- Handle missing metadata scenarios -->

### Step 2: Embedding Verification & Noise Detection
#### Check Embeddings
<!-- Check if lines/words are embedded -->
<!-- Embed if missing -->
<!-- Metadata requirements -->

#### Identify Noise Words
- Mark noise words with `is_noise=True` in DynamoDB
- Skip noise words during Pinecone embedding
- Noise detection happens once during initial processing

**Storage Strategy**:
- DynamoDB: Store ALL words (including noise)
- Pinecone: Embed only meaningful words
- Benefits: Complete OCR preservation + efficient search

### Step 3: Parallel Pattern Detection
#### 3.1 Currency Pattern Detection
<!-- Regex patterns for currency -->
<!-- Examples of currency formats -->

#### 3.2 DateTime Pattern Detection
<!-- Common date/time formats -->
<!-- Pattern matching approach -->

#### 3.3 Contact Pattern Detection
<!-- Phone, email, website patterns -->
<!-- Validation rules -->

#### 3.4 Quantity Pattern Detection
<!-- Quantity indicators (@ , x, Qty:) -->
<!-- Association with prices -->

#### 3.5 Merchant Pattern Query
**Purpose**: Retrieve proven labeling patterns from the same merchant in a single query

**Query Strategy**:
- Query Pinecone for words with `validated_labels` from the same `merchant_name`
- Use metadata-only query (dummy vector) for efficiency
- Retrieve up to 1000 validated words to build pattern dictionary

**Pattern Extraction**:
- Group results by word text (case-insensitive)
- Track label frequency for each word
- Build confidence scores based on occurrence count
- Return dictionary: `{word_text: {label: confidence}}`

**Key Benefits**:
- ONE query instead of N queries (99% reduction)
- Leverages historical labeling data
- Merchant-specific patterns (e.g., "Big Mac" at McDonald's)
- Self-improving system (patterns get better over time)

### Step 4: Currency Classification Rules
#### Position-Based Rules
<!-- Bottom 20% = likely totals -->
<!-- Middle section = likely line items -->

#### Keyword-Based Rules
<!-- Mapping keywords to labels -->
<!-- Priority of rules -->

#### Context-Based Rules
<!-- Quantity patterns nearby -->
<!-- Line structure analysis -->

### Step 5: Apply Merchant Patterns
<!-- Using patterns from Pinecone query -->
<!-- Confidence scoring -->
<!-- Pattern precedence -->

### Step 6: Smart GPT Decision & Batch Labeling

#### 6.1 Determine if GPT is Needed
**Essential Labels Check**:
```
ESSENTIAL_LABELS = {
    "MERCHANT_NAME",    # Must have merchant
    "DATE",             # Must have transaction date
    "GRAND_TOTAL",      # Must have final amount
    "PRODUCT_NAME"      # Must have at least one item
}
```

**Decision Criteria**:
1. **Skip GPT if all essential labels found** - Receipt is sufficiently labeled
2. **Skip GPT if only noise words remain** - Words like punctuation, random characters
3. **Skip GPT if unlabeled words < threshold** - e.g., less than 5 meaningful words
4. **Call GPT if missing essential labels** - Need to find critical information

**Noise Word Detection**:
- Single characters (except currency symbols)
- Pure punctuation
- Common separators (---, ===, ...)
- Receipt artifacts (torn edges, scan artifacts)

#### 6.2 Grouping Strategy
<!-- Group by line for context -->
<!-- Batch size optimization -->

#### 6.3 Prompt Engineering
<!-- Include merchant context -->
<!-- Provide labeled examples -->
<!-- Constrain to CORE_LABELS -->

#### 6.4 Response Processing
<!-- Parse structured output -->
<!-- Validate against CORE_LABELS -->

### Step 7: Storage and Learning
#### Store Labels
<!-- Create ReceiptWordLabel entities -->
<!-- Batch write to DynamoDB -->

#### Update Pattern Cache
<!-- Extract successful patterns -->
<!-- Update Pinecone metadata -->
<!-- Confidence tracking -->

## Code Examples

### Smart GPT Decision Logic
```python
def should_call_gpt(words: List[ReceiptWord], labeled_words: List[ReceiptWord]) -> bool:
    """Determine if GPT is needed based on essential labels and noise filtering."""

    # Define essential labels
    ESSENTIAL_LABELS = {"MERCHANT_NAME", "DATE", "GRAND_TOTAL", "PRODUCT_NAME"}

    # Check if all essential labels are found
    found_labels = {word.label for word in labeled_words if word.label}
    missing_essentials = ESSENTIAL_LABELS - found_labels

    if missing_essentials:
        return True  # Must call GPT to find essential labels

    # Filter out noise words
    meaningful_unlabeled = []
    for word in words:
        if word.label:
            continue

        # Skip noise words
        if is_noise_word(word.text):
            continue

        meaningful_unlabeled.append(word)

    # Apply threshold
    return len(meaningful_unlabeled) >= 5

def is_noise_word(text: str) -> bool:
    """Detect noise words that don't need labeling."""
    # Single character (except currency symbols)
    if len(text) == 1 and text not in ['$', '€', '£', '¥']:
        return True

    # Pure punctuation or separators
    if text in ['.', ',', ':', '-', '---', '===', '***', '...']:
        return True

    # Only special characters
    if not any(c.isalnum() for c in text):
        return True

    return False
```

### Merchant Pattern Query
```python
async def query_merchant_patterns(
    merchant_name: str,
    client_manager: ClientManager
) -> Dict[str, str]:
    """Query Pinecone for validated patterns from the same merchant."""

    # Single query for all patterns
    results = await client_manager.pinecone.query(
        vector=[0] * 1536,  # Metadata-only query
        top_k=1000,
        include_metadata=True,
        filter={
            "merchant_name": merchant_name,
            "validated_labels": {"$exists": True}
        },
        namespace="words"
    )

    # Build pattern dictionary
    patterns = defaultdict(lambda: defaultdict(int))

    for match in results.matches:
        text = match.metadata.get("text", "").lower()
        labels = match.metadata.get("validated_labels", [])

        for label in labels:
            patterns[text][label] += 1

    # Return most common label for each word
    final_patterns = {}
    for text, label_counts in patterns.items():
        final_patterns[text] = max(label_counts, key=label_counts.get)

    return final_patterns
```

### Label Distribution Analysis
```python
def analyze_label_distribution(labeled_words: List[ReceiptWord]) -> Dict[str, int]:
    """Analyze which CORE_LABELS are present in the receipt."""

    label_counts = defaultdict(int)
    for word in labeled_words:
        if word.label:
            label_counts[word.label] += 1

    # Identify label categories present
    categories = {
        "merchant_info": any(label in label_counts for label in
                            ["MERCHANT_NAME", "ADDRESS_LINE", "PHONE_NUMBER"]),
        "transaction_info": any(label in label_counts for label in
                               ["DATE", "TIME", "PAYMENT_METHOD"]),
        "line_items": any(label in label_counts for label in
                         ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE", "LINE_TOTAL"]),
        "totals": any(label in label_counts for label in
                     ["SUBTOTAL", "TAX", "GRAND_TOTAL"])
    }

    return {
        "label_counts": dict(label_counts),
        "categories_present": categories,
        "total_labels": len(label_counts),
        "missing_from_corpus": 18 - len(label_counts)
    }
```

## Performance Metrics
### Cost Analysis
<!-- Comparison table: old vs new approach -->
<!-- API call reduction -->
<!-- Processing time improvement -->

### Accuracy Metrics
<!-- Pattern matching accuracy -->
<!-- GPT labeling accuracy -->
<!-- Overall system accuracy -->

## Best Practices
### Error Handling
<!-- Graceful degradation -->
<!-- Fallback strategies -->

### Monitoring
<!-- Key metrics to track -->
<!-- Alert thresholds -->

### Optimization Tips
<!-- Caching strategies -->
<!-- Batch size tuning -->
<!-- Pattern confidence thresholds -->

## Migration Guide
### From Per-Word to Efficient Labeling
<!-- Step-by-step migration -->
<!-- Backward compatibility -->
<!-- Testing approach -->

## Troubleshooting
### Common Issues
<!-- Missing merchant metadata -->
<!-- Low pattern confidence -->
<!-- GPT rate limits -->

### Debugging Tips
<!-- Logging recommendations -->
<!-- Test data sets -->
<!-- Validation tools -->

## Future Enhancements
### Planned Improvements
<!-- Multi-language support -->
<!-- Advanced pattern learning -->
<!-- Real-time pattern updates -->

## Appendices
### A. CORE_LABELS Reference
<!-- Complete list with descriptions -->

### B. Pattern Examples by Merchant Type
<!-- Restaurant patterns -->
<!-- Retail patterns -->
<!-- Service patterns -->

### C. API Reference
<!-- Function signatures -->
<!-- Parameter descriptions -->
<!-- Return values -->
