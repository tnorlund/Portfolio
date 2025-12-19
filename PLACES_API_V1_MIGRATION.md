# Google Places API v1 Migration + ReceiptPlace Entity

**Status:** Implementation Complete (12/16 Phases) - Ready for Production Deployment

## Executive Summary

This document describes the complete migration from Legacy Google Places API to the new Places API v1, along with the introduction of the ReceiptPlace entity that captures rich location data including geographic coordinates for spatial clustering.

### Key Achievements

- ✅ **API Migration:** Full v1 client implementation with backward compatibility adapter
- ✅ **Entity Design:** New ReceiptPlace entity capturing 25+ data fields including coordinates, hours, business status
- ✅ **Dual-Write:** Both ReceiptMetadata and ReceiptPlace written during metadata finding
- ✅ **Spatial Indexing:** Geohash-based GSI4 for efficient nearby place detection
- ✅ **Backfill Ready:** Automated migration script for existing receipts

## Architecture Overview

### Three-Layer Architecture

```
┌─────────────────────────────────────────────────────┐
│  Application Layer (receipt_agent)                   │
│  - Metadata finder with dual-write                   │
│  - Uses v1 API via Places client                     │
└────────────┬────────────────────────────────────────┘
             │
┌────────────▼────────────────────────────────────────┐
│  Places API Client Layer                            │
│  ┌──────────────────┐      ┌──────────────────┐    │
│  │  Legacy Client   │      │   V1 Client      │    │
│  │  (deprecated)    │      │   (new)          │    │
│  └──────────────────┘      └──────────────────┘    │
│         │                          │                │
│   Adapter Layer (backward compat)  │                │
│         │                          │                │
│  ┌─────────────────────────────────┘                │
│  │  PlacesClient (factory)                          │
│  │  - Returns v1 if use_v1_api=True                 │
│  │  - Returns legacy if use_v1_api=False            │
│  └──────────────────┬─────────────────────────────┘
└─────────────────────┼──────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────┐
│  Google Places API (REST)                          │
│  - Legacy: maps.googleapis.com/maps/api/place/     │
│  - v1: places.googleapis.com/v1/                   │
└────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. API Migration (Phase 1: Complete)

**Files:**
- `receipt_places/types_v1.py` - Pydantic models for v1 API responses
- `receipt_places/adapter.py` - Backward compatibility layer
- `receipt_places/client_v1.py` - Full v1 API client
- `receipt_places/config.py` - Feature flag `use_v1_api`
- `tests/test_adapter.py` - 23 comprehensive adapter tests

**Key APIs Implemented:**
- `get_place_details(place_id)` - Fetch full place info with field masks
- `search_by_phone(phone_number)` - Phone number-based search
- `search_by_address(address)` - Address-based search

**Cost Savings:**
- Field mask optimization: 20-30% cost reduction via selective field requests
- Only request needed fields: `name`, `address`, `phone`, `coordinates`, `hours`, `status`

### 2. Entity Design (Phase 2.1-2.3: Complete)

**ReceiptPlace Entity:**
- Replaces ReceiptMetadata for new receipts
- Captures 25+ fields from v1 API response
- Auto-calculates geohash from coordinates (GSI4)

**Key Fields:**
```python
# Identity
place_id: str                      # Google Place ID
merchant_name: str
merchant_category: str             # Primary business type

# Geographic
latitude: Optional[float]          # For spatial clustering
longitude: Optional[float]
geohash: str                       # Precision 6 (~1.2km), auto-calculated
viewport_ne/sw_lat/lng: float     # Bounding box

# Business Info
business_status: str               # OPERATIONAL, CLOSED_TEMPORARILY, CLOSED_PERMANENTLY
open_now: Optional[bool]
hours_summary: List[str]           # "Mon: 9 AM - 5 PM"
hours_data: Dict                   # Structured periods

# Contact
phone_intl: str                    # International format
website: str
maps_url: str

# Metadata
matched_fields: List[str]
validated_by: ValidationMethod     # INFERENCE, GPT, MANUAL
validation_status: str             # MATCHED, UNSURE, NO_MATCH
confidence: float                  # 0.0-1.0
```

**DynamoDB Schema:**
```text
PK: IMAGE#{image_id}
SK: RECEIPT#{receipt_id:05d}#PLACE

GSI1: Query by merchant name
GSI2: Query by place_id
GSI3: Query by validation status
GSI4: Spatial queries via geohash (NEW)
      GSI4PK: GEOHASH#{geohash}
      GSI4SK: PLACE#{place_id}
```

### 3. Integration (Phase 2.4-2.5: Complete)

**Dual-Write Pattern:**

```python
# In receipt_agent/metadata_finder.apply_fixes()
for match in matches_to_update:
    # Write ReceiptMetadata (legacy)
    dynamo.update_receipt_metadata(metadata)

    # Write ReceiptPlace (new, with v1 API data)
    if create_receipt_place and metadata.place_id:
        place_v1 = await places.get_place_details(metadata.place_id)
        receipt_place = ReceiptPlace(...)
        dynamo.add_receipt_place(receipt_place)
```

**Backfill Script:**

```bash
# Dry run: preview migration
python scripts/backfill_receipt_place.py --dry-run

# Backfill first 1000 receipts
python scripts/backfill_receipt_place.py --limit 1000

# Backfill specific receipt
python scripts/backfill_receipt_place.py --image-id ABC --receipt-id 1
```

## Production Deployment Plan

### Phase 3.1: Safe Baseline Deployment

**Before deployment:**
1. Set `use_v1_api=False` (default)
2. Dual-write enabled (default `create_receipt_place=True`)
3. Metadata finder writes both ReceiptMetadata and ReceiptPlace

**Deployment checklist:**
- [ ] Code review and merge to main
- [ ] Deploy to staging
- [ ] Test dual-write with sample receipts
- [ ] Verify ReceiptPlace records created correctly
- [ ] Check API costs and cache hit rates
- [ ] Monitor error rates in production

**Expected behavior:**
- Receipt agent continues working normally
- ReceiptMetadata records created as before
- ReceiptPlace records created alongside (monitored)
- v1 API used for ReceiptPlace enrichment only
- Zero breaking changes

### Phase 3.2: Gradual Rollout

**Timeline:**
- Week 1-2: 10% of receipts use v1 API
  - Set `use_v1_api=True` for 10% of traffic
  - Monitor metrics closely
- Week 3: 50% rollout
  - Increase to 50% if metrics look good
  - Continue monitoring
- Week 4: 100% rollout
  - Full migration to v1 API
  - Legacy client deprecated

**Rollout mechanism:**
```python
import random
config.use_v1_api = random.random() < 0.10  # 10% traffic
```

### Phase 3.3: Data Backfill

**Strategy:**
1. Backfill in phases:
   - First 1000 receipts (test phase)
   - Next 10,000 receipts (validation)
   - Remaining receipts (full backfill)

2. Commands:
   ```bash
   # Dry run to verify
   python scripts/backfill_receipt_place.py --dry-run --limit 1000

   # Actual backfill
   python scripts/backfill_receipt_place.py --limit 1000

   # Monitor progress
   python scripts/backfill_receipt_place.py --limit 10000
   ```

3. Verification:
   - Query GSI4 by geohash to find nearby receipts
   - Verify coordinates are valid
   - Check confidence scores
   - Monitor DynamoDB write units

## Migration Path Forward

### After Phase 3 (Production Live)

**Phase 4: Merchant Clustering**
- Design PlaceCluster entity
- Implement clustering logic using geohash + location
- Move canonical_* fields to PlaceCluster
- Separate ReceiptPlace (location data) from PlaceCluster (grouping logic)

**Phase 5: Consumer Updates**
- Update receipt_agent to use ReceiptPlace for queries
- Implement spatial clustering workflows
- Remove legacy metadata_validator
- Update analytics to use v1 data

## Monitoring and Metrics

### Key Metrics to Track

**API Performance:**
- v1 API latency (target: < 200ms)
- v1 API error rates (target: < 0.1%)
- Cache hit rates (target: 70-90% for phone, 40-60% for address)
- API costs (target: 20-30% reduction vs legacy)

**Data Quality:**
- ReceiptPlace creation success rate (target: > 95%)
- Geohash accuracy (all places have valid coordinates)
- Confidence score distribution
- Validation status breakdown

**Business Metrics:**
- Receipt processing latency (must not increase)
- Merchant matching accuracy (should improve)
- Spatial clustering success (new capability)

### Logging & Observability

```python
logger.info(f"Created ReceiptPlace for {image_id}#{receipt_id} "
           f"(place_id={place_id}, lat={latitude}, lng={longitude})")

# Monitor dual-write success
logger.debug(f"Dual-write: ReceiptMetadata={meta_ok}, "
            f"ReceiptPlace={place_ok}")

# Track backfill progress
logger.info(f"Backfill progress: {processed}/{total} "
           f"({processed/total*100:.1f}%) "
           f"{created} created, {failed} failed")
```

## Rollback Plan

### If Issues Arise

1. **Quick Rollback:**
   ```python
   # Set feature flag to False
   config.use_v1_api = False

   # Disable dual-write
   create_receipt_place = False
   ```

2. **Partial Rollback:**
   - Keep legacy client running
   - Resume dual-write with legacy API
   - Debug v1 API issues

3. **Data Recovery:**
   - ReceiptPlace records coexist with ReceiptMetadata
   - No data loss (both entities separate)
   - Can safely delete ReceiptPlace and regenerate

## Testing Checklist

### Unit Tests
- [ ] Adapter layer (23 tests passing)
- [ ] v1 API client endpoint tests
- [ ] ReceiptPlace validation tests
- [ ] Geohash calculation tests

### Integration Tests
- [ ] End-to-end metadata finding with dual-write
- [ ] Backfill script with sample data
- [ ] Query by geohash (GSI4)
- [ ] Spatial distance calculations

### Performance Tests
- [ ] Dual-write latency impact
- [ ] Geohash calculation performance
- [ ] DynamoDB write throttling
- [ ] API rate limiting

## Success Criteria

✅ **Phase 3 Complete When:**
- v1 API live in production with feature flag OFF
- Dual-write working correctly
- Both ReceiptMetadata and ReceiptPlace being created
- Backfill script tested and ready
- Monitoring and alerting in place
- Rollback plan documented and tested

✅ **Phase 4 Complete When:**
- PlaceCluster entity designed and implemented
- 100% of active receipts have ReceiptPlace records
- Spatial clustering queries working correctly
- Legacy code removed, v1 API at 100% traffic
- Analytics updated to use new data model

## References

- [Google Places API v1 Documentation](https://developers.google.com/maps/documentation/places/web-service/overview)
- [Geohashing for Spatial Indexing](https://en.wikipedia.org/wiki/Geohash)
- [DynamoDB Best Practices](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/BestPractices.html)
- Plan File: `/Users/tnorlund/.claude/plans/declarative-churning-turtle.md`

## Questions & Blockers

**Q: Why keep ReceiptMetadata if we have ReceiptPlace?**
A: Gradual migration strategy. ReceiptMetadata is backward compatible, ReceiptPlace adds rich data without breaking changes. Legacy code continues working while new code uses ReceiptPlace.

**Q: What if v1 API returns no coordinates?**
A: Geohash field remains empty, GSI4 queries fail gracefully. ReceiptPlace still stores other useful data (hours, status, ratings). Fallback to legacy address-based clustering.

**Q: How does geohashing work for spatial queries?**
A: Precision 6 (~1.2km cells) groups nearby places. Query GSI4 for geohash and neighbors to find all receipts within ~2km. Then use Haversine formula for exact distance.

**Q: Can we rollback mid-backfill?**
A: Yes, backfill is resumable (tracks last_key). Safe to pause, debug issues, and resume. ReceiptPlace records are independent, can be deleted and regenerated.
