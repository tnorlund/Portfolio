# File Polling Implementation for ChromaDB Client Closing

## Overview

Replaced fixed delays with **file polling** to wait only as long as necessary for files to unlock, up to a maximum timeout. This is more efficient than fixed delays.

## Implementation

### Core Functions

#### `wait_for_file_unlock(file_path, max_wait=2.0, check_interval=0.05)`

Polls a single file to check if it's unlocked by attempting to open it exclusively.

**How it works**:
1. Attempts to open file in `r+b` mode (exclusive)
2. If successful → file is unlocked, return immediately
3. If fails → file is locked, wait `check_interval` (50ms) and retry
4. Continues until file unlocks or `max_wait` timeout

**Benefits**:
- Returns immediately if file is already unlocked (no delay)
- Only waits as long as necessary (up to max_wait)
- Checks every 50ms (fast response when file unlocks)

#### `wait_for_chromadb_files_unlocked(persist_directory, max_wait=2.0)`

Checks ChromaDB files (SQLite + HNSW) for unlock status.

**Files checked**:
1. **Primary**: `chroma.sqlite3` (most critical)
2. **Secondary**: HNSW index files (`.bin`, `.pickle`) in collection directories

**Strategy**:
- Waits for SQLite file to unlock (critical)
- Checks HNSW files but doesn't fail if they're still locked (less critical)
- Limits checks to avoid excessive polling (first 5 collections, first 3 files each)

### Updated `close_chromadb_client()`

**New signature**:
```python
def close_chromadb_client(
    client: Any,
    collection_name: Optional[str] = None,
    persist_directory: Optional[str] = None,  # NEW
    max_wait: float = 2.0,  # NEW
) -> None:
```

**Changes**:
1. **Auto-detects persist directory** from client if not provided
2. **Uses file polling** instead of fixed delay
3. **Configurable max_wait** (default: 2.0s)

**Fallback**: If persist directory can't be determined, uses 0.1s delay

## Call Sites Updated

### 1. Process Chunk Handler
```python
close_chromadb_client(
    chroma_client,
    collection_name="chunk_processing",
    persist_directory=temp_dir,
    max_wait=2.0,  # Full wait before uploads
)
```

### 2. Download and Merge Delta
```python
close_chromadb_client(
    delta_client,
    collection_name="delta_processing",
    persist_directory=delta_temp,
    max_wait=1.0,  # Shorter wait (less critical)
)
```

### 3. Intermediate Merge - Chunk Clients
```python
close_chromadb_client(
    chunk_client,
    collection_name="group_merge",
    persist_directory=chunk_temp,
    max_wait=1.0,  # Shorter wait for chunk cleanup
)
```

### 4. Intermediate Merge - Main Client
```python
close_chromadb_client(
    chroma_client,
    collection_name="intermediate_merge",
    persist_directory=temp_dir,
    max_wait=2.0,  # Full wait before uploads
)
```

### 5. Final Merge - Chunk Clients
```python
close_chromadb_client(
    chunk_client,
    collection_name="final_merge_chunk",
    persist_directory=chunk_temp,
    max_wait=1.0,  # Shorter wait for chunk cleanup
)
```

### 6. Final Merge - Main Client
```python
close_chromadb_client(
    chroma_client,
    collection_name="final_merge",
    persist_directory=temp_dir,
    max_wait=2.0,  # Full wait before final upload
)
```

## Expected Performance Impact

### Before (Fixed 0.5s Delays)
- **Total calls**: ~75-95 per execution
- **Total delay**: 37.5 - 47.5 seconds
- **Percentage of Lambda**: ~12-16% of 5-minute timeout

### After (File Polling)
- **Best case** (files unlock quickly): ~1-5 seconds total
- **Worst case** (files take time): ~10-20 seconds total (if many files need max wait)
- **Average case**: ~5-10 seconds total

**Expected savings**: **60-80% reduction** in wait time

## How File Polling Works

### Example Timeline

**Scenario**: File unlocks after 200ms

**Fixed Delay (Old)**:
```
0ms:   Close client
0ms:   gc.collect()
0ms:   Start 500ms delay
500ms: Delay complete, proceed
Total: 500ms (even though file unlocked at 200ms)
```

**File Polling (New)**:
```
0ms:    Close client
0ms:    gc.collect()
0ms:    Start polling
50ms:   Check file → locked
100ms:  Check file → locked
150ms:  Check file → locked
200ms:  Check file → unlocked! Return immediately
Total: 200ms (only waited as long as needed)
```

## Benefits

1. **Faster when files unlock quickly**: No unnecessary delays
2. **Still reliable**: Waits up to max_wait if files take time
3. **Configurable**: Can adjust max_wait per call site
4. **Smart**: Checks actual file status, not just waits

## Trade-offs

**Pros**:
- Much faster in typical cases
- Still handles slow unlocks
- More efficient use of Lambda time

**Cons**:
- More complex than fixed delay
- Requires file system access
- Slightly more CPU usage (polling)

## Testing Recommendations

1. **Monitor logs** for "File unlocked after polling" messages
2. **Track elapsed times** to see actual wait times
3. **Compare success rates** vs fixed delay approach
4. **Adjust max_wait** if needed based on results

