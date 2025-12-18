# Deployment Checklist: Places API v1 Migration Phase 3.1

**Date:** [DEPLOYMENT_DATE]
**Version:** 1.0
**Status:** Ready for Safe Baseline Deployment (Feature Flag OFF)

## Pre-Deployment Phase (1-2 days before)

### Code Review & Testing
- [ ] All Phase 1-2 code changes reviewed and approved
- [ ] 23 adapter tests passing (test_adapter.py)
- [ ] Integration tests for dual-write passing
- [ ] No critical linting issues (pylint/flake8)
- [ ] Code coverage meets minimum threshold (>80%)

### Configuration Verification
- [ ] Feature flag `use_v1_api` defaults to `False` in PlacesConfig
- [ ] Dual-write enabled by default: `create_receipt_place=True`
- [ ] API keys configured for Google Places (both v1 and legacy)
- [ ] DynamoDB table exists with proper GSI configuration
- [ ] CloudWatch logging configured for receipt_agent

### Dependency Check
- [ ] All required packages in requirements.txt/pyproject.toml
  - pydantic >= 2.0
  - boto3 >= 1.26.0
  - geohash2 (for spatial indexing)
- [ ] No version conflicts
- [ ] Development dependencies separated from production

### Documentation Complete
- [ ] PLACES_API_V1_MIGRATION.md written and reviewed
- [ ] Runbook for ops team prepared
- [ ] Rollback procedures documented
- [ ] Health check endpoints documented
- [ ] Architecture diagrams up to date

## Deployment Day (Production)

### Pre-Deployment (1 hour before)

**Infrastructure Checks:**
- [ ] DynamoDB table status: Active
- [ ] DynamoDB capacity provisioned
  - [ ] Read capacity: [EXPECTED_VALUE] RCU
  - [ ] Write capacity: [EXPECTED_VALUE] WCU
- [ ] Google Places API quota available
- [ ] Network connectivity to AWS and Google APIs verified
- [ ] Backups of DynamoDB created

**System Checks:**
- [ ] Run `scripts/verify_deployment_readiness.py`
  - [ ] All critical checks pass
  - [ ] No critical warnings
- [ ] receipt_agent service healthcheck passes
- [ ] Cache service status: Healthy
- [ ] Log aggregation system: Ready

**Team Readiness:**
- [ ] On-call engineer assigned
- [ ] Slack channel monitored: #places-api-migration
- [ ] Runbooks printed/accessible
- [ ] Rollback plan reviewed with team

### Deployment (Production Release)

**Code Deployment:**
- [ ] Deploy to production with feature flag OFF
  ```
  use_v1_api = False  # Safe baseline
  create_receipt_place = True  # Enable dual-write
  ```
- [ ] Verify deployment successful
- [ ] Confirm no errors in CloudWatch logs
- [ ] Verify receipt_agent still processing

**Dual-Write Verification (First 30 minutes):**
- [ ] Check ReceiptMetadata records created
- [ ] Check ReceiptPlace records created alongside
- [ ] Verify geohash calculated correctly
- [ ] No increase in error rates
- [ ] Latency impact within expected range

**Monitoring Setup:**
- [ ] CloudWatch dashboards active
- [ ] Alerts configured and active
  - [ ] DynamoDB throttling alert
  - [ ] API error rate alert (>1%)
  - [ ] Latency alert (p99 > 2s)
  - [ ] Dual-write failure rate alert (>0.1%)
- [ ] Log tail monitoring initiated
- [ ] Metrics collection started

### Immediate Post-Deployment (First Hour)

**System Behavior:**
- [ ] Receipt processing latency normal
  - [ ] p50: < 100ms
  - [ ] p95: < 500ms
  - [ ] p99: < 2000ms
- [ ] Error rate normal (< 0.1%)
- [ ] API cost running at expected level
- [ ] No DynamoDB throttling

**Data Validation:**
- [ ] Sample 10 ReceiptPlace records created
- [ ] Verify all fields populated correctly
- [ ] Geohash format valid
- [ ] Confidence scores reasonable (0.5-0.95)

**Stakeholder Notification:**
- [ ] Deployment success announced in #places-api-migration
- [ ] Engineering team: "v1 API running in dual-write mode"
- [ ] Product team: "Safe baseline deployed, monitoring metrics"
- [ ] Ops team: "Alerts active, prepare for potential issues"

### Ongoing Monitoring (First 24 Hours)

**Hourly Checks (First 6 Hours):**
- [ ] Error rate stable and low
- [ ] Latency stable
- [ ] DynamoDB write units consumption normal
- [ ] API quota usage within expected range
- [ ] ReceiptPlace records created consistently

**Post 6 Hours:**
- [ ] Cumulative metrics look good
- [ ] No pattern of failures
- [ ] No unexpected latency spikes
- [ ] Dual-write success rate > 99%

**End of Day Checks:**
- [ ] Summary metrics collected
- [ ] Any issues escalated appropriately
- [ ] Team debriefing scheduled if needed
- [ ] Logs archived for review

## Deployment Success Criteria

### Must-Have (Block Rollback if Failed)
- [ ] No increase in receipt processing errors
- [ ] Dual-write success rate > 99%
- [ ] ReceiptPlace records created for all found metadata
- [ ] No DynamoDB throttling
- [ ] No API quota exceeded

### Should-Have (Monitor Closely)
- [ ] API latency within ±10% of baseline
- [ ] Cache hit rates maintained (70-90% phone, 40-60% address)
- [ ] API costs within 10% of projection
- [ ] Geohash data valid for 95%+ of places

### Nice-to-Have (For Future Optimization)
- [ ] Spatial query performance acceptable
- [ ] Confidence score distribution reasonable
- [ ] All GSI queries responding normally
- [ ] Metrics dashboard populated and readable

## Post-Deployment Phase (Day 2+)

### Monitoring Cadence
- [ ] Reduce from hourly to daily checks (Day 2)
- [ ] Shift to weekly reviews (Week 2+)
- [ ] Continue alerts active for 30 days

### Metrics Review (Daily)
- [ ] Dual-write success rate
- [ ] ReceiptPlace creation rate
- [ ] Geohash calculation success rate
- [ ] DynamoDB WCU consumption
- [ ] API error rates

### Weekly Review (Starting Week 2)
- [ ] Overall system health
- [ ] Cost analysis
- [ ] Identify optimization opportunities
- [ ] Plan for next phase (gradual rollout)

### Risk Monitoring (Ongoing)
- [ ] Watch for geohash calculation failures
- [ ] Monitor for ReceiptPlace write failures
- [ ] Track API cost trends
- [ ] Monitor for data consistency issues

## Rollback Plan

**If Critical Issue Found:**
1. [ ] Set `use_v1_api = False` (already off, but verify)
2. [ ] Set `create_receipt_place = False` to disable dual-write
3. [ ] Redeploy receipt_agent
4. [ ] Verify ReceiptMetadata still being created
5. [ ] Assess damage and create incident report

**Data Cleanup (if needed):**
- [ ] Identify ReceiptPlace records created during issue
- [ ] Verify ReceiptMetadata records intact
- [ ] Delete ReceiptPlace records if needed
- [ ] Re-run backfill script for those records

## Sign-Off

**Pre-Deployment (Date: _____):**
- [ ] Engineering Lead: _________________ Date: _____
- [ ] Ops Lead: _________________ Date: _____
- [ ] Product Lead: _________________ Date: _____

**Post-Deployment (Date: _____):**
- [ ] Engineering Lead: _________________ Date: _____
- [ ] Ops Lead: _________________ Date: _____
- [ ] On-Call Engineer: _________________ Date: _____

## Notes

```
[Use this section for deployment-specific notes, exceptions, or deviations from standard procedure]
```

## Success Metrics Dashboard

**Track these metrics for 30 days:**

| Metric | Baseline | Target | Current | Status |
|--------|----------|--------|---------|--------|
| Error Rate | <0.1% | <0.1% | — | — |
| Latency p50 | <100ms | <100ms | — | — |
| Latency p99 | <2s | <2s | — | — |
| Dual-Write Rate | 100% | 100% | — | — |
| ReceiptPlace Success | 99% | 99% | — | — |
| API Cost | [baseline] | ±10% | — | — |
| DynamoDB WCU | [baseline] | ±10% | — | — |

---

**Next Steps:**
1. Monitor for 24 hours at feature flag OFF
2. If all metrics green: Plan Phase 3.2 (gradual rollout)
3. If issues found: Execute rollback plan
4. Document lessons learned
