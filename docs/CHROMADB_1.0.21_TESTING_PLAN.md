# ChromaDB 1.0.21 Testing Plan

## Changes to Make

### 1. Update Dependencies (`receipt_label/pyproject.toml`)

**Change**:
```toml
# From:
"chromadb>=1.3.3",
"chromadb-client>=1.3.3",

# To:
"chromadb==1.0.21",
"chromadb-client==1.0.21",
```

### 2. Revert Workarounds in `compaction.py`

**What to Revert** (test if 1.0.21 needs them):
- ❌ Improved `close_chromadb_client()` function (lines 94-163) - revert to simple version or remove
- ❌ Main client flush after chunk merge (lines 1253-1284)
- ❌ Main client flush after closing chunk client (lines 1318-1346)
- ❌ Enhanced file verification before upload (lines 924-1041) - revert to simple SQLite check
- ✅ Keep: Empty snapshot initialization (in `chroma_s3_helpers.py`) - useful feature
- ✅ Keep: Enhanced error logging - useful for debugging

**What to Keep**:
- Empty snapshot initialization (useful regardless of version)
- Basic error logging improvements

### 3. Clean Environment

```bash
# Clean S3 snapshots and reset batch summaries
python3 dev.cleanup_for_testing.py --env dev --stop-executions
```

### 4. Deploy

```bash
cd infra
pulumi up --stack tnorlund/portfolio/dev
```

### 5. Test Step Function

```bash
# Run word embedding ingestion
./scripts/start_ingestion_dev.sh word

# Monitor execution
aws stepfunctions list-executions \
  --state-machine-arn "arn:aws:states:us-east-1:681647709217:stateMachine:word-ingest-sf-dev-8fa425a" \
  --region us-east-1 \
  --max-results 1 \
  --query "executions[0].{status:status, startDate:startDate}"
```

## Testing Checklist

- [ ] Update pyproject.toml to ChromaDB 1.0.21
- [ ] Revert workarounds in compaction.py
- [ ] Clean S3 and reset batch summaries
- [ ] Deploy to dev
- [ ] Run step function
- [ ] Check if all 4 groups succeed
- [ ] Verify no SQLite locking errors
- [ ] Verify no HNSW corruption errors

## Expected Results with 1.0.21

**If 1.0.21 works correctly**:
- ✅ All 4 chunk groups succeed (100% success rate)
- ✅ No SQLite locking errors
- ✅ No HNSW corruption errors
- ✅ No workarounds needed

**If 1.0.21 still has issues**:
- ⚠️ May need to keep some workarounds
- ⚠️ Or try 1.2.2 as alternative
- ⚠️ Or go back to 1.3.3 with workarounds

## Rollback Plan

If 1.0.21 doesn't work:
1. Revert pyproject.toml back to 1.3.3
2. Restore workarounds in compaction.py
3. Redeploy

