# Receipt Metadata Finder: Improvements Over Place ID Finder

## Overview

The **Receipt Metadata Finder** is an improved version of the Place ID Finder that finds **ALL missing metadata** for receipts, not just place_ids.

## Key Improvements

### 1. Comprehensive Metadata Finding ✅

**Before (Place ID Finder)**:
- Only found `place_id`
- Updated other fields as a side effect

**Now (Receipt Metadata Finder)**:
- Finds ALL missing metadata:
  - `place_id` (Google Place ID)
  - `merchant_name` (business name)
  - `address` (formatted address)
  - `phone_number` (phone number)
- Can fill in partial metadata intelligently

### 2. Intelligent Source Priority ✅

**Before**:
- Google Places API was primary source
- Receipt content was secondary

**Now**:
- **Receipt content (PRIMARY)**: Extract from labels (MERCHANT_NAME, ADDRESS, PHONE) or lines
- **Google Places (SECONDARY)**: Validate and fill in missing fields
- **Similar receipts (VERIFICATION)**: Verify findings

This is more reliable because the receipt itself is the most accurate source!

### 3. Source Tracking ✅

**New Feature**:
- Tracks where each field came from:
  - `receipt_content`: Extracted from receipt labels/lines
  - `google_places`: From Google Places API
  - `similar_receipts`: From similar receipts
- Helps with debugging and quality assessment

### 4. Partial Fills ✅

**Before**:
- All-or-nothing: If place_id couldn't be found, nothing was updated

**Now**:
- Can fill in some fields even if others can't be found
- Example: Can find merchant_name and address even if place_id can't be found
- Better than leaving everything empty

### 5. Better Submission Tool ✅

**Before**:
- `submit_place_id`: Only submitted place_id

**Now**:
- `submit_metadata`: Submits ALL fields with:
  - Per-field confidence scores
  - Source tracking for each field
  - Reasoning for each field
  - Overall confidence

## Example Workflow

### Receipt with Missing Metadata

**Input**:
- `place_id`: Missing
- `merchant_name`: Missing
- `address`: "123 Main St"
- `phone_number`: Missing

**Agent Process**:
1. Examines receipt content
2. Extracts merchant_name from MERCHANT_NAME label: "Joe's Pizza"
3. Uses address to search Google Places
4. Finds place_id and phone_number from Google Places
5. Submits ALL fields found

**Output**:
- `place_id`: "ChIJ..." ✅
- `merchant_name`: "Joe's Pizza" ✅ (from receipt_content)
- `address`: "123 Main St, City, State, USA" ✅ (from receipt, validated by Google)
- `phone_number`: "(555) 123-4567" ✅ (from google_places)

## Usage

```python
from receipt_agent.tools.receipt_metadata_finder import ReceiptMetadataFinder

finder = ReceiptMetadataFinder(
    dynamo_client=dynamo,
    places_client=places,
    chroma_client=chroma,
    embed_fn=embed_fn,
)

# Find all missing metadata
report = await finder.find_all_metadata_agentic()
finder.print_summary(report)

# Apply fixes
result = await finder.apply_fixes(dry_run=False)
```

## Test Script

```bash
# Find metadata for receipts with missing fields
python scripts/test_receipt_metadata_finder.py --limit 10

# Apply fixes (dry run)
python scripts/test_receipt_metadata_finder.py --apply --dry-run

# Actually apply fixes
python scripts/test_receipt_metadata_finder.py --apply
```

## Migration from Place ID Finder

The old `PlaceIdFinder` is still available for backward compatibility, but `ReceiptMetadataFinder` is recommended because it:
- Finds more metadata
- Uses receipt content as primary source (more reliable)
- Can fill in partial metadata
- Tracks sources for better debugging

## Summary

| Feature | Place ID Finder | Receipt Metadata Finder ⭐ |
|---------|----------------|---------------------------|
| Finds place_id | ✅ | ✅ |
| Finds merchant_name | ❌ (side effect) | ✅ (primary) |
| Finds address | ❌ (side effect) | ✅ (primary) |
| Finds phone | ❌ (side effect) | ✅ (primary) |
| Extracts from receipt | ❌ | ✅ (PRIMARY source) |
| Source tracking | ❌ | ✅ |
| Partial fills | ❌ | ✅ |
| Per-field confidence | ❌ | ✅ |

The Receipt Metadata Finder is a comprehensive upgrade! 🎯


