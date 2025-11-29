# Pull Request

## 📋 Description

This PR updates the word embedding format to use a simple context-only approach that supports on-the-fly embedding for agents. The new format removes XML-style position tags, uses multiple `<EDGE>` tags for better edge detection, and enables configurable context size (default: 2 words on each side).

### Key Changes

1. **New Simple Format**: `left_words... word right_words...` (replaces XML-style `<TARGET>`, `<POS>`, `<CONTEXT>` tags)
2. **Configurable Context Size**: Default 2 words on each side (was fixed at 1)
3. **Multiple `<EDGE>` Tags**: One per missing position (preserves relative position information)
4. **Cross-Line Context**: Finds words horizontally regardless of line, filtered by same horizontal space (centroid y-check)
5. **On-the-Fly Embedding**: New helper function and agent tools for embedding without ChromaDB storage

### Format Comparison

**Before:**
```
<TARGET>Total</TARGET> <POS>bottom-center</POS> <CONTEXT>Subtotal Tax</CONTEXT>
```

**After:**
```
<EDGE> Subtotal Total Tax Discount
```

## 🔄 Type of Change

- [x] ✨ New feature (non-breaking change which adds functionality)
- [x] 🔧 Refactoring (improves embedding format and adds on-the-fly capability)
- [x] 📚 Documentation update

## 🎯 Motivation

The previous format used XML-style tags (`<TARGET>`, `<POS>`, `<CONTEXT>`) which made on-the-fly embedding difficult. The new format:
- Enables agents to embed words without full receipt context
- Provides better edge detection with multiple `<EDGE>` tags
- Captures more context (2 words vs 1) for better phrase matching
- Simplifies the format for easier construction

## 📝 Changes Made

### Core Formatting Functions
- **`_format_word_context_embedding_input()`**: Updated to simple format
- **`_get_word_neighbors()`**: Now finds words horizontally with centroid y-check for same horizontal space
- **`format_word_for_embedding()`**: New helper for on-the-fly embedding

### Updated Files
- `receipt_label/receipt_label/embedding/word/realtime.py`
- `receipt_label/receipt_label/embedding/word/submit.py`
- `receipt_chroma/receipt_chroma/embedding/formatting/word_format.py`
- `receipt_label/receipt_label/embedding/word/poll.py`
- `receipt_chroma/receipt_chroma/embedding/delta/word_delta.py`

### Tests
- Updated `receipt_chroma/tests/unit/test_word_format.py` to match new format
- Added tests for cross-line neighbor detection
- Added tests for multiple context words

### Documentation
- `docs/WORD_EMBEDDING_FORMAT_UPDATE.md`: Comprehensive format documentation
- `docs/AGENT_ON_THE_FLY_EMBEDDING.md`: Agent usage guide

### Agent Tools
- `receipt_agent/receipt_agent/tools/on_the_fly_embedding_tools.py`: New tools for agents

## 🧪 Testing

- [x] Tests pass locally
- [x] Added tests for new functionality
- [x] Updated existing tests if needed
- [x] Manual testing completed

### Test Coverage
- Format with single word (edge case)
- Format with multiple neighbors
- Format with multiple context words (context_size=2)
- Neighbor detection across different lines
- Neighbor detection with same horizontal space check
- Parse function for new format

## 📚 Documentation

- [x] Documentation updated
- [x] Comments added for complex logic
- [x] README updated if needed

## 🔍 Technical Details

### Neighbor Detection Logic

The new implementation:
1. Sorts all words by x-coordinate (horizontal position)
2. Filters candidates by checking if their centroid y is within target's vertical span (between `bottom_left["y"]` and `top_left["y"]`)
3. Collects up to `context_size` words on each side

This allows finding words horizontally regardless of line, while still filtering to words on roughly the same horizontal space.

### Edge Handling

Multiple `<EDGE>` tags preserve relative position:
- `<EDGE> <EDGE> word` = word at very left edge
- `<EDGE> word1 word` = word 1 position from left
- `word1 word2 word` = word 2+ positions from left

## 🚀 Usage Examples

### On-the-Fly Embedding

```python
from receipt_label.embedding.word.realtime import format_word_for_embedding

formatted = format_word_for_embedding(
    word_text="Total",
    left_words=["Subtotal"],
    right_words=["Tax", "Discount"],
    context_size=2
)
# Result: "<EDGE> Subtotal Total Tax Discount"
```

### Agent Tool Usage

```python
from receipt_agent.tools.on_the_fly_embedding_tools import create_on_the_fly_embedding_tools

tools, state = create_on_the_fly_embedding_tools(chroma_client)

# Embed and search in one step
similar_words = search_with_on_the_fly_embedding(
    word_text="Total",
    left_words=["Subtotal"],
    right_words=["Tax"],
    n_results=10
)
```

## ⚠️ Breaking Changes

**Note**: This is a breaking change for existing embeddings. All word embeddings will need to be regenerated with the new format. However:
- The embedding API remains the same
- Query compatibility: Old queries using stored embeddings will still work
- New embeddings will use the new format
- Gradual migration: Embeddings will be updated as receipts are reprocessed

## 🤖 AI Review Status

### Cursor Bot Review

- [x] Waiting for Cursor bot analysis
- [ ] Cursor findings addressed
- [ ] No critical issues found

### AI Review (Cursor)

- [ ] Cursor bot findings addressed (if any)

## ✅ Checklist

- [x] My code follows this project's style guidelines
- [x] I have performed a self-review of my code
- [x] My changes generate no new warnings
- [x] I have added tests that prove my fix is effective or that my feature works
- [x] New and existing unit tests pass locally with my changes
- [x] Any dependent changes have been merged and published

## 🚀 Deployment Notes

- No special deployment instructions required
- Existing embeddings in ChromaDB will continue to work for queries
- New embeddings will automatically use the new format
- Consider running a batch re-embedding job for consistency (optional, not required)

## 📷 Examples

### Edge Cases

- At very left: `<EDGE> <EDGE> Total Tax Discount`
- At very right: `Items Subtotal Total <EDGE> <EDGE>`
- Both edges: `<EDGE> <EDGE> Total <EDGE> <EDGE>`
- 1 word from left: `<EDGE> Subtotal Total Tax Discount`
- 2+ words from left: `Items Subtotal Total Tax Discount`

---

**Note**: This PR enables on-the-fly embedding for agents while maintaining backward compatibility for queries. Existing embeddings will continue to work, and new embeddings will use the improved format.
