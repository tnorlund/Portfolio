# Testing Async Build System

## Initial Deployment ✅

Your `pulumi up` succeeded! You should see these outputs:
- `dynamo_base_image_uri`: `....:git-6642d5b30`
- `label_base_image_uri`: `....:git-ff823670d`
- `agent_base_image_uri`: `....:git-<hash>`

Duration: ~1m24s (first build creates all ECR repos + CodeBuild projects)

## Test Plan

We'll test three scenarios in order of complexity:

### Test 1: Handler-Only Change (Simplest)
**Tests:** Lambda-only rebuild, no base image changes

### Test 2: receipt_agent Change (Medium)
**Tests:** Lambda rebuild with package change (not in base)

### Test 3: receipt_dynamo Change (Hardest!)
**Tests:** Base image rebuild + Lambda rebuild + dependency propagation

---

## Test 1: Handler-Only Change

### What to Change
Modify the Lambda handler code (not any packages):

```bash
vim infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py
```

Add a comment at the top:
```python
# Testing async build system - handler change
```

### Expected Behavior

**Local:**
```bash
$ pulumi up
Previewing update (dev):

Type                          Name                                Plan
~   custom:codebuild:Docker   metadata-harmonizer-dev-harmonize   update

Resources:
    ~ 1 to update

Do you want to perform this update? yes
Updating (dev):

Type                          Name                                Status
~   custom:codebuild:Docker   metadata-harmonizer-dev-harmonize   updated

Duration: 10-15s  ← Fast!
```

**What's happening:**
- Content hash changes (handler code modified)
- S3 upload (~5 sec)
- CodeBuild triggered (async)
- `pulumi up` returns immediately

**AWS (Background):**
- CodeBuild builds Lambda image (~1.5 min)
- Uses existing base image (no rebuild needed!)
- Updates Lambda function

### Verification

1. **Check CodeBuild started:**
```bash
aws codebuild list-builds-for-project \
  --project-name metadata-harmonizer-dev-harmonize-img-builder \
  --sort-order DESCENDING \
  --max-items 1
```

2. **Watch build progress:**
```bash
# Get the build ID from above, then:
aws codebuild batch-get-builds --ids <build-id>
```

3. **Check if Lambda updated (after ~2-3 min):**
```bash
aws lambda get-function \
  --function-name metadata-harmonizer-dev-harmonize-metadata \
  --query 'Code.ImageUri'
```

### Success Criteria
✅ `pulumi up` returned in <30 seconds
✅ CodeBuild job started
✅ Lambda image updated (check after 2-3 min)
✅ Base images NOT rebuilt

---

## Test 2: receipt_agent Change

### What to Change
Modify a package that's used by Lambdas but NOT included in base images:

```bash
vim receipt_agent/validate.py
```

Add a comment:
```python
# Testing async build - receipt_agent change
```

### Expected Behavior

**Local:**
```bash
$ pulumi up
Previewing update (dev):

Type                          Name                                Plan
~   custom:codebuild:Docker   metadata-harmonizer-dev-harmonize   update

Resources:
    ~ 1 to update

Duration: 10-15s  ← Fast!
```

**What's happening:**
- Content hash changes (receipt_agent in source_paths)
- Base images DON'T change (receipt_agent not in bases)
- S3 upload (~5 sec)
- CodeBuild triggered (async)
- `pulumi up` returns immediately

**AWS (Background):**
- CodeBuild builds Lambda image (~1.5 min)
- Installs receipt_agent on top of base
- Updates Lambda function

### Verification

Same as Test 1, but also verify base images weren't rebuilt:

```bash
# Check base image tags haven't changed
pulumi stack output agent_base_image_uri
# Should still be: ....:git-ff823670d (or whatever it was before)

# Verify no new base image builds
aws codebuild list-builds-for-project \
  --project-name base-receipt-agent-dev-img-builder \
  --sort-order DESCENDING \
  --max-items 3
# Should not show a new build from today
```

### Success Criteria
✅ `pulumi up` returned in <30 seconds
✅ Lambda CodeBuild started
✅ Lambda image updated
✅ Base images NOT rebuilt ← Important!

---

## Test 3: receipt_dynamo Change (THE HARD ONE!)

### What to Change
Modify a package that's included in ALL base images:

```bash
vim receipt_dynamo/client.py
```

Add a comment at the top:
```python
# Testing async build - receipt_dynamo change
```

### Expected Behavior

**Local:**
```bash
$ pulumi up
Previewing update (dev):

Type                          Name                                Plan
~   custom:codebuild:Docker   base-receipt-dynamo-dev             update
~   custom:codebuild:Docker   base-receipt-label-dev              update
~   custom:codebuild:Docker   base-receipt-agent-dev              update
~   custom:codebuild:Docker   metadata-harmonizer-dev-harmonize   update
~   custom:codebuild:Docker   create-labels-dev-create            update

Resources:
    ~ 5 to update  ← All base images + dependent Lambdas!

Duration: 15-20s  ← Still fast!
```

**What's happening:**
- All base image tags change: `git-old-dirty` → `git-<new>-dirty`
- Pulumi sees `image_uri` Outputs changed
- Marks all dependent Lambdas for update
- Uploads contexts to S3 (~10 sec, some parallel)
- Triggers all CodeBuilds (async)
- `pulumi up` returns immediately

**AWS (Background):**
```
00:00 - All 3 base images start building (PARALLEL)
03:30 - base-dynamo ✅
05:00 - base-label ✅
06:30 - base-agent ✅

06:30 - Lambda CodeBuilds were triggered at 00:00
        Docker was retrying pulls...
        Now base-agent:git-<new>-dirty available!

06:30 - All Lambdas start building (PARALLEL)
08:00 - All Lambdas ✅
```

### Verification

**1. Check all base images were triggered:**
```bash
# Base dynamo
aws codebuild list-builds-for-project \
  --project-name base-receipt-dynamo-dev-img-builder \
  --sort-order DESCENDING --max-items 1

# Base label
aws codebuild list-builds-for-project \
  --project-name base-receipt-label-dev-img-builder \
  --sort-order DESCENDING --max-items 1

# Base agent
aws codebuild list-builds-for-project \
  --project-name base-receipt-agent-dev-img-builder \
  --sort-order DESCENDING --max-items 1
```

**2. Check Lambda CodeBuild was triggered:**
```bash
aws codebuild list-builds-for-project \
  --project-name metadata-harmonizer-dev-harmonize-img-builder \
  --sort-order DESCENDING --max-items 1
```

**3. Watch the cascade (this is the magic!):**

Wait 3-4 minutes, then check if base images are in ECR:
```bash
# Check base-agent image with new tag
pulumi stack output agent_base_image_uri
# Note the new tag: ....:git-<new>-dirty

# Verify it's in ECR
aws ecr describe-images \
  --repository-name base-receipt-agent-dev-repo-<id> \
  --image-ids imageTag=git-<new>-dirty
```

Wait 8 minutes total, then check Lambda:
```bash
aws lambda get-function \
  --function-name metadata-harmonizer-dev-harmonize-metadata \
  --query 'Code.ImageUri'
# Should show new digest
```

**4. Check CodeBuild logs to see Docker waiting:**

Get the Lambda build ID, then view logs:
```bash
aws codebuild batch-get-builds --ids <lambda-build-id> \
  --query 'builds[0].logs.deepLink' --output text
```

You might see logs like:
```
Step 1/8 : FROM 681647709217.dkr.ecr.us-east-1.amazonaws.com/base-receipt-agent-dev:git-<new>-dirty
Error response from daemon: manifest for ....:git-<new>-dirty not found
# ... retrying ...
# ... eventually succeeds when base finishes!
```

### Success Criteria
✅ `pulumi up` returned in <30 seconds
✅ All 3 base image CodeBuilds started
✅ Lambda CodeBuilds started
✅ Base images built in parallel (~3-4 min)
✅ Lambda builds waited for base, then completed (~8 min total)
✅ All changes landed in AWS

---

## Quick Verification Script

Run this after each test to see the current state:

```bash
#!/bin/bash
echo "=== Base Image Tags ==="
pulumi stack output dynamo_base_image_uri
pulumi stack output label_base_image_uri
pulumi stack output agent_base_image_uri

echo -e "\n=== Recent Base Image Builds ==="
aws codebuild list-builds-for-project \
  --project-name base-receipt-agent-dev-img-builder \
  --sort-order DESCENDING --max-items 3 \
  --query 'ids' --output table

echo -e "\n=== Recent Lambda Builds ==="
aws codebuild list-builds-for-project \
  --project-name metadata-harmonizer-dev-harmonize-img-builder \
  --sort-order DESCENDING --max-items 3 \
  --query 'ids' --output table

echo -e "\n=== Current Lambda Image ==="
aws lambda get-function \
  --function-name metadata-harmonizer-dev-harmonize-metadata \
  --query 'Code.ImageUri' --output text
```

---

## Troubleshooting

### "pulumi up took too long"
- Check if you're accidentally in sync mode: `echo $PULUMI_DOCKER_BUILD_SYNC`
- Should be unset or "false" for local dev

### "CodeBuild not starting"
- Check CloudWatch logs: `aws codebuild list-builds --sort-order DESCENDING`
- Verify IAM permissions
- Check S3 bucket for context.zip

### "Lambda build failing with 'manifest not found'"
- This is EXPECTED initially!
- Docker will retry
- Check if base image build failed:
  ```bash
  aws codebuild batch-get-builds --ids <base-build-id> \
    --query 'builds[0].buildStatus'
  ```

### "Base image built but Lambda still failing"
- Check if base image tag matches Dockerfile:
  ```bash
  pulumi stack output agent_base_image_uri
  # vs.
  aws ecr list-images --repository-name base-receipt-agent-dev-repo-<id>
  ```

---

## After Testing

Once all tests pass, commit and revert your test changes:

```bash
# Revert test changes
git checkout -- receipt_dynamo/client.py
git checkout -- receipt_agent/validate.py
git checkout -- infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py

# Run one final pulumi up to ensure no changes
pulumi up
# Should show: "no changes"
```

If it shows changes, the git-based hashing detected the reverted files. This is actually GOOD - it means the system works! The tags will revert to the original values.

---

## Summary

| Test | Change | pulumi up | AWS Time | Base Rebuilt? |
|------|--------|-----------|----------|---------------|
| 1. Handler | Lambda code | ~10s | ~1.5 min | No ✅ |
| 2. Agent | receipt_agent | ~10s | ~1.5 min | No ✅ |
| 3. Dynamo | receipt_dynamo | ~15s | ~8 min | Yes ✅ |

All tests should complete with:
- ✅ Fast local `pulumi up` (<30 sec)
- ✅ Async builds on AWS
- ✅ Correct dependency propagation
- ✅ No local Docker builds (laptop cool!)

