# Future Work & Enhancement Opportunities

## PlaceCluster Entity - Merchant Deduplication & Grouping

**Status:** Designed & implemented, but NOT YET INTEGRATED

**What it is:**
Entity for grouping multiple Google place_ids that refer to the same merchant (e.g., different IDs for the same Starbucks location, or Starbucks + Starbucks Coffee at same address).

**Why it was designed:**
- Potential future need to group receipts by canonical merchant
- Separate location data (ReceiptPlace) from merchant data (PlaceCluster)
- Support analytics across franchise locations

**Why we're deferring it:**
- Current system with ReceiptPlace + place_id already handles deduplication via Google's canonical IDs
- No immediate use case identified
- Adds complexity without clear ROI right now
- Data harmonizer (via LLM) already solves the "bad metadata" problem

**When to revisit:**
- If you need to group receipts by merchant (not by location)
- If Google's place_ids have significant duplicates that need consolidation
- If merchant-level analytics become a priority

**What exists:**
- `receipt_dynamo/entities/place_cluster.py` - Entity definition (ready to use)
- DynamoDB key structure designed (GSI1 for deduplication, GSI2 for place_id→cluster lookup)
- Methods: `add_place_id()`, `merge_from()`

**What would be needed to activate:**
1. Implement `_place_cluster.py` (data operations layer - follow `_receipt_place.py` pattern)
2. Implement clustering algorithm (detect when multiple place_ids = same merchant)
3. Backfill existing ReceiptPlace records into PlaceCluster
4. Update consumers to use PlaceCluster for merchant-level grouping
5. Update metadata harmonizer to work with PlaceCluster (optional)

---

## Metadata Harmonizer Enhancement

**Current approach:** LLM + similarity search (accurate, slow, expensive)

**Opportunity:**
For new receipts → skip harmonizer (Google data is clean via ReceiptPlace)
For old receipts → keep current harmonizer (cleans ReceiptMetadata)

**Benefit:** Reduce harmonizer calls for new receipts by ~100% (they come pre-cleaned from Google)

**Not a code change:** Just operational logic in receipt_agent
