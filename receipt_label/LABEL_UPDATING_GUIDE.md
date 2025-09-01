# Receipt Label Updating Guide

This guide shows how to use the PydanticOutputParser results to update receipt word labels in DynamoDB.

## Overview

The label updating system bridges LLM analysis results with the actual receipt word data stored in DynamoDB:

1. **LLM Analysis** → Produces `CurrencyLabel` objects with classifications
2. **Word Matching** → Finds the specific `ReceiptWord` entities that match the currency text
3. **Label Management** → Checks existing `ReceiptWordLabel` entities for conflicts
4. **Database Updates** → Adds/updates labels with proper conflict resolution

## Key Components

### 1. `ReceiptLabelUpdater` Class

Located in `receipt_label/label_updater.py`, this handles the core logic:

- **Word Matching**: Matches LLM currency text to actual receipt words
- **Conflict Detection**: Checks for existing labels that might conflict
- **Label Updates**: Adds new labels or updates conflicting ones
- **Dry Run Mode**: Shows what would be done without making changes

### 2. Integration with `costco_analyzer.py`

The analyzer now supports label updating:

```python
result = await analyze_costco_receipt(
    client=client,
    image_id="uuid-here",
    receipt_id=1,
    update_labels=True,  # Enable label updating
    dry_run=False        # Actually apply changes (True = show only)
)
```

## Usage Examples

### Basic Analysis (No Label Updates)

```python
from receipt_label.costco_analyzer import analyze_costco_receipt

# Just analyze, don't update labels
result = await analyze_costco_receipt(client, image_id, receipt_id)
print(f"Found {len(result.discovered_labels)} labels")
```

### Dry Run (Show What Would Be Updated)

```python
# See what labels would be updated without making changes
result = await analyze_costco_receipt(
    client, image_id, receipt_id,
    update_labels=True,
    dry_run=True
)
```

### Full Update (Actually Apply Labels)

```python
# Apply labels to DynamoDB
result = await analyze_costco_receipt(
    client, image_id, receipt_id,
    update_labels=True,
    dry_run=False
)
```

### Direct Label Updater Usage

```python
from receipt_label.label_updater import ReceiptLabelUpdater

updater = ReceiptLabelUpdater(client)

# Apply currency labels from LLM analysis
update_results = await updater.apply_currency_labels(
    image_id="uuid-here",
    receipt_id=1,
    currency_labels=discovered_labels,  # From LLM analysis
    dry_run=False
)

# Show results
from receipt_label.label_updater import display_label_update_results
display_label_update_results(update_results)
```

## Data Flow

### 1. LLM Analysis Results

From the PydanticOutputParser, we get `CurrencyLabel` objects:

```python
@dataclass
class CurrencyLabel:
    word_text: str      # "$198.93" 
    label_type: str     # "GRAND_TOTAL"
    line_ids: [56]      # OCR line IDs where this text appears
    confidence: float   # 0.99
    reasoning: str      # LLM explanation
    value: float        # 198.93
```

### 2. Word Matching Process

The system finds the actual `ReceiptWord` entities:

1. **Get candidate words** from the `line_ids` using `client.list_receipt_words_from_line()`
2. **Match currency text** to specific words using fuzzy matching algorithms
3. **Return best match** with confidence score

### 3. Label Conflict Resolution

For each matched word, check existing labels using `client.list_receipt_word_labels_for_word()`:

- **No existing labels** → Add new label
- **Same label exists** → Skip (no change needed)
- **Different currency label exists** → Update (replace conflicting label)
- **Non-currency labels exist** → Add alongside existing

### 4. Database Updates

Create `ReceiptWordLabel` entities and apply to DynamoDB:

```python
new_label = ReceiptWordLabel(
    image_id=image_id,
    receipt_id=receipt_id,
    line_id=word.line_id,
    word_id=word.word_id,
    label="GRAND_TOTAL",
    reasoning="LLM Classification: Found at bottom of receipt (confidence: 0.99)",
    timestamp_added=datetime.now(),
    validation_status="VERIFIED",  # or "NEEDS_REVIEW" if confidence < 0.95
    label_proposed_by="costco_analyzer_llm"
)
```

## Word Matching Algorithm

The matching algorithm handles various text formats:

### Exact Matching (Highest Priority)
- `"$198.93"` matches `"$198.93"` exactly
- `"198.93"` matches `"198.93"` exactly (normalized)

### Value Matching
- `"$198.93"` matches `"198.93"` (numeric value comparison)
- Tolerates small differences (< 1 cent)

### Fuzzy Matching  
- Handles OCR errors and variations
- Minimum 70% similarity required
- Prefers longer matches over shorter ones

## Conflict Resolution Strategy

When the same word already has labels:

| Existing Label | New Label | Action |
|---------------|-----------|---------|
| None | GRAND_TOTAL | **Add** |
| GRAND_TOTAL | GRAND_TOTAL | **Skip** (already labeled) |
| GRAND_TOTAL | TAX | **Update** (replace GRAND_TOTAL with TAX) |
| PRODUCT_NAME | GRAND_TOTAL | **Add** (different category) |

Currency labels (GRAND_TOTAL, TAX, LINE_TOTAL, SUBTOTAL) are mutually exclusive per word, but can coexist with other label types.

## Error Handling

The system handles various error conditions:

- **Word not found**: Logs warning, continues with other labels
- **Multiple matches**: Chooses highest confidence match
- **Database errors**: Logs error, shows in results summary
- **Validation failures**: Marks labels as "NEEDS_REVIEW"

## Performance Considerations

- **Batching**: Processes multiple labels per receipt efficiently
- **Caching**: Reuses word lookups when possible  
- **Async**: All database operations are async
- **Logging**: Comprehensive logging for debugging

## Demo Scripts

### Quick Test
```bash
python receipt_label/demo_label_updating.py
```

### Full Integration Test
```bash
OLLAMA_API_KEY=xxx LANGCHAIN_API_KEY=xxx python receipt_label/receipt_label/costco_analyzer.py
```

## Monitoring and Validation

### Label Update Results

The system provides detailed feedback:

```
📝 LABEL UPDATE RESULTS
================================================================================
Summary:
  added: 18
  skipped: 3
  updated: 2
  
Total labels processed: 23

Label 1: GRAND_TOTAL - $198.93
  Word: '198.93' (line 56, word 2)  
  Action: added
  
Label 2: TAX - $0.00
  Word: '0.00' (line 11, word 3)
  Action: skipped
  Reason: Label already exists with same type
```

### Validation Status

Labels are automatically assigned validation status:
- **VERIFIED**: High confidence (≥ 0.95)
- **NEEDS_REVIEW**: Lower confidence (< 0.95)

### LangSmith Tracing

All LLM interactions are traced in LangSmith for monitoring and debugging.

## Best Practices

1. **Always run dry_run first** to see what would change
2. **Monitor confidence scores** - low confidence labels need review
3. **Check validation results** - arithmetic relationships should be consistent
4. **Use specific receipt IDs** for testing rather than batch processing
5. **Review conflict resolution** - ensure updates make sense

This system provides a robust bridge between LLM analysis and the actual receipt data stored in DynamoDB, with comprehensive error handling and conflict resolution.