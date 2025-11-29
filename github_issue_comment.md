# Comment for GitHub Issue #5868

## Still Experiencing Corruption Despite Workaround

I've implemented the workaround you suggested (closing internal connections via `_client._client`, setting client to None, forcing GC), but I'm **still seeing corrupted SQLite databases** when uploading ChromaDB snapshots to S3.

### What I'm Doing

My `close()` method:
1. Closes internal SQLite connections: `self._client._client.close()` (if available)
2. Clears reference: `self._client = None`
3. Forces GC (3 passes): `gc.collect()` multiple times
4. Adds delay: `time.sleep(0.5)` for OS file handle release
5. **Always called before S3 upload** in my compaction handler

### The Problem

Despite this, snapshots are still corrupted:
- ✅ Collection count works (reports 68,744 items)
- ❌ **All data access fails**: `peek()`, `get()`, `query()` → `InternalError: Error executing plan: Internal error: Error finding id`

Tested by downloading fresh snapshots from S3 - corruption is confirmed in the uploaded files, not local.

### Request

The workaround isn't sufficient. We need:
1. A proper `close()` method in PersistentClient API, OR
2. A more reliable workaround that guarantees SQLite files are unlocked

This is blocking production - downstream consumers can't read our snapshots.

**Code**: `receipt_chroma/receipt_chroma/data/chroma_client.py:177-253`

