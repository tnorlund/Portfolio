# Pulumi Trigger Mechanism Explained

## The Problem You Encountered

**Deleting the hash file from S3 didn't trigger a rebuild** - here's why:

### How Pulumi's Trigger System Works

1. **Hash Calculation** (`_calculate_content_hash()`)
   - Calculates SHA256 from filesystem
   - Hash: `28637f5d4a02`

2. **Upload Command** (`upload_cmd`)
   ```python
   triggers=[content_hash]  # ← KEY: Only runs when hash CHANGES
   ```
   - **Only runs when `content_hash` changes**
   - If hash is same → command is skipped entirely
   - Script never executes → never checks S3

3. **Pipeline Trigger** (`pipeline_trigger_cmd`)
   ```python
   triggers=[content_hash]  # ← Also only runs when hash changes
   depends_on=[upload_cmd]  # ← Must wait for upload first
   ```
   - Only runs when hash changes
   - Depends on upload completing

### Why Deleting Hash File Didn't Work

```
1. You delete hash.txt from S3 ✅
2. You run pulumi up ✅
3. Pulumi calculates hash: 28637f5d4a02 (same as before)
4. Pulumi checks: Has hash changed? ❌ NO
5. Pulumi skips: upload_cmd (triggers=[content_hash] didn't change)
6. Upload script NEVER RUNS
7. S3 hash check NEVER HAPPENS
8. Pipeline NEVER TRIGGERS
```

**The upload script checks S3 for the hash file, but the script never runs because Pulumi's trigger mechanism skipped it!**

## The Solution

### Option 1: Force Hash to Change (What I Just Did)

Add a marker file to force hash change:
```bash
echo "# Force rebuild" > receipt_label/receipt_label/vector_store/client/.force_rebuild
git add .force_rebuild
git commit -m "Force rebuild"
pulumi up
```

### Option 2: Use Force Rebuild Config

```bash
pulumi config set docker-build:force-rebuild true
pulumi up
```

This sets `FORCE_REBUILD="True"` in the upload script, which bypasses the hash check:
```bash
if [ "$STORED_HASH" = "$HASH" ] && [ "$FORCE_REBUILD" != "True" ]; then
  echo "✅ Context up-to-date. Skipping upload."
  exit 0
fi
```

### Option 3: Modify a File That's Hashed

Touch any file that's included in the hash calculation:
- Any file in `receipt_label/receipt_label/`
- Any file in `infra/embedding_step_functions/unified_embedding/`
- The Dockerfile

## Why This Design?

**Pulumi's `triggers` mechanism is designed for efficiency:**
- Only runs commands when inputs change
- Prevents unnecessary work
- But requires hash to actually change

**The upload script's hash check is a secondary safety:**
- Prevents uploading same content twice
- But only works if the script runs!

## Fixing the Root Issue

The real problem is that the hash `28637f5d4a02` was calculated when the validation code was already present (probably from uncommitted changes). So when you committed, the hash didn't change.

**Better approach**: Always commit changes BEFORE running `pulumi up`, so the hash reflects the committed state.

## Current Status

I've added a marker file (`.force_rebuild`) which should force the hash to change. Run `pulumi up` again and it should:
1. Calculate new hash (different from `28637f5d4a02`)
2. Run upload_cmd (because hash changed)
3. Upload script checks S3, finds hash missing or different
4. Uploads context.zip
5. Triggers pipeline
6. Builds new image with validation code

