# Gradual Rollout Plan: Places API v1 Migration (Phase 3.2)

**Timeline:** 4 weeks (starting after Phase 3.1 baseline deployment)
**Risk Level:** Low (feature flag enables instant rollback)
**Goal:** 100% traffic on v1 API with zero incidents

## Rollout Phases Overview

```
Week 1-2 (Phase 1):  10% v1 API  ✓ Baseline stable
                     90% Legacy

Week 3 (Phase 2):    50% v1 API  ✓ Phase 1 healthy
                     50% Legacy

Week 4 (Phase 3):   100% v1 API  ✓ Phase 2 healthy
                      0% Legacy   → Complete migration
```

## Week 1-2: Phase 1 Rollout (10% v1)

### Pre-Rollout (Day 1 before Phase 1)

**Configuration:**
```python
# receipt_places/config.py
use_v1_api = True  # Enable v1 API usage
v1_traffic_percentage = 10.0  # Only 10% of traffic
```

**Deployment:**
- [ ] Deploy with 10% v1 traffic setting
- [ ] Verify legacy API still receiving 90% traffic
- [ ] Confirm metrics collection active
- [ ] Set up real-time dashboards

**Team Notification:**
- [ ] Engineering: "Beginning Phase 1 - 10% rollout"
- [ ] Ops: "Monitor v1 API metrics closely"
- [ ] Product: "Initial production rollout in progress"

### Daily Monitoring (Week 1-2)

**Every 4 hours:**
- [ ] Error rate check: < 1% required
- [ ] Latency check p99: < 2s required
- [ ] Dual-write success: > 99% required
- [ ] API cost trend analysis
- [ ] DynamoDB capacity utilization

**Daily (End of Day):**
- [ ] Compile metrics report
- [ ] Review any incidents
- [ ] Check ReceiptPlace creation rates
- [ ] Validate cache hit rates

**Weekly (Friday):**
- [ ] Full metric review
- [ ] Compare v1 vs legacy performance
- [ ] Team debriefing
- [ ] Decision: advance to Phase 2 or hold

### Phase 1 Success Criteria

**Must Meet (Required for Phase 2):**
- [ ] Error rate < 1% for 7 consecutive days
- [ ] p99 latency < 2s for 7 consecutive days
- [ ] Dual-write success > 99% for 7 consecutive days
- [ ] ReceiptPlace creation > 95% success
- [ ] No DynamoDB throttling incidents
- [ ] API quota within expected range

**Should Meet (Indicators):**
- [ ] Cache hit rates maintained (±5% of baseline)
- [ ] API cost within ±10% of projection
- [ ] Geohash data 99%+ valid
- [ ] No customer-facing issues

**Rollback Triggers:**
- [ ] Error rate > 5% at any point → immediate rollback
- [ ] p99 latency > 5s at any point → immediate rollback
- [ ] Dual-write success < 95% → investigate and rollback if unresolved
- [ ] Unplanned incidents affecting users → rollback and investigate

### Phase 1 Rollback Procedure

If rollback needed:
```bash
# Set configuration to 0% v1
use_v1_api = True  # But v1_traffic_percentage = 0.0
v1_traffic_percentage = 0.0
create_receipt_place = False  # Disable dual-write
```

Then:
1. Redeploy with configuration change
2. Verify 100% traffic on legacy API
3. Assess root cause
4. Fix issues before retry
5. Wait 3 days before Phase 1 retry

## Week 3: Phase 2 Rollout (50% v1)

### Transition Criteria

**All of these must be true:**
- [ ] Phase 1 ran for full 2 weeks
- [ ] Phase 1 success criteria all met
- [ ] Team consensus to proceed
- [ ] No known issues in backlog
- [ ] DynamoDB capacity validated for 50% increase

### Phase 2 Configuration

```python
use_v1_api = True
v1_traffic_percentage = 50.0  # 50% v1, 50% legacy split
create_receipt_place = True
```

### Deployment (Monday of Week 3)

**Before Deployment:**
- [ ] All Phase 1 metrics reviewed
- [ ] Stakeholders notified
- [ ] Team briefed on Phase 2
- [ ] On-call coverage confirmed
- [ ] Runbooks reviewed

**Deployment:**
- [ ] Update configuration
- [ ] Deploy during business hours
- [ ] Confirm 50/50 traffic split
- [ ] Verify metrics collection active
- [ ] Monitor for first hour

### Daily Monitoring (Week 3)

**Every 2 hours:**
- [ ] Check error rates (legacy vs v1)
- [ ] Compare latency p99 between implementations
- [ ] Verify dual-write rate
- [ ] Check for any patterns

**Daily:**
- [ ] Compile comparative metrics (v1 vs legacy)
- [ ] Cost analysis by API version
- [ ] ReceiptPlace creation metrics
- [ ] DynamoDB metrics

**Phase 2 Specific Checks:**
- [ ] Are v1 and legacy error rates similar?
- [ ] Is v1 latency better or worse than legacy?
- [ ] ReceiptPlace creation rate sustainable?
- [ ] API costs trending as expected?

### Phase 2 Success Criteria

**Must Meet (Required for Phase 3):**
- [ ] v1 error rate < 1.5% (slightly higher due to larger volume)
- [ ] v1 p99 latency < 2.5s
- [ ] Dual-write success > 99%
- [ ] ReceiptPlace creation > 95% success
- [ ] No degradation vs legacy API
- [ ] v1 and legacy error rates within 0.5% of each other

**Should Meet:**
- [ ] v1 latency ≤ legacy latency (within 10%)
- [ ] API cost savings visible (target: 20-30%)
- [ ] Cache hit rates stable
- [ ] Customer experience metrics stable

**Rollback Triggers (Same as Phase 1):**
- [ ] Error rate > 5%
- [ ] p99 latency > 5s
- [ ] Dual-write success < 95%
- [ ] Any critical customer incident

### Phase 2 Rollback Procedure

If rollback needed:
```python
v1_traffic_percentage = 0.0  # Back to 0%
# Keep create_receipt_place=True for monitoring dual-write
```

Then investigate issues before Phase 2 retry.

## Week 4: Phase 3 Rollout (100% v1)

### Transition Criteria

**All of these must be true:**
- [ ] Phase 2 ran for full week
- [ ] Phase 2 success criteria all met
- [ ] v1 API proven stable and performant
- [ ] Team consensus
- [ ] DynamoDB capacity validated
- [ ] Backfill plan ready for Phase 3.3

### Phase 3 Configuration

```python
use_v1_api = True
v1_traffic_percentage = 100.0  # 100% v1, 0% legacy
create_receipt_place = True
```

### Deployment (Monday of Week 4)

**Before Deployment:**
- [ ] All Phase 2 metrics reviewed
- [ ] Stakeholders notified of complete migration
- [ ] Celebrate Phase 1 and Phase 2 success
- [ ] Ops team stands by
- [ ] Legacy API monitoring still active (for fallback)

**Deployment:**
- [ ] Update configuration
- [ ] Deploy during business hours
- [ ] Confirm 100% v1 traffic
- [ ] Verify all metrics flowing
- [ ] Monitor continuously for first 24 hours

### Critical Success Period (Week 4)

**First 24 Hours:**
- [ ] Continuous monitoring
- [ ] Any issues → Immediate rollback available
- [ ] Alert on any deviations
- [ ] Engineering team actively watching

**Days 2-7:**
- [ ] Continue daily reviews
- [ ] Finalize ReceiptPlace backfill plan
- [ ] Start planning Phase 3.3 (data backfill)
- [ ] Document lessons learned

### Final Success Criteria

**Complete Migration Success:**
- [ ] 7 days of stable operation at 100% v1
- [ ] Error rates stable and low (< 0.5%)
- [ ] Latency improved or equal to legacy
- [ ] Cost savings achieved (20-30% target)
- [ ] No customer-facing issues
- [ ] ReceiptPlace data quality validated

## Key Metrics Dashboard

### Metrics to Track Each Phase

| Metric | Baseline | Phase 1 | Phase 2 | Phase 3 |
|--------|----------|---------|---------|---------|
| Error Rate | <0.5% | <1% | <1.5% | <0.5% |
| p99 Latency | <1s | <2s | <2.5s | <2s |
| Dual-Write | N/A | >99% | >99% | >99% |
| Cache Hit Rate | 70-90% | Same | Same | Same |
| API Cost (baseline) | 100% | 105% | 110% | 70% |
| ReceiptPlace Success | N/A | >95% | >95% | >95% |

### Comparison Charts

Track these side-by-side for each phase:
- v1 API Error Rate vs Legacy
- v1 API Latency vs Legacy
- v1 API Cost vs Legacy
- Dual-write Success Rate

## Communication Plan

### Daily (During Rollout Phases)
- [ ] Slack updates in #places-api-migration
- [ ] Status: Green/Yellow/Red
- [ ] Key metrics
- [ ] Any blockers

### Weekly (Every Friday)
- [ ] Complete metrics report
- [ ] Team meeting (15 min)
- [ ] Decision: continue or pause
- [ ] Updated timeline

### Milestone Announcements
- [ ] Phase 1 start: "Beginning controlled rollout"
- [ ] Phase 1 success: "10% rollout successful, proceeding to Phase 2"
- [ ] Phase 2 success: "50% rollout successful, proceeding to Phase 3"
- [ ] Phase 3 complete: "Full migration complete, legacy API deprecated"

## Rollback Scenarios & Recovery

### Scenario 1: Phase 1 Fails (Early Detection)

**If issues in first 2 days:**
1. Immediate rollback to 0% v1
2. Investigate root cause
3. Fix and deploy
4. Wait 3 days
5. Restart Phase 1

**Recovery Time:** 1-2 weeks

### Scenario 2: Phase 2 Fails (Mid-Rollout)

**If issues at 50% traffic:**
1. Immediate rollback to Phase 1 (10%)
2. Investigate and fix
3. Deploy fix
4. Wait 2 days
5. Retry Phase 2

**Recovery Time:** 3-5 days

### Scenario 3: Phase 3 Fails (Late Detection)

**Unlikely but possible:**
1. Immediate rollback to Phase 2 (50%)
2. Critical incident investigation
3. Fix implemented
4. Wait 3-5 days
5. Retry Phase 3

**Recovery Time:** 1-2 weeks

## Success Metrics After Full Rollout

### Expected Outcomes
- ✓ 100% traffic on v1 API
- ✓ 20-30% cost reduction vs legacy
- ✓ Same or better performance
- ✓ Zero rollbacks after Day 7 of Phase 3
- ✓ ReceiptPlace data foundation ready for Phase 3.3

### Next Steps (Week 5+)
- Proceed to Phase 3.3 (Backfill existing receipts)
- Deprecate legacy API (still available for emergencies)
- Update documentation
- Plan Phase 4 (PlaceCluster entity)

---

## Quick Reference: Phase Advancement

```
┌─────────────────────┐
│ Phase 1: 10% v1     │ ← 2 weeks
├─────────────────────┤
│ ✓ Error rate <1%    │
│ ✓ p99 latency <2s   │
│ ✓ Dual-write >99%   │
└────────────┬────────┘
             │
             ▼
┌─────────────────────┐
│ Phase 2: 50% v1     │ ← 1 week
├─────────────────────┤
│ ✓ Same as Phase 1   │
│ ✓ Cost trending ✓   │
│ ✓ v1 ≤ Legacy       │
└────────────┬────────┘
             │
             ▼
┌─────────────────────┐
│ Phase 3: 100% v1    │ ← 1 week
├─────────────────────┤
│ ✓ All metrics solid │
│ ✓ 7 days stable     │
│ ✓ Ready for Phase 4 │
└─────────────────────┘
```
