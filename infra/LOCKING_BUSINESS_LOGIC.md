# ChromaDB Compaction Locking - Business Logic Explained

## 🎯 Core Problem: Prevent Lost Updates

**Problem**: Multiple Lambda invocations processing different messages want to update the same ChromaDB snapshot simultaneously.

**Risk**: If not coordinated, updates could be lost or overwritten.

**Example**: 
- Lambda A reads snapshot version "v1"
- Lambda B reads snapshot version "v1"  
- Lambda A updates and writes "v2"
- Lambda B updates and writes "v2" (overwrites Lambda A's changes!)

## 🔒 Solution: Distributed Locking Strategy

We use **two-phase locking** with **CAS (Compare-And-Swap)** validation to ensure:
1. Only ONE Lambda writes at a time (mutual exclusion)
2. Updates build on the most recent snapshot (no lost work)
3. Lock contention is minimized (performance)

---

## 📋 Current Implementation (Optimized)

### Phase 1: Quick Validation Lock (~100ms)

**What happens OFF-LOCK:**
- Read latest version pointer from S3
- Download/copy snapshot from EFS to local Lambda storage
- Apply all updates to ChromaDB in-memory (merge deltas, metadata, labels)

**What happens ON-LOCK:**
```python
# Acquire lock (minimal time)
if not lm.acquire(lock_id):
    # Skip if busy - let someone else handle it
    continue

# CAS validation: Did someone else update the pointer while we were working?
current_pointer = read_pointer_from_s3()
if current_pointer != expected_pointer:
    # Someone already published! Skip to avoid overwriting
    release_lock()
    continue

# Still valid! Release lock and proceed to upload
release_lock()
```

**Duration**: ~100ms  
**Why so short**: We only check if pointer changed, then release immediately

---

### Phase 2: Heavy Work OFF-LOCK (~3-4 seconds)

**What happens:**
- EFS copy from /mnt/chroma to /tmp (1-3 seconds)
- ChromaDB operations (merge deltas, update metadata, apply labels)
- All in-memory on local Lambda storage

**Why OFF-LOCK**: This is SLOW work. If we kept the lock, other Lambdas would block waiting.  
**Risk**: Pointer might change while we work.  
**Solution**: Re-validate in Phase 3 (CAS)

---

### Phase 3: Critical Upload Lock (~2-3 seconds)

**What happens ON-LOCK:**
```python
# Re-acquire lock
acquire_lock()

# Double-check: Did pointer change during our work?
current_pointer = read_pointer_from_s3()
if current_pointer != expected_pointer:
    # Someone else published! Skip to avoid overwriting
    release_lock()
    continue

# Upload to S3 atomically (this is the ONLY place updates become permanent)
upload_snapshot_to_s3()

# Update S3 pointer
write_pointer_to_s3(new_version)

# Release lock
release_lock()
```

**Duration**: 2-3 seconds (S3 upload is fast with versioned keys)  
**Why ON-LOCK**: This is when updates become permanent. Must prevent races.

---

### Phase 4: EFS Cache Update OFF-LOCK (~1-2 seconds)

**What happens OFF-LOCK:**
```python
# Copy updated snapshot back to EFS for next time
copy_local_to_efs()
```

**Duration**: 1-2 seconds  
**Why OFF-LOCK**: EFS is just a cache. If this fails, we re-download from S3 next time.  
**Non-critical**: S3 is the source of truth.

---

## 🤔 Why This Design?

### 1. **Why two-phase locking?**
- **Reads OFF-LOCK**: Minimize contention. Many Lambdas can read in parallel.
- **Writes ON-LOCK**: Mutual exclusion. Only one publishes.

### 2. **Why CAS (Compare-And-Swap)?**
- **Validation**: Ensures we don't overwrite newer updates
- **Example**: 
  - Lambda A working on v1
  - Lambda B publishes v2
  - Lambda A tries to publish → CAS detects v2 exists → skips (avoids lost update!)

### 3. **Why EFS update off-lock?**
- **S3 is authoritative**: If EFS update fails, re-download from S3
- **Performance**: Don't block other Lambdas for cache updates
- **Resilience**: EFS can fail without affecting correctness

### 4. **Why re-acquire lock in Phase 3?**
- **Atomicity**: Upload + pointer update must be atomic
- **CAS guarantee**: Validate one final time before publishing
- **Prevents**: Lost updates if pointer changed during Phase 2

---

## 📊 Before vs After Optimization

| Phase | Before | After | Improvement |
|-------|--------|-------|-------------|
| Lock time | 3.5-4s | 2-3s | ✅ 33-44% faster |
| EFS copy | Under lock | Off-lock | ✅ Non-blocking |
| CAS validation | 1 validation | 2 validations | ✅ Prevents races |

---

## ⚠️ What If Lock Is Busy?

**Backoff strategy:**
```python
backoff_attempts = [0.1, 0.2]  # Short delays

for attempt in backoff_attempts:
    if acquire_lock():
        # Got lock! Proceed with upload
        break
    else:
        # Someone else is uploading, wait briefly
        sleep(attempt)
else:
    # Failed after all attempts, skip this batch
    # SQS will retry later
```

**Why skip instead of block?**
- Lambda timeout limit (5 minutes max)
- Don't want to block if someone else is actively uploading
- SQS will retry this batch later when lock is free

---

## 🎯 Key Principles

1. **S3 is the source of truth** (EFS is just a cache)
2. **Lock only for atomic operations** (read CAS, upload)
3. **Maximize parallel reads** (off-lock downloads)
4. **CAS prevents lost updates** (detect conflicts before publishing)
5. **Performance through concurrency** (minimize lock time)

---

## ✅ Guarantees

1. **Mutual exclusion**: Only one Lambda can publish updates at a time
2. **No lost updates**: CAS ensures we don't overwrite newer work
3. **Correctness**: S3 is always consistent (even if EFS fails)
4. **Performance**: Other Lambdas only blocked during critical upload (2-3s)

