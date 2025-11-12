# Best Practices for Handling PENDING Labels

## Overview

After running the CoVe-enabled LangGraph workflow, labels are marked as:
- **`VALID`**: CoVe successfully verified the label (high confidence)
- **`PENDING`**: CoVe didn't verify the label (either CoVe failed, or verification wasn't conclusive)

This document outlines best practices for handling `PENDING` labels.

## Current State

### What PENDING Means

A `PENDING` label indicates:
1. **CoVe verification failed** - The verification process encountered an error
2. **CoVe couldn't verify** - The verification questions couldn't be answered conclusively
3. **Initial extraction only** - The label was extracted but not yet verified

**Important**: `PENDING` does NOT mean the label is wrong - it just means it hasn't been verified yet.

### Validation Status Lifecycle

```
NONE → PENDING → VALID (CoVe verified)
                ↓
            INVALID (manual review or batch validation)
                ↓
            NEEDS_REVIEW (uncertain cases)
```

## Best Practices

### 1. **Tiered Validation Strategy** (Recommended)

Use a multi-tier approach based on label importance and confidence:

#### Tier 1: Critical Labels (Immediate Review)
- **Labels**: `GRAND_TOTAL`, `MERCHANT_NAME`, `DATE`, `TAX`
- **Action**: Re-run CoVe with stricter parameters or manual review
- **Priority**: High

#### Tier 2: Important Labels (Batch Validation)
- **Labels**: `SUBTOTAL`, `LINE_TOTAL`, `PRODUCT_NAME`, `QUANTITY`, `UNIT_PRICE`
- **Action**: Use OpenAI Batch API validation (existing `completion` workflow)
- **Priority**: Medium

#### Tier 3: Supporting Labels (Statistical Analysis)
- **Labels**: `PAYMENT_METHOD`, `LOYALTY_ID`, `WEBSITE`, `PHONE_NUMBER`
- **Action**: Cross-receipt validation (merchant-level consistency)
- **Priority**: Low

### 2. **Re-run CoVe with Different Parameters**

For PENDING labels, consider:

```python
# Option A: Re-run with stricter CoVe (more verification questions)
await analyze_receipt_simple(
    ...,
    enable_cove=True,
    cove_verification_questions=10,  # More questions
    cove_threshold=0.9,  # Higher confidence threshold
)

# Option B: Re-run with different LLM model
# Use a larger model (e.g., gpt-oss:120b) for verification
```

### 3. **Use Existing Batch Validation System**

The codebase already has a completion batch validation system:

**Location**: `receipt_label/receipt_label/completion/`

**How it works**:
1. Submit PENDING labels to OpenAI Batch API
2. OpenAI validates labels using function calling
3. Results mark labels as `VALID`, `INVALID`, or `NEEDS_REVIEW`

**Usage**:
```python
from receipt_label.receipt_label.completion.submit import submit_labels_for_validation
from receipt_label.receipt_label.completion.poll import download_openai_batch_result

# Submit PENDING labels
batch_summary = submit_labels_for_validation(
    labels=pending_labels,
    client_manager=client_manager,
)

# Poll for results
pending_to_update, valid_labels, invalid_labels = download_openai_batch_result(
    batch_summary=batch_summary,
    client_manager=client_manager,
)
```

### 4. **Merchant-Level Cross-Validation**

For labels that should be consistent across receipts from the same merchant:

**Location**: `infra/validation_by_merchant/`

**How it works**:
1. Groups receipts by merchant
2. Validates labels across all receipts from that merchant
3. Flags inconsistencies (e.g., different merchant names for same place_id)

**Use cases**:
- `MERCHANT_NAME` consistency
- `ADDRESS_LINE` consistency
- `PHONE_NUMBER` consistency

### 5. **Statistical Analysis & Pattern Detection**

Identify patterns in PENDING labels:

```python
# Example: Analyze PENDING labels by type
pending_by_label_type = {}
for label in pending_labels:
    pending_by_label_type.setdefault(label.label, []).append(label)

# Identify problematic label types
problematic_types = [
    label_type for label_type, labels in pending_by_label_type.items()
    if len(labels) > threshold
]
```

**Actions**:
- If many `GRAND_TOTAL` labels are PENDING → Check currency extraction logic
- If many `PRODUCT_NAME` labels are PENDING → Check line item parsing
- If many labels from specific merchants are PENDING → Check receipt format

### 6. **Confidence-Based Filtering**

Use confidence scores to prioritize:

```python
# High confidence PENDING labels (likely correct, just not verified)
high_confidence_pending = [
    label for label in pending_labels
    if getattr(label, 'confidence', 0) > 0.8
]

# Low confidence PENDING labels (need review)
low_confidence_pending = [
    label for label in pending_labels
    if getattr(label, 'confidence', 0) < 0.5
]
```

**Strategy**:
- **High confidence PENDING**: Accept as-is or re-run CoVe once
- **Low confidence PENDING**: Manual review or batch validation

### 7. **Time-Based Re-validation**

Re-validate PENDING labels periodically:

```python
# Re-validate PENDING labels older than 30 days
old_pending = [
    label for label in pending_labels
    if (datetime.now() - label.timestamp_added).days > 30
]
```

**Rationale**:
- CoVe models may improve over time
- New validation techniques may become available
- Receipt patterns may change

## Recommended Workflow

### Phase 1: Immediate (After Overnight Run)

1. **Generate Report**
   ```bash
   python dev.analyze_pending_labels.py --stack dev --output pending_report.json
   ```

2. **Identify Critical Issues**
   - Receipts with no VALID labels
   - Receipts with PENDING `GRAND_TOTAL`
   - High percentage of PENDING labels

3. **Quick Wins**
   - Re-run CoVe on high-confidence PENDING labels
   - Fix obvious issues (e.g., missing metadata)

### Phase 2: Short-term (Week 1)

1. **Batch Validation**
   - Submit critical PENDING labels to OpenAI Batch API
   - Process results and update validation_status

2. **Merchant-Level Validation**
   - Run validation_by_merchant workflow
   - Fix inconsistencies

3. **Pattern Analysis**
   - Identify common failure modes
   - Update CoVe prompts/parameters

### Phase 3: Long-term (Ongoing)

1. **Continuous Monitoring**
   - Track PENDING label rate over time
   - Alert on spikes

2. **Model Improvement**
   - Use PENDING labels as training data
   - Improve CoVe verification questions

3. **Automated Re-validation**
   - Schedule periodic re-validation of old PENDING labels
   - Use improved models/techniques

## Implementation Scripts

### Script 1: Analyze PENDING Labels

```python
# dev.analyze_pending_labels.py
# - Lists all PENDING labels
# - Groups by label type, merchant, receipt
# - Calculates statistics
# - Identifies patterns
```

### Script 2: Re-validate PENDING Labels

```python
# dev.revalidate_pending_labels.py
# - Re-runs CoVe on PENDING labels
# - Uses stricter parameters
# - Updates validation_status
```

### Script 3: Submit PENDING to Batch Validation

```python
# dev.submit_pending_to_batch.py
# - Submits PENDING labels to OpenAI Batch API
# - Monitors batch status
# - Processes results
```

## Metrics to Track

1. **PENDING Rate**: `PENDING / (VALID + PENDING)`
   - Target: < 20%
   - Alert if > 40%

2. **Critical Label PENDING Rate**: PENDING rate for `GRAND_TOTAL`, `MERCHANT_NAME`, etc.
   - Target: < 5%
   - Alert if > 15%

3. **CoVe Success Rate**: `VALID / (VALID + PENDING)`
   - Target: > 80%
   - Alert if < 60%

4. **Re-validation Success Rate**: How many PENDING labels become VALID after re-validation
   - Target: > 50%

## Decision Tree

```
Is label PENDING?
├─ Is it a CRITICAL label (GRAND_TOTAL, MERCHANT_NAME)?
│  ├─ Yes → Re-run CoVe with stricter parameters
│  │        ├─ Still PENDING? → Manual review
│  │        └─ VALID? → Done
│  └─ No → Continue
├─ Is confidence > 0.8?
│  ├─ Yes → Accept or re-run CoVe once
│  └─ No → Continue
├─ Is it part of a merchant with many PENDING labels?
│  ├─ Yes → Run merchant-level validation
│  └─ No → Continue
└─ Submit to batch validation
   ├─ VALID → Done
   ├─ INVALID → Mark as INVALID
   └─ NEEDS_REVIEW → Manual review
```

## Next Steps

1. **Create analysis script** (`dev.analyze_pending_labels.py`)
   - Generate statistics and reports
   - Identify patterns

2. **Create re-validation script** (`dev.revalidate_pending_labels.py`)
   - Re-run CoVe on PENDING labels
   - Update validation_status

3. **Integrate with batch validation**
   - Automatically submit high-priority PENDING labels
   - Process results

4. **Set up monitoring**
   - Track PENDING rate over time
   - Alert on anomalies

5. **Document patterns**
   - Keep a log of common failure modes
   - Update CoVe prompts based on findings

