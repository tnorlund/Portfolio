# Force-Rebuild Config Explanation

## Does `force-rebuild` Help?

**Short answer**: Yes, but only if the hash changes. It doesn't solve the trigger problem.

## How `force-rebuild` Works

### Configuration
```bash
pulumi config set docker-build:force-rebuild true
```

### Code Flow

1. **Config Read** (line 83):
   ```python
   config = pulumi.Config("docker-build")
   self.force_rebuild = config.get_bool("force-rebuild") or False
   ```

2. **Passed to Script** (line 283):
   ```python
   FORCE_REBUILD="{self.force_rebuild}"
   ```

3. **Script Check** (line 288):
   ```bash
   if [ "$STORED_HASH" = "$HASH" ] && [ "$FORCE_REBUILD" != "True" ]; then
     echo "✅ Context up-to-date. Skipping upload."
     exit 0
   fi
   ```

4. **Effect**: If `FORCE_REBUILD="True"`, script always uploads (bypasses hash check)

## The Problem

**`force-rebuild` only helps IF the upload script runs!**

But the upload script only runs when:
- `triggers=[content_hash]` detects hash changed
- OR command is created for first time

### Why It Doesn't Help Here

```
1. Hash calculated: 28637f5d4a02 (same as before)
2. Pulumi checks: Has hash changed? ❌ NO
3. Pulumi skips: upload_cmd (triggers=[content_hash] didn't change)
4. Upload script NEVER RUNS
5. FORCE_REBUILD never checked
```

**The script never executes, so `force-rebuild` never matters!**

## When `force-rebuild` DOES Help

### Scenario 1: Hash Changes
```
1. Hash changes: 28637f5d4a02 → 0082cf3b18e7
2. Pulumi runs: upload_cmd (hash changed)
3. Script executes
4. Script checks: STORED_HASH = 28637f5d4a02, HASH = 0082cf3b18e7
5. Hashes differ → Uploads anyway (force-rebuild not needed)
```

### Scenario 2: Hash Same, But Force Rebuild Set
```
1. Hash same: 28637f5d4a02
2. Pulumi runs: upload_cmd (because hash changed OR first time)
3. Script executes
4. Script checks: STORED_HASH = 28637f5d4a02, HASH = 28637f5d4a02
5. Hashes match BUT FORCE_REBUILD="True"
6. Script uploads anyway (bypasses hash check)
```

## The Real Issue

**The trigger mechanism prevents the script from running:**

```python
upload_cmd = command.local.Command(
    f"{self.name}-upload-context",
    triggers=[content_hash],  # ← Only runs when hash CHANGES
    ...
)
```

If hash doesn't change, Pulumi never runs the command, so the script never executes.

## Solutions

### Option 1: Force Hash to Change (What We Did)
```bash
# Add marker file
echo "# Force rebuild" > receipt_label/receipt_label/vector_store/client/.force_rebuild
git add .force_rebuild
git commit -m "Force rebuild"
pulumi up  # Hash changes → trigger fires → script runs
```

### Option 2: Use Force Rebuild + Change Hash
```bash
# Set force-rebuild
pulumi config set docker-build:force-rebuild true

# Change hash (add marker file or modify a file)
touch receipt_label/receipt_label/vector_store/client/chromadb_client.py
pulumi up  # Hash changes → trigger fires → script runs → force-rebuild bypasses hash check
```

### Option 3: Fix the Trigger Mechanism (Better Long-term)

The trigger should check if the upload is needed, not just if hash changed. But that would require code changes.

## Current Status

With the marker file (`.force_rebuild`), the hash changed to `0082cf3b18e7`. Now:
1. ✅ Hash changed → `upload_cmd` will run
2. ✅ Script will execute
3. ✅ Script will upload (hash different OR force-rebuild set)

**So `force-rebuild` would help NOW (with hash changed), but wouldn't have helped before (hash same).**

## Recommendation

**Use both**:
```bash
# Set force-rebuild for safety
pulumi config set docker-build:force-rebuild true

# Ensure hash changes (or it won't trigger)
pulumi up  # With marker file, hash changed, so it will trigger
```

This ensures:
- Hash change triggers the upload command
- Force-rebuild ensures upload happens even if hash somehow matches

