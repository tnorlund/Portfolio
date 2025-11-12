# ChromaDB Version Migration Plan

## Overview

This document outlines the strategy for migrating ChromaDB databases when upgrading ChromaDB versions, particularly when Rust HNSW index format changes occur.

## Current State

- **receipt_label**: `chromadb>=1.0.0` (full) and `chromadb-client>=1.0.0` (lambda)
- **merchant_validation_container**: `chromadb>=0.4.22` (needs updating)
- **Storage**: EFS-first architecture with S3 backup
- **Collections**: `words` and `lines`

## Migration Strategy

### Phase 1: Pre-Migration Testing (Real Database)

**CRITICAL: Test with REAL databases from S3, not synthetic test data!**

1. **Test with real database from S3 (current version)**:
   ```bash
   # Activate venv
   source .venv/bin/activate

   # Test if current version (1.3.2) can read the S3 database
   python dev.test_real_migration.py --env dev --collection words
   ```

   This will:
   - Download a real database from S3 (created with receipt_label[full])
   - Test if ChromaDB 1.3.2 can read it
   - Verify data integrity

2. **Upgrade to latest version and test again**:
   ```bash
   # Upgrade ChromaDB
   pip install 'chromadb>=1.3.4'

   # Test if new version can read the database
   python dev.test_real_migration.py --env dev --collection words
   ```

   - If it can read it: ✅ No migration needed!
   - If it cannot: ⚠️ Migration required - script will test migration automatically

### Phase 2: Bump ChromaDB Version

**DO NOT bump the version until after testing the migration script!**

The migration script must be tested with the CURRENT version first to ensure it can:
1. Read old format databases
2. Export data correctly
3. Create new format databases with the NEW version

**Version Bump Steps**:

1. **Update dependencies** (in order):
   ```bash
   # Update receipt_label/pyproject.toml
   # Change: chromadb>=1.0.0 to chromadb>=<NEW_VERSION>

   # Update infra/merchant_validation_container/requirements.txt
   # Change: chromadb>=0.4.22 to chromadb>=<NEW_VERSION>
   ```

2. **Install new version locally**:
   ```bash
   pip install -e 'receipt_label[full]'
   ```

3. **Test migration with new version**:
   - The script should now create databases in the NEW format
   - Old databases may not be readable with new version (this is expected)
   - Test that migration script can read old format and write new format

### Phase 3: Dev Environment Migration

1. **Migrate dev S3 snapshots**:
   ```bash
   python dev.migrate_chromadb.py \
       --source s3 \
       --bucket chromadb-dev-bucket \
       --collection words \
       --output-path ./migrated_words \
       --upload-back \
       --output-bucket chromadb-dev-bucket \
       --output-key words/snapshot/latest_migrated/
   ```

2. **Update Lambda/container configs** to point to migrated snapshots

3. **Test dev environment** thoroughly

### Phase 4: Production Migration (Lambda-Based)

**You're right - we need a Lambda function for production!**

The migration script (`dev.migrate_chromadb.py`) is for local testing. For production EFS/S3 updates, we need a Lambda function because:

1. **EFS Access**: Production EFS is only accessible from VPC
2. **Scale**: Need to process large databases efficiently
3. **Orchestration**: Can be triggered via Step Functions or EventBridge
4. **Monitoring**: Lambda provides built-in CloudWatch integration

#### Lambda Migration Function Design

**Location**: `infra/chromadb_compaction/lambdas/migrate_chromadb.py`

**Features**:
- EFS-mounted database access
- S3 snapshot download/upload
- Batch processing for large collections
- Progress tracking (DynamoDB)
- Rollback capability

**Trigger Options**:
1. **Manual**: Invoke via AWS Console or CLI
2. **Scheduled**: EventBridge rule (e.g., after deployment)
3. **Step Function**: Part of deployment workflow

**Implementation Plan**:
```python
# Pseudo-code structure
def lambda_handler(event, context):
    collection = event['collection']  # 'words' or 'lines'
    migration_mode = event['mode']  # 'efs', 's3', or 'both'

    if migration_mode == 'efs':
        migrate_efs_database(collection)
    elif migration_mode == 's3':
        migrate_s3_snapshot(collection)
    else:
        # Migrate both EFS and S3
        migrate_efs_database(collection)
        migrate_s3_snapshots(collection)
```

### Phase 5: EFS Migration Strategy

EFS migration is more complex because:

1. **Zero Downtime**: Need to avoid interrupting active readers
2. **Atomic Updates**: Must update pointer files atomically
3. **Rollback**: Need ability to revert if migration fails

**Recommended Approach**:

1. **Blue-Green Migration**:
   - Create new EFS directory: `/mnt/chroma/{collection}/snapshot_migrated/`
   - Migrate data to new directory
   - Update pointer file atomically
   - Keep old directory as backup

2. **Locking Strategy**:
   - Use existing DynamoDB lock: `chroma-{collection}-update`
   - Acquire lock during migration
   - Prevent concurrent compactions

3. **Migration Lambda Flow**:
   ```
   Acquire Lock → Download from EFS → Migrate Locally →
   Upload to New EFS Path → Update Pointer → Verify → Release Lock
   ```

### Phase 6: Rollback Plan

If migration fails:

1. **EFS**: Update pointer file back to old directory
2. **S3**: Keep old snapshots (versioned)
3. **Lambda/Containers**: Revert to old ChromaDB version

## Testing Checklist

- [ ] Migration script works with current ChromaDB version
- [ ] Can read old format databases
- [ ] Can export all data correctly
- [ ] Bump ChromaDB version
- [ ] New version creates correct format
- [ ] Migration script can read old, write new
- [ ] Test migration on dev S3 snapshot
- [ ] Verify data integrity after migration
- [ ] Test Lambda function locally (SAM/localstack)
- [ ] Test Lambda function in dev environment
- [ ] Migrate dev EFS databases
- [ ] Test dev environment end-to-end
- [ ] Create production migration Lambda
- [ ] Test production migration on one collection
- [ ] Full production migration

## Migration Lambda Implementation

### Step 1: Create Lambda Handler

Create `infra/chromadb_compaction/lambdas/migrate_chromadb.py`:

```python
"""Lambda function for migrating ChromaDB databases in production."""
import os
import tempfile
import shutil
from typing import Dict, Any

from receipt_label.utils.chroma_client import ChromaDBClient
from receipt_label.utils.chroma_s3_helpers import (
    download_snapshot_atomic,
    upload_snapshot_atomic,
)
from receipt_label.utils.lock_manager import LockManager

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Migrate ChromaDB database from old format to new format."""
    collection = event.get('collection', 'words')
    mode = event.get('mode', 'both')  # 'efs', 's3', or 'both'

    # Use migration functions from dev.migrate_chromadb.py
    # (refactor those functions to be importable)

    return {"statusCode": 200, "body": "Migration complete"}
```

### Step 2: Add Infrastructure

Add to `infra/chromadb_compaction/components/lambda_functions.py`:

- New Lambda function with EFS mount
- IAM permissions for S3 and DynamoDB
- VPC configuration (for EFS access)
- Timeout: 15 minutes (for large databases)

### Step 3: Testing

1. Test locally with SAM/localstack
2. Test in dev environment with small database
3. Monitor CloudWatch logs and metrics

## Timeline Recommendation

1. **Week 1**: Test migration script locally, verify with current version
2. **Week 2**: Bump ChromaDB version, test migration script with new version
3. **Week 3**: Migrate dev environment, create Lambda function
4. **Week 4**: Test Lambda in dev, prepare production migration plan
5. **Week 5**: Execute production migration (one collection at a time)

## Risk Mitigation

1. **Backup**: Always backup before migration
2. **Blue-Green**: Keep old databases until verified
3. **Rollback**: Test rollback procedure before production
4. **Monitoring**: Add comprehensive logging and metrics
5. **Gradual**: Migrate one collection at a time

## Questions to Answer Before Production

1. What is the latest ChromaDB version? (Check PyPI)
2. Are there breaking changes in the new version?
3. Do we have a backup/restore procedure tested?
4. What is the rollback procedure?
5. How long will migration take for production databases?
6. What is the downtime window (if any)?

