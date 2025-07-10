# Noise Word Implementation for receipt_dynamo

## Overview
This document outlines the implementation of noise word handling in the receipt_dynamo package to support the efficient labeling strategy in receipt_label.

## Entity Changes

### 1. ReceiptWord Entity Update

Add a new field to track noise words:

```python
@dataclass(eq=True, unsafe_hash=False)
class ReceiptWord(DynamoDBEntity):
    # ... existing fields ...
    is_noise: bool = False  # New field to mark noise words
```

### 2. DynamoDB Item Mapping

Update `to_item()` method to include is_noise:

```python
def to_item(self) -> Dict[str, Any]:
    return {
        # ... existing fields ...
        "is_noise": {"BOOL": self.is_noise},
    }
```

Update `item_to_receipt_word()` to handle the new field:

```python
def item_to_receipt_word(item: Dict[str, Any]) -> ReceiptWord:
    return ReceiptWord(
        # ... existing fields ...
        is_noise=item.get("is_noise", {}).get("BOOL", False),
    )
```

## Storage Strategy

### DynamoDB Behavior
- **Store all words**: Including noise words for complete OCR preservation
- **Mark noise words**: Set `is_noise=True` during initial processing
- **No labels for noise**: Noise words should never have associated ReceiptWordLabel entities
- **Query filtering**: Can filter out noise words when needed using the is_noise flag

### Query Patterns

1. **Get all words** (including noise):
   ```python
   # Standard query - returns everything
   words = client.query_receipt_words(receipt_id)
   ```

2. **Get only meaningful words**:
   ```python
   # Filter in application layer
   meaningful_words = [w for w in words if not w.is_noise]
   ```

3. **Count noise vs meaningful**:
   ```python
   noise_count = sum(1 for w in words if w.is_noise)
   meaningful_count = sum(1 for w in words if not w.is_noise)
   ```

## Integration with receipt_label

The receipt_label package will:
1. Identify noise words during processing
2. Set `is_noise=True` when storing to DynamoDB
3. Skip noise words when:
   - Creating embeddings for Pinecone
   - Applying labels
   - Calling GPT for labeling

## Benefits

1. **Complete data preservation**: All OCR output retained
2. **Efficient processing**: Skip noise during expensive operations
3. **Audit trail**: Can see what was filtered and why
4. **Flexibility**: Can adjust noise detection without data loss
5. **Analytics**: Can measure noise levels across receipts

## Migration Strategy

For existing data:
1. Add `is_noise` with default `False` to existing records
2. Run noise detection on historical data if needed
3. Update incrementally as receipts are reprocessed

## Testing

Add tests for:
- Entity serialization/deserialization with is_noise field
- Backward compatibility (missing is_noise defaults to False)
- Query filtering based on noise status
