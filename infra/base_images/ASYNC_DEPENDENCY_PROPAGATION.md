# Async Dependency Propagation: How It Works

## Overview

This document explains how Pulumi handles asynchronous builds across the entire dependency chain when you modify a base package like `receipt_dynamo`.

## The Enhancement

`CodeBuildDockerImage` now supports content-based tags via the `image_tag` parameter:

```python
base_image = CodeBuildDockerImage(
    "base-receipt-agent",
    dockerfile_path="...",
    image_tag="git-ff823670d",  # ← Content-based tag!
    sync_mode=False,  # ← Async build!
)
```

When the tag changes, `image_uri` Output changes, triggering downstream rebuilds.

## The Dependency Chain

```
receipt_dynamo/
    ↓ (included in)
base-receipt-dynamo (CodeBuildDockerImage)
    ↓ (base_image_uri dependency)
base-receipt-label (CodeBuildDockerImage)
    ↓ (base_image_uri dependency)
base-receipt-agent (CodeBuildDockerImage)
    ↓ (base_image_uri dependency)
metadata_harmonizer Lambda (CodeBuildDockerImage)
label_validation Lambda (CodeBuildDockerImage)
... (other Lambdas)
```

## Scenario: Modify receipt_dynamo/client.py

### Step 1: Pulumi Calculates New State (Local, ~5 seconds)

```bash
$ pulumi up
```

Pulumi evaluates all Python code:

```python
# base_images.py
dynamo_tag = get_content_hash(receipt_dynamo_dir)
# → git status shows changes
# → Returns: "git-abc1234-dirty" (NEW!)

label_tag = get_combined_content_hash([receipt_dynamo, receipt_label])
# → Returns: "git-abc1234-dirty" (NEW!)

agent_tag = get_combined_content_hash([receipt_dynamo, receipt_chroma, ...])
# → Returns: "git-abc1234-dirty" (NEW!)
```

Pulumi compares to stored state:
- `base_dynamo.image_uri`: `....:git-ff823670d` → `....:git-abc1234-dirty` ✅ Changed!
- `base_label.image_uri`: `....:git-ff823670d` → `....:git-abc1234-dirty` ✅ Changed!
- `base_agent.image_uri`: `....:git-ff823670d` → `....:git-abc1234-dirty` ✅ Changed!

### Step 2: Pulumi Builds Dependency Graph (Local, ~1 second)

```
Level 0: base-receipt-dynamo (no dependencies)
Level 1: base-receipt-label (depends on base-receipt-dynamo)
Level 2: base-receipt-agent (depends on base-receipt-label)
Level 3: All Lambdas (depend on base-receipt-agent)
```

### Step 3: Pulumi Shows Preview (Local, ~1 second)

```
Previewing update (dev):

Type                           Name                           Plan
~   custom:codebuild:Docker..  base-receipt-dynamo-img       update
~   custom:codebuild:Docker..  base-receipt-label-img        update
~   custom:codebuild:Docker..  base-receipt-agent-img        update
~   custom:codebuild:Docker..  metadata-harmonizer-img       update
~   custom:codebuild:Docker..  label-validation-img          update

Resources:
    ~ 5 to update

Duration: 7s
```

### Step 4: You Approve (Local, instant)

```
Do you want to perform this update? yes
```

### Step 5: Pulumi Executes Updates (Async, Level by Level)

#### Level 0: base-receipt-dynamo (Async)

```
Time: 00:00 - Start base-receipt-dynamo update
  • Upload context to S3 (~5 sec)
  • Trigger CodeBuild (async)
  • ✅ pulumi up continues immediately!

Time: 00:05 - pulumi up RETURNS! You can close your laptop! ⚡

[Background on AWS]
Time: 00:05 - CodeBuild starts building base-receipt-dynamo
Time: 03:30 - ✅ base-receipt-dynamo complete, pushed to ECR
              Tag: ....:git-abc1234-dirty
```

**Key Point:** Pulumi doesn't wait for CodeBuild! It just triggers it and moves on.

But wait... how does Level 1 know when Level 0 is done?

#### The Secret: Pulumi's Resource Completion

When `CodeBuildDockerImage` "completes" (from Pulumi's perspective):
- S3 upload done ✅
- CodeBuild triggered ✅
- `image_uri` Output resolved ✅

Pulumi considers the resource "updated" even though CodeBuild is still running!

The `image_uri` Output is **immediately available** with the new tag:
```python
base_dynamo.image_uri = "123456.dkr.../base-receipt-dynamo-dev:git-abc1234-dirty"
```

This allows Level 1 to start!

#### Level 1: base-receipt-label (Async, starts immediately after Level 0)

```
Time: 00:05 - base-receipt-dynamo "complete" (S3 uploaded, CodeBuild triggered)
Time: 00:05 - Start base-receipt-label update
  • Upload context to S3 (~5 sec)
  • Trigger CodeBuild with BASE_IMAGE_URI=....:git-abc1234-dirty
  • ✅ Continues immediately!

[Background on AWS]
Time: 00:10 - CodeBuild starts building base-receipt-label
Time: 00:10 - Tries to pull FROM base-receipt-dynamo:git-abc1234-dirty
Time: 00:10 - ⏸️  WAITS! Image not in ECR yet!
Time: 03:30 - base-receipt-dynamo pushed to ECR
Time: 03:30 - 🚀 Docker pull succeeds!
Time: 03:30 - Continues building base-receipt-label
Time: 05:00 - ✅ base-receipt-label complete
```

**Key Point:** CodeBuild WAITS for the base image to appear in ECR!

Docker's `FROM` statement will retry pulling the image. CodeBuild has a 60-minute timeout, plenty of time for the base to build.

#### Level 2: base-receipt-agent (Async, starts immediately after Level 1)

```
Time: 00:05 - base-receipt-label "complete" (S3 uploaded, CodeBuild triggered)
Time: 00:05 - Start base-receipt-agent update
  • Upload context to S3 (~5 sec)
  • Trigger CodeBuild with BASE_IMAGE_URI=....:git-abc1234-dirty
  • ✅ Continues immediately!

[Background on AWS]
Time: 00:10 - CodeBuild starts building base-receipt-agent
Time: 00:10 - Tries to pull FROM base-receipt-label:git-abc1234-dirty
Time: 00:10 - ⏸️  WAITS! Image not in ECR yet!
Time: 05:00 - base-receipt-label pushed to ECR
Time: 05:00 - 🚀 Docker pull succeeds!
Time: 05:00 - Continues building base-receipt-agent
Time: 06:30 - ✅ base-receipt-agent complete
```

#### Level 3: All Lambdas (Async, parallel, start immediately after Level 2)

```
Time: 00:05 - base-receipt-agent "complete" (S3 uploaded, CodeBuild triggered)
Time: 00:05 - Start ALL Lambda updates (parallel!)
  • metadata_harmonizer: Upload context, trigger CodeBuild
  • label_validation: Upload context, trigger CodeBuild
  • label_harmonizer: Upload context, trigger CodeBuild
  • ✅ All continue immediately!

[Background on AWS]
Time: 00:10 - All Lambda CodeBuilds start
Time: 00:10 - All try to pull FROM base-receipt-agent:git-abc1234-dirty
Time: 00:10 - ⏸️  All WAIT! Image not in ECR yet!
Time: 06:30 - base-receipt-agent pushed to ECR
Time: 06:30 - 🚀 All Docker pulls succeed!
Time: 06:30 - All continue building
Time: 08:00 - ✅ All Lambdas complete (parallel!)
```

## Timeline Summary

```
Time    You             Pulumi          AWS CodeBuild
────    ──────────────  ──────────────  ─────────────────────────────────────
00:00   pulumi up       Calculating...
00:05   ✅ DONE!        Triggering...
00:05   Close laptop!   ✅ DONE!        base-dynamo building...
                                        base-label waiting for base-dynamo...
                                        base-agent waiting for base-label...
                                        lambdas waiting for base-agent...
03:30                                   base-dynamo ✅ → base-label starts
05:00                                   base-label ✅ → base-agent starts
06:30                                   base-agent ✅ → all lambdas start
08:00                                   ✅ All complete!
```

**Your experience:** 5 seconds, then you're done! Everything else happens in AWS.

## How Docker Handles Missing Base Images

When CodeBuild runs:
```dockerfile
FROM 123456.dkr.../base-receipt-dynamo-dev:git-abc1234-dirty
```

If the image doesn't exist yet:
1. Docker tries to pull
2. ECR returns "manifest not found"
3. Docker retries (with exponential backoff)
4. Eventually (when base finishes), pull succeeds
5. Build continues

This is **automatic** - no special code needed!

## Why This Works

### 1. Pulumi Doesn't Wait for CodeBuild

`CodeBuildDockerImage` completes when:
- ✅ S3 upload done
- ✅ CodeBuild triggered
- ✅ Outputs resolved

It does NOT wait for:
- ❌ CodeBuild to finish
- ❌ Image to be in ECR

This makes `pulumi up` fast!

### 2. Docker Waits for Base Images

CodeBuild/Docker handles the waiting:
- `FROM` statement will retry pulling
- CodeBuild timeout (60 min) is plenty
- No special orchestration needed

### 3. Pulumi's Dependency Graph Ensures Correct Order

Even though everything triggers "immediately":
- Level 0 triggers first
- Level 1 triggers second (but waits in Docker)
- Level 2 triggers third (but waits in Docker)
- Level 3 triggers fourth (but waits in Docker)

The dependency chain is preserved!

## Configuration

### Async Mode (Default for Development)

```python
base_image = CodeBuildDockerImage(
    "base-receipt-agent",
    sync_mode=False,  # ← Async (default)
    image_tag="git-abc123",
)
```

- `pulumi up` returns immediately
- Builds happen in background
- Perfect for development

### Sync Mode (For CI/CD)

```python
base_image = CodeBuildDockerImage(
    "base-receipt-agent",
    sync_mode=True,  # ← Sync
    image_tag="git-abc123",
)
```

- `pulumi up` waits for CodeBuild to complete
- Ensures builds are done before pipeline continues
- Slower but more predictable for CI/CD

The system auto-detects CI and uses sync mode!

## Edge Cases

### What if base build fails?

```
Time: 00:05 - base-dynamo CodeBuild starts
Time: 02:00 - ❌ base-dynamo build FAILS (syntax error)
Time: 02:00 - base-label still waiting for base-dynamo:git-abc123-dirty
Time: 62:00 - ⏰ base-label CodeBuild TIMEOUT (60 min)
Time: 62:00 - ❌ base-label build FAILS
```

**Result:** Cascade failure. You'll see failed CodeBuild jobs in AWS console.

**Fix:** Fix the base package, run `pulumi up` again.

### What if you run pulumi up twice quickly?

```
Run 1:
  00:00 - Trigger base-dynamo with tag git-abc123-dirty
  00:05 - pulumi up returns

Run 2 (before Run 1 finishes):
  00:10 - Pulumi sees same tag (git-abc123-dirty)
  00:10 - No changes detected
  00:10 - Nothing rebuilds ✅
```

**Result:** Idempotent! Second run is a no-op.

### What if you modify receipt_dynamo again while builds are running?

```
Run 1:
  00:00 - Trigger builds with tag git-abc123-dirty
  00:05 - pulumi up returns
  [Builds running in background]

Run 2 (at 01:00, while Run 1 still building):
  01:00 - Tag changes to git-def456-dirty
  01:00 - Pulumi sees change
  01:00 - Triggers NEW builds with git-def456-dirty
  01:05 - pulumi up returns
  [Now TWO sets of builds running!]
```

**Result:** Both complete. The second set uses the newer code. Lambda functions get updated to the latest (git-def456-dirty).

## Benefits

✅ **Fast pulumi up** - Returns in ~5 seconds
✅ **No local builds** - Everything on AWS
✅ **Correct ordering** - Dependency chain preserved
✅ **Parallel builds** - Lambdas build simultaneously
✅ **Cost efficient** - Only rebuilds what changed
✅ **Laptop friendly** - Close laptop, builds continue

## Monitoring

Check build status:
```bash
# AWS Console
https://console.aws.amazon.com/codesuite/codebuild/projects

# AWS CLI
aws codebuild list-builds-for-project --project-name base-receipt-agent-img-dev

# Pulumi
pulumi stack output  # Shows current image URIs
```

## Summary

The "magic" is that:
1. Pulumi triggers builds level-by-level (respecting dependencies)
2. Each trigger is async (returns immediately)
3. Docker waits for base images to appear in ECR (automatic)
4. Everything builds in the correct order, asynchronously!

You get both **speed** (async) and **correctness** (dependency ordering)! 🎯

