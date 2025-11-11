# ChromaDB Line Query Testing

## Overview

Created `dev.test_chromadb_line_query.py` to test ChromaDB line queries by:
1. Randomly picking a line from DynamoDB using `list_receipt_lines()`
2. Downloading the ChromaDB "lines" snapshot from S3
3. Querying ChromaDB using the line's ID
4. Comparing the query results with actual IDs in ChromaDB

## Findings

### Empty ChromaDB Collection

**Issue**: The ChromaDB "lines" collection has **0 records** in the snapshot.

```
Total records in 'lines' collection: 0
```

This means:
- No IDs can be queried
- The snapshot is empty or data hasn't been compacted yet
- Possible causes:
  1. The snapshot is empty (no lines have been embedded yet)
  2. The data is in deltas that haven't been compacted into the snapshot yet
  3. The snapshot download didn't include the collection data

### Line ID Format

The script constructs line IDs using the following format:

```
IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}
```

**Example**:
```
IMAGE#cf8e3a6e-0947-4a3d-9bc0-7ce3894c9912#RECEIPT#00002#LINE#00009
```

**Components**:
- `IMAGE#` + UUID image ID
- `RECEIPT#` + zero-padded receipt ID (5 digits, e.g., `00002`)
- `LINE#` + zero-padded line ID (5 digits, e.g., `00009`)

This format matches what the Lambda uses (`infra/routes/address_similarity_cache_generator/lambdas/index.py`).

## Usage

```bash
# Basic usage (auto-detects DynamoDB table and S3 bucket)
python dev.test_chromadb_line_query.py

# Specify stack
python dev.test_chromadb_line_query.py --stack dev

# Override bucket name
python dev.test_chromadb_line_query.py --bucket-name chromadb-dev-shared-buckets-vectors-abc123

# Override DynamoDB table name
python dev.test_chromadb_line_query.py --table-name ReceiptsTable-abc123
```

## Script Features

1. **Random Line Selection**: Randomly picks a line from DynamoDB using `list_receipt_lines()`
2. **Snapshot Download**: Downloads the latest ChromaDB snapshot from S3 using `download_snapshot_atomic()`
3. **ID Listing**: Fetches ALL record IDs from ChromaDB to compare formats
4. **Format Comparison**: Checks if the constructed ID matches any IDs in ChromaDB
5. **Detailed Logging**: Shows:
   - Total records in collection
   - Sample IDs from ChromaDB (first 30)
   - IDs matching the image/receipt combination
   - IDs matching the line_id
   - Exact query ID being tested

## Next Steps

1. **Verify Compaction**: Check if the compaction Lambda has processed deltas into the snapshot
2. **Check Delta Files**: Verify that delta files exist in S3 (`lines/delta/*/delta.tar.gz`)
3. **Trigger Compaction**: If deltas exist but snapshot is empty, manually trigger compaction
4. **Verify Embedding Pipeline**: Ensure lines are being embedded and written to ChromaDB deltas
5. **Check Snapshot Version**: Verify the snapshot pointer is pointing to the correct version

## Related Files

- `dev.test_chromadb_line_query.py` - Test script (ignored by gitignore)
- `infra/routes/address_similarity_cache_generator/lambdas/index.py` - Lambda using same ID format
- `receipt_label/utils/chroma_s3_helpers.py` - Snapshot download utilities
- `infra/chromadb_compaction/` - Compaction Lambda implementation

