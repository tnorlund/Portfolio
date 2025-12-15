# Duplicate Images Merge Strategy

## Overview

After analyzing 1,189 images in the dev environment, we identified **955 duplicate images** (80.3% duplicates).

- ✅ **898 images** were safely deleted (no labels to merge)
- ⚠️ **7 groups** (8 images) require label merging before deletion

## Remaining Duplicate Groups Requiring Label Merge

### Group 1: 3 images
- **Keep**: `965c73ca-e68b-4afe-a42c-c41732e48ef4` (13 labels)
- **Delete**:
  - `fb69b028-a9d1-4d27-b30f-6e5f1502e2ac` (12 labels)
  - `a6596a97-4ce5-49ab-876d-6a2dfaeffca4` (12 labels)
- **Merge Strategy**:
  - Add 1 new label
  - No label conflicts (different label types, will be added as separate entries)

### Group 2: 2 images
- **Keep**: `e5e5dbf2-c31a-4b41-a80f-af81c6f1b4d1` (104 labels)
- **Delete**: `b81f388d-5c6d-459e-a5c5-d38007950ba5` (3 labels)
- **Merge Strategy**:
  - Add 3 new labels from delete image (all will be added as separate entries since they have different label types)

### Group 3: 2 images
- **Keep**: `5985d2dd-462e-4ccb-928c-4f4d596d6be2` (148 labels)
- **Delete**: `223c03e2-9f7e-481d-bc33-2b0631fbaaf9` (50 labels)
- **Merge Strategy**:
  - Add 9 new labels as separate entries
  - Example: Word (2,65,1) will have both `LINE_TOTAL` (VALID) and `UNIT_PRICE` (VALID) as separate label entries
  - Example: Word (2,77,1) will have `ITEM_TOTAL` (INVALID), `GRAND_TOTAL` (VALID), and `TOTAL` (INVALID) as separate entries
  - **INVALID labels preserved** as separate entries

### Group 4: 2 images
- **Keep**: `95ae3b59-238c-4725-a2b0-d428d09bb82a` (4 labels)
- **Delete**: `739dfe4b-edac-49b1-86c3-3568a7c98492` (4 labels)
- **Merge Strategy**:
  - Add 4 new labels from delete image (all will be added as separate entries since they have different label types)

### Group 5: 2 images
- **Keep**: `4c5ba3ff-a7c1-49a3-bb91-4e6d75bf1ebb` (78 labels)
- **Delete**: `5492b016-cc08-4d57-9a64-d6775684361c` (48 labels)
- **Merge Strategy**:
  - Add 16 new labels as separate entries
  - Example: Word (1,2,1) will have both `BUSINESS_NAME` (INVALID) and `MERCHANT_NAME` (INVALID) as separate entries
  - Example: Word (1,3,1) will have both `BUSINESS_NAME` (INVALID) and `MERCHANT_NAME` (VALID) as separate entries
  - **INVALID labels preserved** as separate entries

### Group 6: 2 images
- **Keep**: `1746d34d-69e2-4764-ae52-4fc444e0a531` (116 labels)
- **Delete**: `49a1a1e7-348a-4cd9-af96-2312e838476e` (22 labels)
- **Merge Strategy**:
  - Add 4 new labels as separate entries
  - Example: Multiple words will have both `ITEM_TOTAL` (INVALID) and `UNIT_PRICE` (INVALID) as separate entries
  - **INVALID labels preserved** as separate entries

### Group 7: 2 images
- **Keep**: `645a8dcb-ee5e-4207-9b73-567ebd1ccc45` (53 labels)
- **Delete**: `0d82e9a0-fac2-4bda-811f-bcb32ebfcb33` (20 labels)
- **Merge Strategy**:
  - Add 8 new labels as separate entries
  - Example: Word (1,27,2) will have both `GRAND_TOTAL` (VALID) and `SUBTOTAL` (VALID) as separate entries
  - Example: Word (1,37,1) will have both `ITEM_TOTAL` (INVALID) and `UNIT_PRICE` (VALID) as separate entries
  - **INVALID labels preserved** as separate entries

## Merge Strategy Details

### How Labels Are Preserved

The merge strategy preserves **INVALID labels** because they're valuable for training and analysis. The strategy works as follows:

1. **Different label types** (e.g., `LINE_TOTAL` vs `UNIT_PRICE`):
   - Both labels are added as separate `ReceiptWordLabel` entries
   - Each word can have multiple labels with different label types
   - This preserves all INVALID labels

2. **Same label type, different validation status**:
   - Only updates if the new label has a higher validation status priority
   - Validation status priority: `VALID` > `NEEDS_REVIEW` > `PENDING` > `NONE` > `INVALID`

3. **Same label type, same validation status**:
   - Keeps existing label (no update needed)

### Validation Status Priority

```
VALID (5) > NEEDS_REVIEW (4) > PENDING (3) > NONE (2) > INVALID (1)
```

### Why Keep INVALID Labels?

INVALID labels are valuable because:
- They represent validation failures that can be used for model improvement
- They show what the model incorrectly predicted
- They provide negative examples for training
- They help understand edge cases and failure modes

## Execution

To execute the merge and deletion:

```bash
# Dry run (review changes)
python dev.merge_labels_and_delete.py

# Execute merge and deletion
python dev.merge_labels_and_delete.py --execute
```

## Summary

- **Total groups needing merge**: 7
- **Total images to delete after merge**: 8
- **Labels to add**: ~38 new label entries
- **Labels to update**: 0 (all conflicts resolved by adding separate entries)

### Images to Delete (after merge)

1. `fb69b028-a9d1-4d27-b30f-6e5f1502e2ac` (Group 1)
2. `a6596a97-4ce5-49ab-876d-6a2dfaeffca4` (Group 1)
3. `b81f388d-5c6d-459e-a5c5-d38007950ba5` (Group 2)
4. `223c03e2-9f7e-481d-bc33-2b0631fbaaf9` (Group 3)
5. `739dfe4b-edac-49b1-86c3-3568a7c98492` (Group 4)
6. `5492b016-cc08-4d57-9a64-d6775684361c` (Group 5)
7. `49a1a1e7-348a-4cd9-af96-2312e838476e` (Group 6)
8. `0d82e9a0-fac2-4bda-811f-bcb32ebfcb33` (Group 7)

### Images to Keep (after merge)

1. `965c73ca-e68b-4afe-a42c-c41732e48ef4` (Group 1)
2. `e5e5dbf2-c31a-4b41-a80f-af81c6f1b4d1` (Group 2)
3. `5985d2dd-462e-4ccb-928c-4f4d596d6be2` (Group 3)
4. `95ae3b59-238c-4725-a2b0-d428d09bb82a` (Group 4)
5. `4c5ba3ff-a7c1-49a3-bb91-4e6d75bf1ebb` (Group 5)
6. `1746d34d-69e2-4764-ae52-4fc444e0a531` (Group 6)
7. `645a8dcb-ee5e-4207-9b73-567ebd1ccc45` (Group 7)

After merging and deletion:
- **Unique images remaining**: 283 (down from 1,189)
- **All INVALID labels preserved** as separate entries
- **All VALID labels preserved** as separate entries

