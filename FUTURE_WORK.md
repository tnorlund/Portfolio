# Future Work & Enhancement Opportunities

## Metadata Harmonizer Enhancement

**Current approach:** LLM + similarity search (accurate, slow, expensive)

**Opportunity:**
For new receipts → skip harmonizer (Google data is clean via ReceiptPlace)
For old receipts → keep current harmonizer (cleans ReceiptMetadata)

**Benefit:** Reduce harmonizer calls for new receipts by ~100% (they come pre-cleaned from Google)

**Not a code change:** Just operational logic in receipt_agent
