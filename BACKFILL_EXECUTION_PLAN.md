# Data Backfill Execution Plan: Phase 3.3

**Purpose:** Migrate all existing ReceiptMetadata to ReceiptPlace entities
**Timeline:** 1-2 weeks (after Phase 3.2 complete at 100% v1)
**Data Volume:** [ESTIMATE_BASED_ON_ACTUAL_DB]
**Approach:** Phased backfill with validation gates

## Pre-Backfill Phase (Day 1-2)

### Database Preparation

**Backup & Snapshots:**
- [ ] Create DynamoDB point-in-time snapshot
  ```bash
  aws dynamodb create-backup \
    --table-name receipts \
    --backup-name receipts-pre-backfill-$(date +%Y%m%d)
  ```
- [ ] Verify backup successful
- [ ] Document backup location and recovery procedure

**Capacity Planning:**
- [ ] Calculate estimated DynamoDB WCU needed
  - Formula: (ReceiptMetadata records × 1.5) / 86400 seconds in day
  - Add 50% buffer for write throughput
- [ ] Request WCU increase if needed
- [ ] Wait for capacity provisioning
- [ ] Verify new capacity active

**Indexes Verification:**
- [ ] Verify GSI1 exists (merchant name queries)
- [ ] Verify GSI2 exists (place_id queries)
- [ ] Verify GSI3 exists (validation status queries)
- [ ] Verify GSI4 exists (geohash spatial queries)
- [ ] All indexes in ACTIVE state

### Data Assessment

**Counting Metadata Records:**
```bash
python scripts/backfill_receipt_place.py --dry-run --count-only
```

Results:
- [ ] Total ReceiptMetadata records: _________
- [ ] Records with place_id: _________
- [ ] Records with latitude/longitude: _________
- [ ] Estimated ReceiptPlace records to create: _________

**Data Quality Check:**
```python
# Run queries to assess data quality
SELECT COUNT(*) FROM ReceiptMetadata WHERE place_id IS NOT NULL
SELECT COUNT(*) FROM ReceiptMetadata WHERE merchant_name IS NULL
SELECT COUNT(*) FROM ReceiptMetadata WHERE address IS NULL
```

- [ ] Records with valid place_id: _________%
- [ ] Records with merchant_name: _________%
- [ ] Records with address: _________%
- [ ] Records with all 3 fields: _________%

### Test Run

**Small-Scale Validation (100 records):**
```bash
python scripts/backfill_receipt_place.py \
  --dry-run \
  --limit 100
```

- [ ] Dry run completes without errors
- [ ] Review sample ReceiptPlace records created
- [ ] Verify geohash calculated correctly
- [ ] Check confidence scores reasonable

**Actual Test Backfill (1000 records):**
```bash
python scripts/backfill_receipt_place.py \
  --limit 1000 \
  --batch-size 50
```

- [ ] Backfill completes successfully
- [ ] Monitor DynamoDB metrics during run
- [ ] Verify ReceiptPlace records in DynamoDB
- [ ] Check for any error patterns
- [ ] Verify query performance (GSI4 geohash queries)

**Query Validation:**
```python
# Test GSI4 queries work correctly
dynamo.get_receipt_places_by_geohash("dr5reg")
```

- [ ] GSI4 queries return correct results
- [ ] Latency acceptable (< 100ms)
- [ ] Pagination works correctly

### Operational Readiness

**Team & On-Call:**
- [ ] On-call engineer assigned
- [ ] Backup on-call configured
- [ ] Communication channels active (#places-api-backfill)
- [ ] Runbooks printed and reviewed

**Monitoring Setup:**
- [ ] CloudWatch dashboard for backfill metrics
- [ ] Alerts configured for:
  - [ ] DynamoDB throttling
  - [ ] API errors > 1%
  - [ ] Backfill process errors
- [ ] Log aggregation active
- [ ] Real-time metrics collection

**Rollback Plan:**
- [ ] Recovery procedure documented
- [ ] Cleanup scripts prepared
- [ ] Team briefed on abort procedures
- [ ] Backup availability confirmed

## Backfill Execution Phase

### Phase 1: Initial Batch (5-10% of Records)

**Before Backfill:**
- [ ] Date/time: __________
- [ ] Expected duration: __________
- [ ] Estimated WCU: __________
- [ ] Team standing by

**Backfill Execution:**
```bash
# Estimate records: [TOTAL * 0.075]
python scripts/backfill_receipt_place.py \
  --limit [BATCH_SIZE] \
  --batch-size 50
```

**Monitoring During Backfill:**
- [ ] Every 5 minutes: Check error rate
- [ ] Every 5 minutes: Monitor DynamoDB WCU usage
- [ ] Every 5 minutes: Check API error rate
- [ ] Every 15 minutes: Review application logs

**After Batch Completion:**
- [ ] [ ] Document completion time
- [ ] [ ] Record metrics:
  - Records created: __________
  - Records skipped: __________
  - Records failed: __________
  - Total duration: __________
  - Errors encountered: [list any]

**Validation After Phase 1:**
```sql
SELECT COUNT(*) FROM ReceiptPlace;
SELECT COUNT(*) FROM ReceiptPlace WHERE latitude IS NULL;
SELECT COUNT(*) FROM ReceiptPlace WHERE geohash = '';
```

- [ ] ReceiptPlace record count: __________
- [ ] Records with valid coordinates: __________%
- [ ] Records with valid geohash: __________%
- [ ] Spot check 10 records match ReceiptMetadata

**Decision Gate:**
- [ ] All validation checks pass → Proceed to Phase 2
- [ ] Issues found → Pause and investigate
  - [ ] Root cause identified: __________
  - [ ] Fix deployed: __________
  - [ ] Resume backfill: __________

### Phase 2: Major Batch (Next 20-50% of Records)

**Same process as Phase 1, but larger batch:**

```bash
python scripts/backfill_receipt_place.py \
  --limit [PHASE_2_SIZE] \
  --batch-size 100  # Larger batch
```

**Phase 2 Validation:**
- [ ] Verify Phase 1 + Phase 2 data consistency
- [ ] Check for duplicate records
- [ ] Validate geohash distribution
- [ ] Test spatial queries (GSI4)

### Phase 3: Remaining Records

**Final backfill of remaining records:**

```bash
python scripts/backfill_receipt_place.py \
  # No limit - backfill all remaining
  --batch-size 200  # Largest batch
```

**After Completion:**
- [ ] Total ReceiptPlace records created: __________
- [ ] Total duration across all phases: __________
- [ ] Total API calls made: __________
- [ ] Estimated API cost: $__________

## Post-Backfill Validation (Day 3-5)

### Data Consistency Checks

**ReceiptMetadata vs ReceiptPlace Comparison:**
```sql
-- Check for mismatches
SELECT m.image_id, m.receipt_id
FROM ReceiptMetadata m
LEFT JOIN ReceiptPlace p ON m.image_id = p.image_id AND m.receipt_id = p.receipt_id
WHERE p.image_id IS NULL AND m.place_id IS NOT NULL;
```

- [ ] No unexpected missing ReceiptPlace records
- [ ] All records with place_id have ReceiptPlace: __________%
- [ ] Address matches between metadata and place: __________%
- [ ] Merchant name matches: __________%

**Geohash Validation:**
```sql
SELECT COUNT(*) FROM ReceiptPlace WHERE geohash != '' AND latitude IS NOT NULL;
```

- [ ] Geohash present for all records with coordinates: __________%
- [ ] Geohash format valid: __________%
- [ ] Geohash precision consistent (precision 6): __________%

**Query Performance Validation:**
- [ ] GSI1 (merchant name) latency: < 100ms
- [ ] GSI2 (place_id) latency: < 100ms
- [ ] GSI3 (validation status) latency: < 100ms
- [ ] GSI4 (geohash) latency: < 100ms

### Application Behavior Validation

**Receipt Processing:**
- [ ] New receipts still creating ReceiptMetadata: YES / NO
- [ ] New receipts still creating ReceiptPlace: YES / NO
- [ ] Dual-write success rate: __________%
- [ ] Error rate normal: __________%

**Query Tests:**
```python
# Test all GSI queries work
dynamo.get_receipt_places_by_merchant("STARBUCKS")
dynamo.list_receipt_places_with_place_id("ChIJ...")
dynamo.get_receipt_places_by_status("MATCHED")
dynamo.get_receipt_places_by_geohash("dr5reg")
```

- [ ] All GSI queries return correct results
- [ ] Pagination works
- [ ] No unexpected errors

### Cost Analysis

**Compare Metrics Before/After:**
| Metric | Before Backfill | After Backfill | Delta |
|--------|-----------------|----------------|-------|
| DynamoDB WCU (baseline) | _________ | _________ | _________ |
| DynamoDB storage | _________ | _________ | _________ |
| API call volume | _________ | _________ | _________ |
| Query latency p99 | _________ | _________ | _________ |

- [ ] Acceptable performance impact: YES / NO
- [ ] Storage increase expected: YES / NO
- [ ] Query latency increase acceptable: YES / NO

## Success Criteria

### Must Have (Required for Completion):
- [ ] 100% of ReceiptMetadata with place_id have ReceiptPlace
- [ ] All ReceiptPlace records have valid geohash
- [ ] Zero data consistency issues
- [ ] All GSI queries working correctly
- [ ] No application degradation
- [ ] Zero unexpected data loss

### Should Have (Indicators):
- [ ] Backfill completed in < 2 weeks
- [ ] DynamoDB throttling avoided (or minimal)
- [ ] API costs within projections
- [ ] No critical incidents during backfill
- [ ] Team confidence high for next phase

### Completion Sign-Off:
- [ ] Engineering Lead: _____________ Date: _____
- [ ] Data Lead: _____________ Date: _____
- [ ] Ops Lead: _____________ Date: _____

## Troubleshooting Guide

### Issue: DynamoDB Throttling

**Symptoms:**
- Write errors during backfill
- DynamoDB ProvisionedThroughputExceededException

**Resolution:**
1. [ ] Pause backfill immediately
2. [ ] Increase DynamoDB WCU capacity
3. [ ] Wait for scale-up to complete
4. [ ] Resume backfill with smaller batch size
5. [ ] Monitor more frequently

### Issue: API Quota Exceeded

**Symptoms:**
- Places API returns 429 errors
- High error rate during backfill

**Resolution:**
1. [ ] Check remaining API quota
2. [ ] Reduce batch size to slow down API calls
3. [ ] Add delays between batches
4. [ ] Consider resuming next day if quota exceeded

### Issue: Geohash Calculation Failures

**Symptoms:**
- Some ReceiptPlace records have empty geohash
- Errors like "Invalid coordinates"

**Resolution:**
1. [ ] Check if place has valid coordinates from v1 API
2. [ ] Verify geohash2 library installed
3. [ ] Check error logs for coordinate range issues
4. [ ] Manually review affected places

### Issue: Data Consistency Mismatch

**Symptoms:**
- Address differs between ReceiptMetadata and ReceiptPlace
- Merchant name doesn't match

**Resolution:**
1. [ ] Likely v1 API has more accurate data
2. [ ] Review specific record differences
3. [ ] Decide if manual correction needed
4. [ ] Document as data improvement

## Next Steps (After Phase 3.3)

### Immediate (Day 6+):
- [ ] Send completion report to stakeholders
- [ ] Document lessons learned
- [ ] Thank team and celebrate success

### Week 2:
- [ ] Sunset legacy metadata processes
- [ ] Archive ReceiptMetadata (keep for 90 days)
- [ ] Plan Phase 4 (PlaceCluster entity)

### Week 3+:
- [ ] Start Phase 4: PlaceCluster design
- [ ] Update analytics for spatial clustering
- [ ] Implement place clustering workflows

## Appendix: Rollback Procedure

**If unrecoverable issues found:**

1. Stop backfill immediately
2. Identify affected ReceiptPlace records (record range)
3. Delete ReceiptPlace records created:
   ```sql
   DELETE FROM ReceiptPlace WHERE timestamp > [backfill_start_time]
   ```
4. Verify ReceiptMetadata intact
5. Document root cause
6. Fix issues
7. Restart backfill with lessons learned

**Recovery Time:** 1-4 hours depending on affected volume
