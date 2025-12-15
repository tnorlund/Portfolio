# Word Embedding Format Update

## Summary

Updated word embeddings to use a simple context-only format that supports:
- **On-the-fly embedding**: Can embed words without full receipt context
- **Configurable context size**: Default 2 words on each side (configurable)
- **Multiple `<EDGE>` tags**: One per missing word position (preserves relative position)
- **No position tags**: Removed `<POS>` tags as context provides spatial information

## New Format

### Format Structure
```
left_words... word right_words...
```

Where:
- `left_words`: Up to `context_size` words to the left (padded with `<EDGE>` if missing)
- `word`: The target word
- `right_words`: Up to `context_size` words to the right (padded with `<EDGE>` if missing)

### Examples (context_size=2)

| Situation | Result |
|-----------|--------|
| At very left edge | `<EDGE> <EDGE> Total Tax Discount` |
| 1 word from left | `<EDGE> Subtotal Total Tax Discount` |
| 2+ words from left | `Items Subtotal Total Tax Discount` |
| At very right edge | `Items Subtotal Total <EDGE> <EDGE>` |
| Both edges | `<EDGE> <EDGE> Total <EDGE> <EDGE>` |

## Key Changes

### 1. Format Function
- **Old**: `<TARGET>word</TARGET> <POS>position</POS> <CONTEXT>left right</CONTEXT>`
- **New**: `left_words word right_words` (simple space-separated)

### 2. Context Size
- **Old**: Fixed 1 word on each side
- **New**: Configurable (default: 2 words on each side)

### 3. Edge Handling
- **Old**: Single `<EDGE>` tag
- **New**: Multiple `<EDGE>` tags (one per missing position)

### 4. Position Tags
- **Old**: Included `<POS>top-left</POS>` etc.
- **New**: Removed (context provides spatial information)

### 5. Line Filtering
- **Old**: Only found neighbors on the same line (vertical span overlap check)
- **New**: Finds neighbors based on horizontal position (x-coordinate) regardless of line

## Benefits

1. **On-the-fly embedding**: Can embed any word with just:
   - Word text
   - Left neighbors (or `<EDGE>`)
   - Right neighbors (or `<EDGE>`)

2. **Better edge distinction**: Multiple `<EDGE>` tags preserve relative position
   - `<EDGE> <EDGE> word` = word at very left edge
   - `<EDGE> word1 word` = word 1 position from left
   - `word1 word2 word` = word 2+ positions from left

3. **More context**: 2 words on each side captures phrases better
   - "Grand Total" vs "Subtotal Total" vs "Total Items"

4. **Simpler format**: Natural language order, easier to construct

5. **Cross-line context**: Finds words horizontally regardless of line
   - Useful for receipts with multi-line items or vertically aligned columns
   - Captures context from words that are far left/right on other lines

## Implementation Details

### Core Functions

1. **`_get_word_neighbors()`**: Returns lists of left/right words (up to context_size)
   - Finds words based on horizontal position (x-coordinate) regardless of line
   - Sorts all words by x-coordinate and selects nearest neighbors
2. **`_format_word_context_embedding_input()`**: Formats word with context
3. **`format_word_for_embedding()`**: Helper for on-the-fly embedding (just text + neighbors)

### Updated Files

- `receipt_label/receipt_label/embedding/word/realtime.py`
- `receipt_label/receipt_label/embedding/word/submit.py`
- `receipt_chroma/receipt_chroma/embedding/formatting/word_format.py`
- `receipt_label/receipt_label/embedding/word/poll.py`
- `receipt_chroma/receipt_chroma/embedding/delta/word_delta.py`

### Metadata Updates

Metadata now includes:
- `left`: First left word (backward compatibility)
- `right`: First right word (backward compatibility)
- `left_context`: Full left context as space-separated string
- `right_context`: Full right context as space-separated string

## Usage

### For On-the-Fly Embedding

```python
from receipt_label.embedding.word.realtime import format_word_for_embedding

# Embed a word with context
formatted = format_word_for_embedding(
    word_text="Total",
    left_words=["Subtotal"],  # or ["<EDGE>"] if at edge
    right_words=["Tax", "Discount"],
    context_size=2
)
# Result: "<EDGE> Subtotal Total Tax Discount"

# Then embed it
embedding = embed_fn([formatted])[0]
```

### For Batch Embedding

```python
from receipt_label.embedding.word.realtime import embed_words_realtime

# Automatically uses context_size=2 (default)
embeddings = embed_words_realtime(words, merchant_name="Store Name")
```

## Migration Notes

- **Existing embeddings**: Will need to be regenerated with new format
- **Query compatibility**: Old queries using stored embeddings will still work
- **New queries**: Can use on-the-fly embedding with `format_word_for_embedding()`

## Testing

Updated tests in `receipt_chroma/tests/unit/test_word_format.py` to match new format.
