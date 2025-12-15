# Execution 62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6: Missing Embeddings Analysis

## Problem

After execution completed successfully:
- ✅ **459 batches** were marked as COMPLETED
- ✅ **62,455 embeddings** expected (sum of all batch `embedding_count` values)
- ❌ **13,223 embeddings** actually in final snapshot (~21% of expected)
- ❌ **49,232 embeddings missing** (~79% of expected)

## Investigation

### What We Know

1. **All batches have `delta_key`**: All 459 poll results have `delta_key` fields, so they should have been processed into chunks
2. **All batches completed**: All 459 batches have `batch_status == "completed"` and `action == "process_results"`
3. **Expected embeddings**: 62,455 total embeddings across all batches
4. **Actual embeddings**: 13,223 in final snapshot

### Potential Root Causes

#### 1. Chunks Not Created for All Batches
- **Check**: Verify how many chunks were created from 459 batches
- **Expected**: With `CHUNK_SIZE_WORDS = 15`, we'd expect ~31 chunks (459 / 15)
- **Issue**: If chunks weren't created for all batches, those batches wouldn't be merged

#### 2. Chunks Created But Not All Processed
- **Check**: Verify how many chunks were successfully processed
- **Issue**: If some chunks failed during `ProcessChunksInParallel`, they wouldn't create intermediate snapshots

#### 3. Chunks Processed But Not All Merged (Most Likely)
- **Check**: Verify how many chunks/groups were merged in final merge
- **Issue**: If the final merge only processed a subset of chunks, the rest would be lost
- **Possible causes**:
  - Hierarchical merge only processed some groups
  - Chunks were filtered out as "empty" or "invalid"
  - Group size limit (20 chunks per group) caused chunks to be dropped

#### 4. Group Size Limit Issue
- **Check**: With `group_size = 20` and ~31 chunks, we'd have 2 groups (20 + 11)
- **Issue**: If the group creation logic has a bug, some chunks might be dropped
- **Code location**: `create_chunk_groups.py` - check if all chunks are assigned to groups

#### 5. Final Merge Filtering Issue
- **Check**: The `final_merge_handler` filters out chunks without `intermediate_key`
- **Issue**: If chunks were processed but didn't return `intermediate_key`, they'd be filtered out
- **Code location**: `compaction.py` lines 652-692

## Next Steps to Diagnose

### Step 1: Check CloudWatch Logs
```bash
# Check how many chunks were created
aws logs filter-log-events \
  --log-group-name "/aws/lambda/embedding-split-chunks-lambda-dev" \
  --filter-pattern "62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6" \
  --query 'events[*].message' | grep -E "total_chunks|Created chunks"

# Check how many chunks were processed
aws logs filter-log-events \
  --log-group-name "/aws/lambda/embedding-vector-compact-lambda-dev" \
  --filter-pattern "62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6" \
  --query 'events[*].message' | grep -E "process_chunk|intermediate_key"

# Check final merge details
aws logs filter-log-events \
  --log-group-name "/aws/lambda/embedding-vector-compact-lambda-dev" \
  --filter-pattern "62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6" \
  --query 'events[*].message' | grep -E "Final merge|chunk_count|filtered_count|intermediate_key"
```

### Step 2: Check S3 Intermediate Snapshots
```bash
# List all intermediate snapshots created
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/intermediate/62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6/ --recursive

# Count chunks
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/intermediate/62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6/ --recursive | grep "chunk-" | wc -l

# Count groups (if hierarchical merge was used)
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/intermediate/62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6/ --recursive | grep "group-" | wc -l
```

### Step 3: Check Chunk Groups
```bash
# Check if chunk groups were created
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/chunk_groups/62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6/

# Download and inspect groups.json
aws s3 cp s3://chromadb-dev-shared-buckets-vectors-c239843/chunk_groups/62bdeb9f-3252-4ff2-b3dd-6f7d93b577b6/groups.json - | jq '.groups | length'
```

### Step 4: Verify Final Merge Input
The final merge should have received all chunk results. Check if:
- All chunks were included in `chunk_results`
- All chunks had `intermediate_key` fields
- No chunks were filtered out as "empty" or "invalid"

## Root Cause Found: Hardcoded 10-Chunk Limit in Group Merge

**BUG**: `merge_chunk_group_handler` has a hardcoded limit of 10 chunks per group (line 547 in `compaction.py`), but `group_size` was increased to 20 in the Step Function definition.

**Impact**:
- Groups are created with up to 20 chunks
- But `merge_chunk_group_handler` only processes the first 10 chunks
- The remaining chunks in each group are silently dropped!

**Example with ~31 chunks and `group_size = 20`**:
- Group 0: 20 chunks → only 10 processed → **10 chunks lost**
- Group 1: 11 chunks → only 10 processed → **1 chunk lost**
- Total: 21 chunks processed out of 31 (68%)

**Fix Applied**: Removed the hardcoded 10-chunk limit. The handler now processes all chunks in each group.

**Note**: We're seeing 21% of embeddings, which is less than the 68% we'd expect from this bug alone. This suggests there may be additional issues:
1. Some chunks may have failed during processing
2. Some groups may have failed during merge
3. Additional filtering in final merge

## Recommendations

1. **Add logging** to track chunk counts at each stage:
   - After `SplitIntoChunks`: log `total_chunks`
   - After `ProcessChunksInParallel`: log how many chunks succeeded
   - After `CreateChunkGroups`: log how many groups and chunks per group
   - After `MergeChunkGroupsInParallel`: log how many groups succeeded
   - In `final_merge_handler`: log `original_count`, `valid_chunk_results`, and `filtered_count`

2. **Validate chunk coverage**: Ensure all batches are represented in chunks
3. **Validate group coverage**: Ensure all chunks are assigned to groups
4. **Validate merge coverage**: Ensure all groups/chunks are merged in final step

