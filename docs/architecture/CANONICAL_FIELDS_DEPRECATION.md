# Canonical Fields Deprecation Plan

## Overview

This document outlines the plan to deprecate and remove canonical fields (`canonical_merchant_name`, `canonical_address`, `canonical_phone_number`, `canonical_place_id`) from the `ReceiptMetadata` entity.

## What Are Canonical Fields?

Canonical fields were introduced to store "harmonized" values across all receipts with the same `place_id`. The idea was that when multiple receipts are associated with the same physical business location (same `place_id`), they should share consistent metadata values.

### Current Canonical Fields

- `canonical_merchant_name`: Harmonized merchant name from the most representative business in the cluster
- `canonical_address`: Normalized canonical address from the most representative business in the cluster
- `canonical_phone_number`: Canonical phone number from the most representative business in the cluster
- `canonical_place_id`: Canonical place ID from the most representative business in the cluster

## Historical Context

### Why They Were Created

Canonical fields were introduced as part of a **rule-based harmonization system** (`MerchantHarmonizerV2`) that attempted to:
1. Cluster receipts by `place_id`
2. Determine a "canonical" representative record from each cluster
3. Copy values from the canonical record to all other records in the cluster

This approach had several problems:
- **Rule-based logic was brittle**: Complex heuristics for determining "most representative" records often failed
- **Poor results**: The harmonization didn't produce reliable, consistent results
- **Maintenance burden**: Two sets of fields to maintain (base + canonical)

### Current State

The system has moved to an **agent-based harmonizer** (`LabelHarmonizerV2` / `ReceiptMetadataFinderWorkflow`) that:
- Uses LLM agents to intelligently determine correct metadata values
- Validates against receipt text using CoVe (Consistency Verification)
- Updates base fields (`merchant_name`, `address`, `phone_number`) directly
- Uses `place_id` as the source of truth for grouping receipts

## Current Usage of Canonical Fields

### 1. GSI Indexing (`gsi1_key()`)

**Location**: `receipt_dynamo/receipt_dynamo/entities/receipt_metadata.py:233-264`

```python
def gsi1_key(self) -> Dict[str, Any]:
    """
    Uses canonical_merchant_name if available (preferred), otherwise
    falls back to merchant_name.
    """
    merchant_name_to_use = (
        self.canonical_merchant_name
        if self.canonical_merchant_name
        else self.merchant_name
    )
```

**Impact**: Low - Already has fallback to `merchant_name`

### 2. Label Validation

**Location**: `receipt_label/receipt_label/label_validation/validate_merchant_name.py:205-207`

```python
normalized_canonical = normalize_text(
    metadata.canonical_merchant_name or ""
)
```

**Location**: `receipt_label/receipt_label/label_validation/validate_address.py:146-150`

```python
canonical_address = (
    _normalize_address(receipt_metadata.canonical_address)
    if receipt_metadata.canonical_address
    else ""
)
```

**Impact**: Medium - Needs to be updated to use base fields

### 3. Clustering Logic (Legacy)

**Location**: `receipt_label/receipt_label/merchant_validation/clustering.py`

Various functions that set canonical fields from clusters. This is the old rule-based system.

**Impact**: Low - This is legacy code that may not be actively used

## Why Remove Canonical Fields?

### 1. Redundancy

With the agent-based harmonizer:
- Base fields (`merchant_name`, `address`, `phone_number`) are now the source of truth
- They are harmonized correctly by the LLM agent
- Canonical fields duplicate this information

### 2. Source of Truth Confusion

Having two sets of fields creates ambiguity:
- Which field should be used?
- Are they always in sync?
- What happens when they differ?

### 3. Maintenance Burden

- Two sets of fields to update
- Two sets of fields to validate
- Two sets of fields to query
- Risk of them getting out of sync

### 4. `place_id` is the Real Grouping Mechanism

The system now uses `place_id` as the authoritative way to group receipts:
- All receipts with the same `place_id` are from the same physical location
- The harmonizer ensures base fields are consistent within a `place_id` group
- No need for separate "canonical" values

## Migration Plan

### Phase 1: Update Harmonizer (Current)

**Status**: ✅ **COMPLETE** (but updating canonical fields, not base fields)

The harmonizer currently updates canonical fields. We need to:
1. ✅ Update harmonizer to update **base fields** instead of canonical fields
2. ✅ Ensure base fields are the source of truth

**File**: `infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py`

**Action**: Change updates from canonical fields to base fields:
- `metadata.canonical_merchant_name` → `metadata.merchant_name`
- `metadata.canonical_address` → `metadata.address`
- `metadata.canonical_phone_number` → `metadata.phone_number`

### Phase 2: Update Consumers

**Status**: ⏳ **PENDING**

Update all code that reads canonical fields to use base fields instead:

1. **Label Validation** (`receipt_label/receipt_label/label_validation/`)
   - `validate_merchant_name.py`: Use `metadata.merchant_name` instead of `metadata.canonical_merchant_name`
   - `validate_address.py`: Use `metadata.address` instead of `metadata.canonical_address`

2. **GSI Indexing** (`receipt_dynamo/receipt_dynamo/entities/receipt_metadata.py`)
   - `gsi1_key()`: Remove canonical field check, use `merchant_name` directly
   - This is already safe since it has a fallback

3. **Any other consumers**
   - Search codebase for `canonical_merchant_name`, `canonical_address`, `canonical_phone_number`
   - Update to use base fields

### Phase 3: Remove Canonical Fields from Entity

**Status**: ⏳ **PENDING**

1. Remove canonical fields from `ReceiptMetadata` dataclass
2. Remove from serialization/deserialization logic
3. Remove from DynamoDB schema (if they're indexed)
4. Update all tests

**File**: `receipt_dynamo/receipt_dynamo/entities/receipt_metadata.py`

### Phase 4: Data Migration (Optional)

**Status**: ⏳ **PENDING**

If canonical fields have valuable data that differs from base fields:
1. Identify records where canonical ≠ base
2. Decide which is correct (likely base, since harmonizer updates it)
3. Optionally migrate canonical → base if needed
4. Or simply let harmonizer re-run to fix base fields

## Implementation Checklist

- [ ] **Phase 1**: Update harmonizer to update base fields (not canonical)
  - [ ] Change `harmonize_metadata.py` to update `merchant_name`, `address`, `phone_number`
  - [ ] Verify harmonizer still works correctly
  - [ ] Test with real data

- [ ] **Phase 2**: Update consumers to use base fields
  - [ ] Update `validate_merchant_name.py`
  - [ ] Update `validate_address.py`
  - [ ] Update `gsi1_key()` to use `merchant_name` directly
  - [ ] Search and update any other consumers
  - [ ] Run tests to verify

- [ ] **Phase 3**: Remove canonical fields from entity
  - [ ] Remove from `ReceiptMetadata` dataclass
  - [ ] Remove from `__post_init__` validation
  - [ ] Remove from `__iter__` serialization
  - [ ] Remove from `item_to_receipt_metadata` deserialization
  - [ ] Remove from `gsi1_key()` (already done in Phase 2)
  - [ ] Update all tests

- [ ] **Phase 4**: Data migration (if needed)
  - [ ] Analyze data to see if canonical fields have unique data
  - [ ] Run harmonizer on all records to ensure base fields are correct
  - [ ] Verify no data loss

## Risks and Considerations

### Risk 1: GSI Query Performance

**Risk**: If GSI uses canonical fields, removing them might affect query performance.

**Mitigation**: GSI already falls back to `merchant_name`, so this is safe. We can update GSI to use `merchant_name` directly.

### Risk 2: Label Validation Accuracy

**Risk**: Label validation might be less accurate if it was relying on canonical fields being more "harmonized" than base fields.

**Mitigation**: With the agent-based harmonizer, base fields should be just as accurate (or more accurate) than canonical fields. Test thoroughly.

### Risk 3: Breaking Changes

**Risk**: Removing fields from the entity might break existing code or integrations.

**Mitigation**:
- Complete Phase 2 (update consumers) before Phase 3 (remove fields)
- Run comprehensive tests
- Deploy incrementally

## Success Criteria

1. ✅ Harmonizer updates base fields correctly
2. ✅ All consumers use base fields (no canonical field references)
3. ✅ GSI indexing works with base fields
4. ✅ Label validation works with base fields
5. ✅ No performance degradation
6. ✅ Canonical fields removed from entity
7. ✅ All tests pass

## Timeline

- **Phase 1**: Immediate (fix harmonizer bug)
- **Phase 2**: After Phase 1 is verified in production
- **Phase 3**: After Phase 2 is complete and stable
- **Phase 4**: Optional, can be done anytime

## Questions to Resolve

1. **Are canonical fields indexed in DynamoDB?**
   - Check if removing them requires a schema migration
   - If yes, plan for zero-downtime migration

2. **Are there any external consumers?**
   - APIs that return canonical fields
   - Reports or analytics that use canonical fields
   - External integrations

3. **What about `canonical_place_id`?**
   - This seems even more redundant (we already have `place_id`)
   - Should be removed as well

## References

- `receipt_dynamo/receipt_dynamo/entities/receipt_metadata.py` - Entity definition
- `infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py` - Harmonizer implementation
- `receipt_label/receipt_label/label_validation/` - Label validation consumers
- `receipt_label/receipt_label/merchant_validation/clustering.py` - Legacy clustering logic

