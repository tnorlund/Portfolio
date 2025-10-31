# Simplified LLM-Based Metadata Validation

## Problem with Current Approach

The current validation uses complex similarity scoring with fuzzy matching:
- Compares merchant names character-by-character
- Compares phone numbers
- Compares addresses
- Has many edge cases and hardcoded thresholds

This is brittle and complex.

## Proposed Solution

Use the LLM to make a simple decision:
- **Input**: ReceiptMetadata + extracted merchant name from receipt
- **Output**: `is_valid` boolean + recommended correction if invalid
- **LLM decides**: Does the metadata match the receipt?

## Benefits

1. **Simpler**: One LLM call, one boolean decision
2. **More reliable**: LLM understands context and variations
3. **Fewer edge cases**: LLM handles typos, abbreviations, variations
4. **More maintainable**: Less code, fewer bugs

## Implementation

### New Model
```python
class MetadataValidationResponse(BaseModel):
    is_valid: bool
    reasoning: str
    recommended_merchant_name: str  # Empty if valid
```

### New Node
Create `phase1_validate_llm.py`:
- Takes ReceiptMetadata and transaction labels
- Extracts merchant name from receipt text
- Calls LLM with structured output
- Returns simple boolean decision

### Usage
Update graph to use new LLM-based validation node instead of complex similarity scoring.

## Example

**ReceiptMetadata**: "Martin Tax"
**Extracted from receipt**: "IN-N-OUT WESTL.. - VILLAGE"

**LLM Response**:
```json
{
  "is_valid": false,
  "reasoning": "ReceiptMetadata says 'Martin Tax' but receipt clearly shows 'IN-N-OUT'. These are completely different merchants.",
  "recommended_merchant_name": "IN-N-OUT WESTL.. - VILLAGE"
}
```

**Action**: Update ReceiptMetadata to "IN-N-OUT WESTL.. - VILLAGE"

## Next Steps

1. Add new model to `models/metadata_validation.py` ✅
2. Create new validation node ✅
3. Update graph to use LLM validation
4. Test with the IN-N-OUT / Martin Tax case
5. Remove old similarity-based validation code

