# ChromaDB Version Rollback Analysis

## Current Situation

### Version History
- **Original Working Version**: ChromaDB 1.0.0
- **Current Version**: ChromaDB >=1.3.3 (specified in `receipt_label/pyproject.toml`)
- **Upgrade Path**: 1.0.0 → 1.3.2 → 1.3.3 → 1.3.4 (per migration docs)

### Issues Introduced After Upgrade
1. **SQLite File Locking**: `error returned from database: (code: 14) unable to open database file`
2. **HNSW Index Corruption**: `Error loading hnsw index`
3. **No `close()` Method**: ChromaDB's `PersistentClient` doesn't expose a close method

## What Changed Between 1.0.0 and 1.3.x?

### Known Changes (from documentation)
- **Breaking Changes**: ChromaDB documentation states that certain releases introduce breaking changes and **irreversible database migrations**
- **HNSW Index Format**: Rust HNSW index format changes occurred (mentioned in migration docs)
- **Database Format**: Database format may have changed between versions

### Potential Issues Introduced

#### 1. SQLite Connection Management
- **Hypothesis**: ChromaDB 1.3.x may have changed how SQLite connections are managed
- **Evidence**:
  - No `close()` method exists (this was true in 1.0.0 too, but may have been less problematic)
  - File locking issues are more frequent in 1.3.x
  - May have introduced more aggressive connection pooling or caching

#### 2. HNSW Index Building
- **Hypothesis**: HNSW index building may have become more asynchronous or aggressive
- **Evidence**:
  - Group 0 always fails with HNSW corruption (100% failure rate)
  - Index files may be written differently in 1.3.x
  - May have introduced background index building that wasn't present in 1.0.0

#### 3. File Handle Management
- **Hypothesis**: File handle release may have changed
- **Evidence**:
  - More frequent "unable to open database file" errors
  - File handles not released quickly enough between operations
  - May have introduced file handle caching or pooling

## Online Research Findings

### What I Found
- **No Specific GitHub Issues**: Web search did not find specific GitHub issues about SQLite file locking in ChromaDB 1.3.x
- **Breaking Changes Warning**: ChromaDB documentation warns about breaking changes and irreversible migrations
- **Version Updates**: Multiple releases between 1.0.0 and 1.3.3 (latest as of Nov 5, 2025)

### What I Did NOT Find
- Specific complaints about SQLite locking issues in 1.3.x
- Community discussions about `PersistentClient` close() method
- Known bugs related to file handle management

**Note**: I may have been mistaken earlier about finding online complaints. The research did not reveal specific community-reported issues matching our symptoms.

## Rollback Considerations

### ⚠️ **CRITICAL WARNING: Irreversible Database Migrations**

According to ChromaDB documentation:
> "Certain releases introduce breaking changes and **irreversible database migrations**. Once upgraded to a new version, **downgrading to an older version is not possible**."

### What This Means

1. **Database Format Changes**: Databases created with ChromaDB 1.3.x may not be readable by 1.0.0
2. **HNSW Index Format**: HNSW indexes created with 1.3.x may not be compatible with 1.0.0
3. **Metadata Format**: Collection metadata may have changed format

### Rollback Feasibility

#### ✅ **Possible If:**
- You have **backups** of databases created with ChromaDB 1.0.0
- You haven't created **new databases** with 1.3.x
- You can **restore** old snapshots from S3/EFS

#### ❌ **Not Possible If:**
- All databases have been migrated to 1.3.x format
- New data has been written with 1.3.x
- Old format databases don't exist anymore

## Rollback Strategy (If Feasible)

### Step 1: Assess Current State
```bash
# Check what databases exist in S3/EFS
# Determine which were created with which version
# Check if old format databases still exist
```

### Step 2: Backup Current State
```bash
# Backup all current databases before rollback
# Store in separate S3 location
# Document current state
```

### Step 3: Restore Old Format Databases
```bash
# If old format databases exist:
# - Restore from S3 snapshots created with 1.0.0
# - Or restore from EFS backups (if available)
```

### Step 4: Rollback Code
```bash
# Update pyproject.toml:
# chromadb>=1.0.0,<1.1.0  # Pin to 1.0.x

# Redeploy infrastructure
pulumi up --stack dev
```

### Step 5: Verify Rollback
```bash
# Test that old databases are readable
# Verify no SQLite locking issues
# Confirm HNSW indexes work
```

## Alternative: Stay on 1.3.x and Fix Issues

### Current Fixes Implemented
1. **Client Closing Workaround**: `close_chromadb_client()` function
2. **Main Client Flush**: Flushing between chunks
3. **File Verification**: Checking SQLite and HNSW files before upload
4. **Delays**: 300ms delays after closing clients

### Why This May Be Better
- **No Database Migration**: Don't need to migrate databases back
- **Forward Compatible**: Stay on latest version
- **Bug Fixes**: 1.3.x likely has bug fixes not in 1.0.0
- **Performance**: May have performance improvements

### If Fixes Don't Work
- May need to investigate ChromaDB source code
- Could file GitHub issues with ChromaDB team
- May need to implement more aggressive workarounds

## Recommendation

### Option 1: Try Current Fixes First (Recommended)
1. **Deploy current fixes** (main client flush, file verification)
2. **Test thoroughly** with enhanced logging
3. **Monitor** for 24-48 hours
4. **If issues persist**, consider rollback

### Option 2: Rollback to 1.0.0 (If Feasible)
1. **Verify** old format databases exist
2. **Backup** current state
3. **Restore** old format databases
4. **Rollback** code to 1.0.0
5. **Test** that issues are resolved

### Option 3: Hybrid Approach
1. **Keep 1.3.x** for new features
2. **Implement aggressive workarounds** for file locking
3. **Monitor** ChromaDB releases for fixes
4. **Upgrade** when fixes are available

## Questions to Answer

1. **Do old format databases exist?**
   - Check S3 snapshots created before upgrade
   - Check EFS backups
   - Determine when upgrade happened

2. **What data would be lost?**
   - If rollback, what data was created with 1.3.x?
   - Can that data be recreated?

3. **Is rollback worth it?**
   - Are current fixes sufficient?
   - Is the pain of rollback less than fixing issues?

4. **What's the timeline?**
   - How urgent is fixing these issues?
   - Can we wait for ChromaDB fixes?

## Next Steps

1. **Check S3/EFS** for old format databases
2. **Test current fixes** thoroughly
3. **Decide** on rollback vs. continuing with fixes
4. **Document** decision and rationale

