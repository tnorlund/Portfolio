# ChromaDB Compaction Migration: Rollback Plan

**Status**: Planning
**Created**: December 15, 2024
**Last Updated**: December 15, 2024

## Overview

This document provides step-by-step instructions for rolling back the compaction logic migration if issues arise.

## When to Rollback

Consider rollback if:

- ❌ **Critical Production Failure**: Compaction operations failing in production
- ❌ **Data Integrity Issues**: Incorrect metadata updates or data loss
- ❌ **Performance Degradation**: Significant slowdown in compaction processing
- ❌ **Import Errors**: Lambda handlers cannot import moved modules
- ❌ **Test Failures**: Major test suite failures that block deployment
- ❌ **Circular Dependencies**: Unresolvable import cycles

Do NOT rollback for:
- ✓ Minor test failures (fix forward)
- ✓ Linting issues (fix forward)
- ✓ Documentation gaps (fix forward)
- ✓ Non-critical bugs (fix forward)

## Rollback Scenarios

### Scenario 1: Rollback Before Deployment

**Situation**: Issues discovered during development/testing, before deploying to any environment.

**Impact**: None - code not deployed

**Steps**:
1. Revert git commits
2. Clean up receipt_chroma changes
3. Verify tests pass

**Time to Rollback**: 5-10 minutes

---

### Scenario 2: Rollback After Dev Deployment

**Situation**: Issues discovered in dev environment after deployment.

**Impact**: Low - only dev environment affected

**Steps**:
1. Deploy previous Lambda version in dev
2. Optionally revert git commits
3. Investigate issues

**Time to Rollback**: 10-15 minutes

---

### Scenario 3: Rollback After Production Deployment

**Situation**: Critical issues discovered in production.

**Impact**: High - production compaction affected

**Steps**:
1. **IMMEDIATE**: Deploy previous Lambda version
2. Monitor recovery
3. Revert git commits
4. Post-mortem analysis

**Time to Rollback**: 5-10 minutes (emergency), 30-60 minutes (full cleanup)

---

## Rollback Procedures

### Quick Rollback (Lambda Only)

Use when Lambda deployment is the issue, but package changes are okay.

#### Step 1: Identify Previous Working Version

```bash
cd infra/

# View Lambda deployment history
pulumi stack history | head -20

# Find last successful deployment before migration
# Note the timestamp or version number
```

#### Step 2: Revert Lambda Handler Imports

```bash
cd chromadb_compaction/lambdas/

# Revert main handler file
git checkout HEAD~1 enhanced_compaction_handler.py

# Revert orchestration wrappers
git checkout HEAD~1 compaction/metadata_handler.py
git checkout HEAD~1 compaction/label_handler.py

# Verify files reverted
git status
```

#### Step 3: Redeploy Lambda

```bash
cd ../../  # Back to infra/

# Deploy with reverted handlers
pulumi up --yes

# Monitor deployment
pulumi logs --follow
```

#### Step 4: Verify Recovery

```bash
# Check Lambda execution
pulumi logs --since 5m | grep ERROR

# Monitor CloudWatch metrics
# - CompactionLambdaSuccess
# - CompactionLambdaError
# - StreamProcessorProcessedRecords
```

**Time to Execute**: 5-10 minutes
**Risk**: Low
**Rollback This Rollback**: Re-apply migration commits

---

### Full Rollback (Package + Lambda)

Use when package changes are causing issues.

#### Step 1: Document Current State

```bash
# Record current git state
git log --oneline -10 > /tmp/migration_state.txt
git diff HEAD~5 > /tmp/migration_changes.diff

# Record package version
cd receipt_chroma/
python -c "import receipt_chroma; print(receipt_chroma.__version__)" > /tmp/package_version.txt
```

#### Step 2: Identify Rollback Point

```bash
# View commit history
git log --oneline --all --graph -20

# Find commits related to migration
git log --oneline --grep="compaction migration"
git log --oneline --since="2024-12-15"

# Identify the commit BEFORE migration started
# Example: abc123def - "Last commit before compaction migration"
```

#### Step 3: Create Rollback Branch

```bash
# Create branch from current state (for reference)
git branch migration-rollback-$(date +%Y%m%d)

# Checkout main/working branch
git checkout upload-refactor

# Create rollback branch
git checkout -b rollback-compaction-migration
```

#### Step 4: Revert Git Commits

**Option A: Revert Specific Commits** (Preserves history)

```bash
# Revert migration commits in reverse order
git revert <commit-hash-4>
git revert <commit-hash-3>
git revert <commit-hash-2>
git revert <commit-hash-1>

# Example:
# git revert 9a8b7c6d  # "Update Lambda imports to use receipt_chroma"
# git revert 8a7b6c5d  # "Move business logic to receipt_chroma"
# git revert 7a6b5c4d  # "Create compaction and stream modules"
```

**Option B: Reset to Previous Commit** (Rewrites history - use with caution)

```bash
# Reset to commit before migration
git reset --hard <commit-before-migration>

# Example:
# git reset --hard abc123def
```

#### Step 5: Remove Package Changes

```bash
cd receipt_chroma/

# Remove new directories
rm -rf receipt_chroma/compaction/
rm -rf receipt_chroma/stream/

# Revert __init__.py changes
git checkout <commit-before-migration> receipt_chroma/__init__.py

# Verify package structure
tree receipt_chroma/ -L 2
```

#### Step 6: Restore Lambda Original Files

```bash
cd ../../infra/chromadb_compaction/lambdas/

# Restore all modified files
git checkout <commit-before-migration> .

# Verify no migration imports remain
grep -r "receipt_chroma.compaction" .
grep -r "receipt_chroma.stream" .

# Should return no results
```

#### Step 7: Reinstall Package

```bash
cd ../../../receipt_chroma/

# Reinstall package in development mode
pip install -e .

# Verify installation
python -c "import receipt_chroma; print('OK')"

# Verify no compaction module
python -c "from receipt_chroma.compaction import update_receipt_metadata" 2>&1 | grep "No module named"
# Should error (expected)
```

#### Step 8: Run Tests

```bash
# Run receipt_chroma tests
pytest tests/ -v

# Run Lambda tests
cd ../infra/chromadb_compaction/lambdas/
pytest tests/ -v

# All tests should pass
```

#### Step 9: Deploy Rolled-Back Version

```bash
cd ../../  # Back to infra/

# Deploy reverted Lambda
pulumi up --yes

# Monitor deployment
pulumi logs --follow
```

#### Step 10: Verify Production Recovery

```bash
# Check Lambda metrics
# - CompactionLambdaSuccess should increase
# - CompactionLambdaError should decrease

# Check DynamoDB streams are being processed
# - StreamProcessorProcessedRecords > 0

# Check ChromaDB updates are happening
# - CompactionMetadataUpdates > 0
# - CompactionLabelUpdates > 0

# Monitor for 30-60 minutes to ensure stability
```

**Time to Execute**: 30-60 minutes
**Risk**: Medium (rewrites history)
**Rollback This Rollback**: Merge migration branch back in

---

## Post-Rollback Actions

### Immediate (Within 1 hour)

1. **Notify Team**:
   ```
   Subject: Compaction Migration Rolled Back

   The compaction logic migration has been rolled back due to [reason].

   Current State:
   - Lambda: Reverted to pre-migration version
   - Package: Compaction modules removed
   - Status: [Stable/Monitoring]

   Next Steps:
   - Root cause analysis
   - Fix identified issues
   - Re-attempt migration when ready
   ```

2. **Document Issues**:
   - Create GitHub issue with rollback details
   - Include error logs
   - Include steps to reproduce
   - Tag as `migration-blocker`

3. **Monitor Systems**:
   - Watch CloudWatch metrics for 1 hour
   - Check for any lingering issues
   - Verify compaction operations recovering

### Short Term (Within 1 day)

1. **Root Cause Analysis**:
   - Identify what went wrong
   - Review error logs
   - Identify code issues
   - Document lessons learned

2. **Fix Issues**:
   - Create fixes in development branch
   - Add tests for identified issues
   - Verify fixes work locally

3. **Update Migration Plan**:
   - Document what went wrong
   - Update implementation steps
   - Add safeguards to prevent recurrence

### Medium Term (Within 1 week)

1. **Re-attempt Migration**:
   - Apply fixes
   - Test thoroughly in dev
   - Gradual rollout to production

2. **Improve Testing**:
   - Add tests for failure scenarios
   - Improve integration tests
   - Add monitoring/alerting

## Rollback Checklist

Before declaring rollback complete:

### Git State
- [ ] Rollback branch created (for reference)
- [ ] Commits reverted or reset
- [ ] No migration code in working tree
- [ ] Git status clean

### Package State
- [ ] `receipt_chroma/compaction/` removed
- [ ] `receipt_chroma/stream/` removed
- [ ] Package `__init__.py` reverted
- [ ] Package reinstalled
- [ ] Import tests confirm no compaction module

### Lambda State
- [ ] Handler files reverted
- [ ] Import statements back to local modules
- [ ] No imports from `receipt_chroma.compaction`
- [ ] No imports from `receipt_chroma.stream`

### Testing
- [ ] receipt_chroma tests pass
- [ ] Lambda tests pass
- [ ] Local integration tests pass

### Deployment
- [ ] Lambda deployed successfully
- [ ] No deployment errors
- [ ] Handlers executing successfully
- [ ] Metrics showing normal operation

### Monitoring (30-60 minutes)
- [ ] CompactionLambdaSuccess metric normal
- [ ] CompactionLambdaError metric low/zero
- [ ] StreamProcessorProcessedRecords normal
- [ ] No customer-facing issues
- [ ] CloudWatch logs clean

### Documentation
- [ ] Rollback documented in GitHub issue
- [ ] Team notified
- [ ] Migration plan updated
- [ ] Lessons learned documented

## Emergency Contact

If rollback fails or additional issues arise:

1. **Escalate to Team Lead**
2. **Check Runbook**: `docs/runbooks/lambda-deployment-issues.md`
3. **AWS Support**: Contact if infrastructure issues
4. **Rollback the Rollback**: Re-apply working version

## Prevention for Next Time

To avoid needing rollback in the future:

1. **Better Testing**:
   - More comprehensive integration tests
   - Load testing in dev environment
   - Canary deployments

2. **Gradual Rollout**:
   - Deploy to dev first (48 hours monitoring)
   - Deploy to staging (24 hours monitoring)
   - Deploy to production (with feature flag)

3. **Feature Flags**:
   - Add environment variable to toggle new code
   - Allow runtime switching between old/new implementations
   - Gradual migration of traffic

4. **Monitoring**:
   - Add new metrics for migration status
   - Set up alerts for anomalies
   - Dashboard for real-time monitoring

## Lessons Learned Template

After rollback, document:

```markdown
# Compaction Migration Rollback - Post-Mortem

## Date
[Date of rollback]

## Duration
[How long was new code deployed before rollback]

## Issue Summary
[Brief description of what went wrong]

## Root Cause
[Technical details of the issue]

## Detection
[How was the issue discovered]

## Impact
[What was affected]

## Resolution
[How was it resolved]

## Prevention
[What will prevent this in the future]

## Action Items
- [ ] Action 1
- [ ] Action 2
- [ ] Action 3

## Timeline
- [Time] - Migration deployed
- [Time] - Issue detected
- [Time] - Rollback initiated
- [Time] - Rollback completed
- [Time] - Systems confirmed stable
```

## References

- [AWS Lambda Versioning](https://docs.aws.amazon.com/lambda/latest/dg/configuration-versions.html)
- [Git Revert vs Reset](https://www.atlassian.com/git/tutorials/undoing-changes)
- [Pulumi Stack Rollback](https://www.pulumi.com/docs/reference/cli/pulumi_stack/)

