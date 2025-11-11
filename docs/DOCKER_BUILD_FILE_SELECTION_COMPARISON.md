# Docker Build File Selection Comparison

This document compares how the **ChromaDB Compaction Lambda** and **Address Similarity Cache Generator Lambda** select files for their Docker builds.

## Overview

Both Lambdas use the same `CodeBuildDockerImage` component, which handles file selection through a **rsync-based process** that automatically includes only necessary files.

---

## File Selection Logic (CodeBuildDockerImage)

The `CodeBuildDockerImage` component uses **rsync with include/exclude patterns** to create a minimal build context. Here's how it works:

### Step 1: Always Include Base Packages
```bash
rsync -a \
  --include='receipt_dynamo/' \
  --include='receipt_dynamo/pyproject.toml' \
  --include='receipt_dynamo/receipt_dynamo/' \
  --include='receipt_dynamo/receipt_dynamo/**' \
  --include='receipt_dynamo/docs/' \
  --include='receipt_dynamo/docs/README.md' \
  --include='receipt_label/' \
  --include='receipt_label/pyproject.toml' \
  --include='receipt_label/receipt_label/' \
  --include='receipt_label/receipt_label/**' \
  --include='receipt_label/README.md' \
  --include='receipt_label/LICENSE' \
  --exclude='*' \
  "$CONTEXT_PATH/" "$TMP/context/"
```

**Result:** Both Lambdas get `receipt_dynamo/` and `receipt_label/` packages.

### Step 2: Include Handler Directory (Automatic)
```bash
# Extract infra directory from Dockerfile path
INFRA_DIR=$(dirname "$DOCKERFILE")
# Example: "infra/chromadb_compaction/lambdas" or "infra/routes/address_similarity_cache_generator"

rsync -a \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "$INFRA_DIR/" "$TMP/context/$INFRA_DIR/"
```

**Result:** The entire directory containing the Dockerfile is included.

### Step 3: Copy Dockerfile
```bash
cp "$DOCKERFILE" "$TMP/context/Dockerfile"
```

---

## ChromaDB Compaction Lambda

### Configuration
- **Dockerfile Path:** `infra/chromadb_compaction/lambdas/Dockerfile`
- **Build Context:** `.` (project root)
- **Source Paths:** `None` (uses default rsync)
- **Infra Directory Included:** `infra/chromadb_compaction/lambdas/`

### Files Included in Build Context
1. ✅ `receipt_dynamo/` (entire package)
2. ✅ `receipt_label/` (entire package)
3. ✅ `infra/chromadb_compaction/lambdas/` (entire directory)
   - `enhanced_compaction_handler.py`
   - `utils/` (directory)
   - `compaction/` (directory)
   - `Dockerfile`
4. ✅ `Dockerfile` (copied to context root)

### Dockerfile COPY Commands
```dockerfile
# Stage 1: Dependencies
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_label/ /tmp/receipt_label/

# Stage 2: Handler Code
COPY infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py ${LAMBDA_TASK_ROOT}/handler.py
COPY infra/chromadb_compaction/lambdas/utils/ ${LAMBDA_TASK_ROOT}/utils/
COPY infra/chromadb_compaction/lambdas/compaction/ ${LAMBDA_TASK_ROOT}/compaction/
```

**Key Point:** The compaction Lambda copies **multiple files/directories** from its infra directory:
- Handler file → `handler.py`
- Utils directory → `utils/`
- Compaction package → `compaction/`

---

## Address Similarity Cache Generator Lambda

### Configuration
- **Dockerfile Path:** `infra/routes/address_similarity_cache_generator/Dockerfile`
- **Build Context:** `.` (project root)
- **Source Paths:** `None` (uses default rsync)
- **Infra Directory Included:** `infra/routes/address_similarity_cache_generator/`

### Files Included in Build Context
1. ✅ `receipt_dynamo/` (entire package)
2. ✅ `receipt_label/` (entire package)
3. ✅ `infra/routes/address_similarity_cache_generator/` (entire directory)
   - `handler/index.py`
   - `Dockerfile`
   - `infra.py` (not used in Docker build, but included)
4. ✅ `Dockerfile` (copied to context root)

### Dockerfile COPY Commands
```dockerfile
# Stage 1: Dependencies
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_label/ /tmp/receipt_label/

# Stage 2: Handler Code
COPY infra/routes/address_similarity_cache_generator/handler/index.py ${LAMBDA_TASK_ROOT}/index.py
```

**Key Point:** The cache generator Lambda copies **only the handler file**:
- Handler file → `index.py`

---

## Key Differences

| Aspect | Compaction Lambda | Cache Generator Lambda |
|--------|------------------|----------------------|
| **Handler Files** | 1 file + 2 directories | 1 file only |
| **Handler Location in Dockerfile** | `handler.py` | `index.py` |
| **Additional Code** | `utils/` and `compaction/` packages | None |
| **Complexity** | More complex (multiple modules) | Simpler (single file) |

---

## What Gets Excluded

Both Lambdas automatically exclude:
- ❌ `__pycache__/` directories
- ❌ `*.pyc` files
- ❌ `.git/` directory
- ❌ Everything else not explicitly included (via `--exclude='*'`)

The rsync logic ensures that **only necessary files** are included in the build context, keeping the `context.zip` small and uploads fast.

---

## Why This Works

1. **Automatic Detection:** The `CodeBuildDockerImage` component automatically extracts the infra directory from the Dockerfile path using `dirname "$DOCKERFILE"`.

2. **Consistent Pattern:** Both Lambdas follow the same pattern:
   - Dockerfile in `infra/<component>/`
   - Handler code in the same directory or subdirectory
   - Rsync automatically includes the entire infra directory

3. **Minimal Context:** Only the required files are included, reducing build context size and upload time.

4. **No Manual Configuration:** Neither Lambda needs to specify `source_paths` because the default rsync logic handles everything automatically.

---

## Potential Issues

If a Lambda needs **additional files** outside the infra directory, it would need to:
1. Specify `source_paths` in the `CodeBuildDockerImage` configuration, OR
2. Restructure to put all needed files within the infra directory

Currently, both Lambdas work with the default rsync logic because all their code is within their respective infra directories.

