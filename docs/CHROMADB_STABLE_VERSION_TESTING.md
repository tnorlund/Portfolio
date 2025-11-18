# ChromaDB Stable Version Testing Plan

## Version Analysis

### Available Versions
- **1.0.x series**: 1.0.0 → 1.0.21 (21 patch releases - well-maintained, likely stable)
- **1.1.x series**: 1.1.0 → 1.1.1 (only 1 patch - less mature)
- **1.2.x series**: 1.2.0 → 1.2.2 (2 patches - moderate maturity)
- **1.3.x series**: 1.3.0 → 1.3.4 (current, has issues)

### Recommendation: **ChromaDB 1.0.21**

**Why 1.0.21?**
- ✅ Latest in the 1.0.x series (21 patch releases = well-tested)
- ✅ Original version (1.0.0) was working
- ✅ Likely has bug fixes without breaking changes
- ✅ More stable than 1.3.x (which introduced our issues)

**Alternative: ChromaDB 1.2.2**
- ⚠️ Newer than 1.0.x but older than 1.3.x
- ⚠️ May have some improvements without 1.3.x issues
- ⚠️ Less tested than 1.0.21

## Testing Plan

### Step 1: Update Dependencies

**Update `receipt_label/pyproject.toml`**:
```toml
# Change from:
"chromadb>=1.3.3",

# To:
"chromadb==1.0.21",  # Pin to stable version
```

**Update `receipt_label/pyproject.toml` (lambda client)**:
```toml
# Change from:
"chromadb-client>=1.3.3",

# To:
"chromadb-client==1.0.21",  # Pin to stable version
```

### Step 2: Clean Environment

Since you're re-ingesting everything, clean the environment:

```bash
# Clean S3 snapshots
python3 dev.cleanup_for_testing.py --env dev

# Reset batch summaries
python3 dev.reset_word_embeddings.py --env dev
```

### Step 3: Install New Version Locally (for testing)

```bash
cd receipt_label
pip install -e '.[full]'  # This will install chromadb==1.0.21
```

### Step 4: Deploy to Dev

```bash
cd infra
pulumi up --stack tnorlund/portfolio/dev
```

This will:
- Build new Docker images with ChromaDB 1.0.21
- Deploy Lambda functions with new layer versions
- Update container images

### Step 5: Test Fresh Ingestion

```bash
# Run word embedding ingestion
./scripts/start_ingestion_dev.sh word

# Monitor execution
aws stepfunctions list-executions \
  --state-machine-arn "arn:aws:states:us-east-1:681647709217:stateMachine:word-ingest-sf-dev-8fa425a" \
  --region us-east-1 \
  --max-results 1
```

### Step 6: Verify Results

**Check execution results**:
```bash
# Get latest execution ARN
EXEC_ARN=$(aws stepfunctions list-executions \
  --state-machine-arn "arn:aws:states:us-east-1:681647709217:stateMachine:word-ingest-sf-dev-8fa425a" \
  --region us-east-1 \
  --max-results 1 \
  --query "executions[0].executionArn" \
  --output text)

# Check results
aws stepfunctions describe-execution \
  --execution-arn "$EXEC_ARN" \
  --region us-east-1 \
  --query "output" \
  --output json | python3 -c "
import sys, json
data = json.load(sys.stdin)
fm = data.get('final_merge_result', {})
print(f\"Status: {fm.get('statusCode')}\")
print(f\"Total embeddings: {fm.get('total_embeddings')}\")
results = data.get('chunk_results', [])
success = [r for r in results if r.get('statusCode') == 200 or 'intermediate_key' in r]
failed = [r for r in results if r.get('statusCode') != 200 and 'intermediate_key' not in r]
print(f\"Success: {len(success)}/{len(results)}\")
print(f\"Failed: {len(failed)}/{len(results)}\")
"
```

**Expected Results with 1.0.21**:
- ✅ All 4 chunk groups succeed (100% success rate)
- ✅ No SQLite locking errors
- ✅ No HNSW corruption errors
- ✅ Final merge completes successfully

## Comparison: 1.0.21 vs 1.3.3

| Feature | 1.0.21 (Recommended) | 1.3.3 (Current) |
|---------|----------------------|------------------|
| Stability | ✅ High (21 patches) | ❌ Low (issues) |
| SQLite Locking | ✅ Should work | ❌ Has issues |
| HNSW Corruption | ✅ Should work | ❌ Has issues |
| Features | Basic | Latest features |
| Database Format | Older format | Newer format |

## Rollback Plan

If 1.0.21 doesn't work or has other issues:

1. **Try 1.2.2** (middle ground):
   ```toml
   "chromadb==1.2.2",
   ```

2. **Or go back to 1.3.3** with our workarounds:
   ```toml
   "chromadb>=1.3.3",
   ```

## Next Steps

1. ✅ Update `pyproject.toml` to pin ChromaDB 1.0.21
2. ✅ Clean S3 and reset batch summaries
3. ✅ Deploy to dev
4. ✅ Run fresh ingestion
5. ✅ Monitor results
6. ✅ Compare with 1.3.3 results

## Questions to Answer

1. **Does 1.0.21 eliminate SQLite locking issues?**
2. **Does 1.0.21 eliminate HNSW corruption?**
3. **Are there any missing features in 1.0.21 we need?**
4. **Is performance acceptable?**

## Conclusion

Since you're re-ingesting everything, testing with ChromaDB 1.0.21 is a great opportunity to:
- ✅ Verify if issues are version-specific
- ✅ Test with a known-stable version
- ✅ Avoid workarounds if not needed
- ✅ Establish a baseline for comparison

