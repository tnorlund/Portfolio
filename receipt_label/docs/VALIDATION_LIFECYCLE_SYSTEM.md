# Validation Lifecycle System: From NONE to VALID/INVALID

## Overview

This document outlines a comprehensive system to ensure all labels eventually reach a final state of either **VALID** or **INVALID**, eliminating intermediate states (NONE, PENDING, NEEDS_REVIEW) through a multi-stage validation pipeline.

## Validation Status States

### Current States

1. **NONE**: No validation has ever been initiated (initial state)
2. **PENDING**: Validation has been queued but not yet completed
3. **VALID**: Validation succeeded - label is correct
4. **INVALID**: Validation rejected - label is incorrect
5. **NEEDS_REVIEW**: Validation needs human review (conflicting evidence)

### Goal

All labels should eventually reach **VALID** or **INVALID** - no labels should remain in NONE, PENDING, or NEEDS_REVIEW indefinitely.

## Validation Lifecycle

```
┌─────┐
│NONE │  (Initial state - label just created)
└──┬──┘
   │
   ▼
┌─────────┐
│PENDING  │  (CoVe validation attempted)
└──┬──────┘
   │
   ├─────────────────┬──────────────────┐
   ▼                 ▼                  ▼
┌───────┐      ┌──────────────┐   ┌─────────────┐
│VALID  │      │NEEDS_REVIEW  │   │  INVALID    │
└───────┘      └──────┬───────┘   └─────────────┘
                      │
                      ▼
              ┌──────────────┐
              │Manual Review │
              └──────┬───────┘
                     │
            ┌────────┴────────┐
            ▼                 ▼
        ┌───────┐        ┌─────────┐
        │VALID  │        │ INVALID │
        └───────┘        └─────────┘
```

## Multi-Stage Validation Pipeline

### Stage 1: Initial Validation (NONE → PENDING → VALID/INVALID)

**When**: During receipt processing (LangGraph workflow)

**Methods**:
1. **CoVe (Chain of Verification)**: LLM-based verification
   - Generates verification questions
   - Answers questions by checking receipt text
   - Revises answer if needed
   - **Result**: VALID (if verified) or PENDING (if inconclusive)

2. **ChromaDB Similarity Search**: Pattern-based validation
   - Queries similar words with VALID labels
   - Calculates similarity scores
   - Checks for conflicts
   - **Result**: VALID (high similarity), INVALID (conflicts), or PENDING (low similarity)

**Decision Rules**:
- If CoVe verifies → **VALID**
- If ChromaDB validates (similarity ≥ 0.75, no conflicts) → **VALID**
- If ChromaDB finds conflicts (similarity ≥ 0.65, conflicting labels) → **INVALID**
- Otherwise → **PENDING** (proceed to Stage 2)

### Stage 2: Batch Validation (PENDING → VALID/INVALID/NEEDS_REVIEW)

**When**: Daily batch job for PENDING labels

**Methods**:
1. **ChromaDB Re-validation**: Re-run with more data
   - More VALID labels may have been added since initial validation
   - Use merchant-aware filtering
   - **Result**: VALID, INVALID, or PENDING

2. **OpenAI Batch API Validation**: LLM-based batch processing
   - Submit PENDING labels to OpenAI Batch API
   - LLM validates each label with context
   - **Result**: VALID, INVALID, or NEEDS_REVIEW

3. **Cross-Receipt Validation**: Merchant-level consistency
   - Compare labels across receipts from same merchant
   - Identify patterns and outliers
   - **Result**: VALID (consistent), INVALID (outlier), or PENDING

**Decision Rules**:
- If 2+ methods agree on VALID → **VALID**
- If 2+ methods agree on INVALID → **INVALID**
- If methods disagree → **NEEDS_REVIEW** (proceed to Stage 3)
- If all methods return PENDING → **PENDING** (proceed to Stage 3)

### Stage 3: Escalated Validation (PENDING/NEEDS_REVIEW → VALID/INVALID)

**When**: Weekly batch job for unresolved labels

**Methods**:
1. **Enhanced CoVe**: Re-run with stricter parameters
   - More verification questions
   - Higher confidence thresholds
   - **Result**: VALID, INVALID, or PENDING

2. **Pattern-Based Validation**: Rule-based checks
   - Check against known patterns (dates, currency, etc.)
   - Validate format and structure
   - **Result**: VALID (matches pattern), INVALID (doesn't match), or PENDING

3. **Statistical Analysis**: Merchant-level statistics
   - Compare against merchant-specific patterns
   - Identify statistical outliers
   - **Result**: VALID (within normal range), INVALID (outlier), or PENDING

**Decision Rules**:
- If 2+ methods agree → **VALID** or **INVALID**
- If methods disagree → **NEEDS_REVIEW** (proceed to Stage 4)
- If all methods return PENDING → **NEEDS_REVIEW** (proceed to Stage 4)

### Stage 4: Manual Review (NEEDS_REVIEW → VALID/INVALID)

**When**: Labels that couldn't be automatically resolved

**Methods**:
1. **Human Review Interface**: Manual validation
   - Present label with context (receipt text, similar labels, validation history)
   - Human reviewer makes decision
   - **Result**: VALID or INVALID

2. **Crowdsourcing**: Multiple reviewers
   - Present to multiple reviewers
   - Majority vote determines outcome
   - **Result**: VALID or INVALID

3. **Expert Review**: Domain expert validation
   - For critical labels (GRAND_TOTAL, MERCHANT_NAME)
   - Expert makes final decision
   - **Result**: VALID or INVALID

**Decision Rules**:
- Human reviewer decision → **VALID** or **INVALID** (final)
- No automatic escalation beyond this stage

## Implementation Strategy

### Priority Tiers

Labels are processed in priority order:

#### Tier 1: Critical Labels
- **Labels**: `GRAND_TOTAL`, `MERCHANT_NAME`, `DATE`, `TAX`
- **Stage 1**: Immediate CoVe + ChromaDB validation
- **Stage 2**: Daily batch validation
- **Stage 3**: Weekly escalated validation
- **Stage 4**: Immediate manual review if unresolved

#### Tier 2: Important Labels
- **Labels**: `SUBTOTAL`, `LINE_TOTAL`, `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`
- **Stage 1**: CoVe + ChromaDB validation
- **Stage 2**: Daily batch validation
- **Stage 3**: Weekly escalated validation
- **Stage 4**: Manual review queue (processed within 1 week)

#### Tier 3: Supporting Labels
- **Labels**: `PAYMENT_METHOD`, `LOYALTY_ID`, `WEBSITE`, `PHONE_NUMBER`
- **Stage 1**: ChromaDB validation (CoVe optional)
- **Stage 2**: Weekly batch validation
- **Stage 3**: Monthly escalated validation
- **Stage 4**: Manual review queue (processed within 1 month)

### Time-Based Escalation

Labels automatically escalate if not resolved within time limits:

| Status | Time Limit | Escalation Action |
|--------|------------|-------------------|
| **PENDING** | 7 days | Move to Stage 2 (batch validation) |
| **PENDING** | 30 days | Move to Stage 3 (escalated validation) |
| **PENDING** | 90 days | Move to Stage 4 (manual review) |
| **NEEDS_REVIEW** | 3 days | Prioritize in manual review queue |
| **NEEDS_REVIEW** | 14 days | Escalate to expert review (Tier 1 only) |

### Confidence Scoring

Each validation method returns a confidence score (0.0 - 1.0):

- **≥ 0.90**: Very high confidence → **VALID** or **INVALID** immediately
- **0.75 - 0.89**: High confidence → **VALID** or **INVALID** (Stage 1)
- **0.60 - 0.74**: Medium confidence → **PENDING** (Stage 2)
- **0.40 - 0.59**: Low confidence → **PENDING** (Stage 3)
- **< 0.40**: Very low confidence → **NEEDS_REVIEW** (Stage 4)

### Conflict Resolution

When validation methods disagree:

1. **2 methods agree, 1 disagrees**: Use majority vote
2. **All methods disagree**: Mark as **NEEDS_REVIEW**
3. **Conflicting VALID/INVALID**: Mark as **NEEDS_REVIEW**
4. **All methods return PENDING**: Escalate to next stage

## Scripts and Automation

### 1. Initial Validation Script

**File**: `dev.process_all_receipts_cove.py` (existing)

- Processes all receipts with CoVe + ChromaDB validation
- Marks labels as VALID, INVALID, or PENDING
- Runs during receipt processing

### 2. Batch Validation Script

**File**: `dev.validate_pending_labels_batch.py` (to be created)

- Lists all PENDING labels from DynamoDB
- Groups by label type and priority tier
- Runs Stage 2 validation methods
- Updates validation_status
- **Frequency**: Daily

### 3. Escalated Validation Script

**File**: `dev.validate_pending_labels_escalated.py` (to be created)

- Lists PENDING labels older than 7 days
- Runs Stage 3 validation methods
- Updates validation_status
- **Frequency**: Weekly

### 4. Manual Review Queue Generator

**File**: `dev.generate_manual_review_queue.py` (to be created)

- Lists NEEDS_REVIEW labels
- Generates review queue with context
- Prioritizes by label type and age
- **Frequency**: Daily

### 5. Time-Based Escalation Script

**File**: `dev.escalate_stale_labels.py` (to be created)

- Finds labels in PENDING/NEEDS_REVIEW older than time limits
- Escalates to next stage
- Updates validation_status
- **Frequency**: Daily

## Monitoring and Metrics

### Key Metrics

1. **Validation Status Distribution**
   - Count of labels in each status (NONE, PENDING, VALID, INVALID, NEEDS_REVIEW)
   - Target: < 1% in PENDING/NEEDS_REVIEW after 30 days

2. **Validation Resolution Rate**
   - Percentage of labels resolved per day
   - Target: > 95% resolved within 7 days

3. **Validation Accuracy**
   - Percentage of VALID labels that are actually correct (sampled)
   - Target: > 99% accuracy

4. **Time to Resolution**
   - Average time from NONE to VALID/INVALID
   - Target: < 7 days for 95% of labels

5. **Escalation Rate**
   - Percentage of labels requiring manual review
   - Target: < 5% of all labels

### Dashboard

Create a dashboard showing:
- Validation status distribution (pie chart)
- Resolution rate over time (line chart)
- Labels by priority tier (bar chart)
- Time to resolution distribution (histogram)
- Manual review queue size (number)

## Decision Flowchart

```
Start: Label created (NONE)
  │
  ▼
Stage 1: CoVe + ChromaDB
  │
  ├─→ VALID ────────────────┐
  ├─→ INVALID ──────────────┤
  └─→ PENDING ───────────────┤
                              │
                              ▼
                    Stage 2: Batch Validation
                              │
                              ├─→ VALID ───────────────┐
                              ├─→ INVALID ─────────────┤
                              ├─→ NEEDS_REVIEW ────────┤
                              └─→ PENDING ─────────────┤
                                                        │
                                                        ▼
                                              Stage 3: Escalated Validation
                                                        │
                                                        ├─→ VALID ───────────┐
                                                        ├─→ INVALID ─────────┤
                                                        └─→ NEEDS_REVIEW ────┤
                                                                             │
                                                                             ▼
                                                                   Stage 4: Manual Review
                                                                             │
                                                                             ├─→ VALID (FINAL)
                                                                             └─→ INVALID (FINAL)
```

## Example Workflow

### Example 1: Successful Automatic Validation

1. **Label Created**: "42.14" labeled as `GRAND_TOTAL` (NONE)
2. **Stage 1 - CoVe**: Verifies label is correct → **VALID**
3. **Result**: Label marked as VALID, no further action needed

### Example 2: ChromaDB Validation

1. **Label Created**: "39.29" labeled as `SUBTOTAL` (NONE)
2. **Stage 1 - CoVe**: Inconclusive → PENDING
3. **Stage 1 - ChromaDB**: Finds 5 similar words with VALID `SUBTOTAL` labels, similarity 0.82 → **VALID**
4. **Result**: Label marked as VALID

### Example 3: Conflict Detection

1. **Label Created**: "17.89" labeled as `GRAND_TOTAL` (NONE)
2. **Stage 1 - CoVe**: Inconclusive → PENDING
3. **Stage 1 - ChromaDB**: Finds similar words with `SUBTOTAL` labels, similarity 0.68 → **INVALID**
4. **Result**: Label marked as INVALID (conflicting evidence)

### Example 4: Manual Review

1. **Label Created**: "ABC123" labeled as `DATE` (NONE)
2. **Stage 1 - CoVe**: Inconclusive → PENDING
3. **Stage 1 - ChromaDB**: No similar words found → PENDING
4. **Stage 2 - Batch Validation**: Inconclusive → PENDING
5. **Stage 3 - Escalated Validation**: Inconclusive → NEEDS_REVIEW
6. **Stage 4 - Manual Review**: Human reviewer determines it's not a date → **INVALID**
7. **Result**: Label marked as INVALID (final)

## Best Practices

### 1. Always Have a Path to Resolution

Every label should have a clear path from its current state to VALID or INVALID. No label should be stuck indefinitely.

### 2. Prioritize Critical Labels

Critical labels (GRAND_TOTAL, MERCHANT_NAME, DATE, TAX) should be validated first and escalated quickly if unresolved.

### 3. Use Multiple Validation Methods

Don't rely on a single validation method. Use multiple methods and combine results for better accuracy.

### 4. Track Validation History

Store validation history for each label:
- Which methods were used
- What results each method returned
- Confidence scores
- Timestamps

### 5. Learn from Manual Reviews

Use manual review decisions to improve automatic validation:
- Train models on manual review data
- Adjust thresholds based on review outcomes
- Identify patterns in labels requiring review

### 6. Monitor and Alert

Set up alerts for:
- High percentage of PENDING labels (> 10%)
- Labels stuck in PENDING > 30 days
- High escalation rate (> 10%)
- Validation accuracy drops below threshold

## Future Enhancements

1. **Machine Learning Model**: Train a model to predict VALID/INVALID based on validation history
2. **Active Learning**: Prioritize labels that would improve the model if reviewed
3. **Confidence Calibration**: Calibrate confidence scores to match actual accuracy
4. **Automated Threshold Tuning**: Automatically adjust thresholds based on validation outcomes
5. **Merchant-Specific Models**: Train merchant-specific validation models for better accuracy

## Related Documentation

- [Chain of Verification (CoVe) Implementation](./CHAIN_OF_VERIFICATION.md)
- [ChromaDB Similarity Search Validation](./CHROMADB_VALIDATION.md)
- [PENDING Labels Best Practices](../../docs/PENDING_LABELS_BEST_PRACTICES.md)

