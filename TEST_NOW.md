# Quick Test: Async Build System

## Your deployment succeeded! Now let's test the 3 scenarios.

## Test 1: Handler-Only Change (Easiest, 5 min)

### Step 1: Make a small change
```bash
# Add a comment to the Lambda handler
echo '# Test: handler-only change' | cat - infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py > temp && mv temp infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py
```

### Step 2: Deploy
```bash
cd infra
time pulumi up --yes
```

**Expected:**
- Should complete in **10-20 seconds** ⚡
- Shows: `~ 1 to update` (just the Lambda)
- No base image changes

### Step 3: Verify async build started
```bash
# Check CodeBuild (should show a build from just now)
aws codebuild list-builds-for-project \
  --project-name metadata-harmonizer-dev-harmonize-img-builder \
  --sort-order DESCENDING \
  --max-items 1
```

### Step 4: Wait 2-3 minutes and verify Lambda updated
```bash
aws lambda get-function \
  --function-name metadata-harmonizer-dev-harmonize-metadata \
  --query 'Configuration.LastModified'
# Should show a recent timestamp
```

✅ **Success if:** pulumi up < 30 sec, CodeBuild started, Lambda updated

---

## Test 2: receipt_agent Change (Medium, 5 min)

### Step 1: Change receipt_agent
```bash
echo '# Test: receipt_agent change' | cat - receipt_agent/validate.py > temp && mv temp receipt_agent/validate.py
```

### Step 2: Deploy
```bash
cd infra
time pulumi up --yes
```

**Expected:**
- Should complete in **10-20 seconds** ⚡
- Shows: `~ 1 to update` (just the Lambda)
- Base images NOT rebuilt

### Step 3: Verify
```bash
# Lambda should rebuild
aws codebuild list-builds-for-project \
  --project-name metadata-harmonizer-dev-harmonize-img-builder \
  --sort-order DESCENDING --max-items 1

# Base images should NOT have new builds
aws codebuild list-builds-for-project \
  --project-name base-receipt-agent-dev-img-builder \
  --sort-order DESCENDING --max-items 1
# ↑ Should show old build, not from today
```

✅ **Success if:** pulumi up < 30 sec, Lambda rebuilt, bases NOT rebuilt

---

## Test 3: receipt_dynamo Change (HARD ONE!, 15 min)

### Step 1: Change receipt_dynamo
```bash
echo '# Test: receipt_dynamo change - triggers cascade!' | cat - receipt_dynamo/client.py > temp && mv temp receipt_dynamo/client.py
```

### Step 2: Deploy
```bash
cd infra
time pulumi up --yes
```

**Expected:**
- Should complete in **15-25 seconds** ⚡
- Shows: `~ 5-6 to update` (3 base images + 2-3 Lambdas)
- All base image URIs change

### Step 3: Watch the cascade!
```bash
# Check all base images started building
for base in base-receipt-dynamo-dev base-receipt-label-dev base-receipt-agent-dev; do
  echo "=== $base ==="
  aws codebuild list-builds-for-project \
    --project-name "${base}-img-builder" \
    --sort-order DESCENDING --max-items 1
done

# Check Lambda started
aws codebuild list-builds-for-project \
  --project-name metadata-harmonizer-dev-harmonize-img-builder \
  --sort-order DESCENDING --max-items 1
```

### Step 4: Wait 8-10 minutes for everything to complete

Check status every 2 minutes:
```bash
# Watch the base image builds
aws codebuild batch-get-builds --ids <build-id-from-above> \
  --query 'builds[0].buildStatus'
```

### Step 5: Verify final state
```bash
# All base images should have new tags
cd infra
pulumi stack output | grep base_image_uri
# Should show new git hashes

# Lambda should be updated
aws lambda get-function \
  --function-name metadata-harmonizer-dev-harmonize-metadata \
  --query 'Configuration.LastModified'
```

✅ **Success if:** 
- pulumi up < 30 sec
- All 3 bases rebuilt (parallel)
- Lambdas rebuilt after bases finished
- Total AWS time ~8-10 min (you didn't wait!)

---

## Cleanup: Revert Test Changes

```bash
cd /Users/tnorlund/Portfolio
git checkout -- infra/metadata_harmonizer_step_functions/lambdas/harmonize_metadata.py
git checkout -- receipt_agent/validate.py
git checkout -- receipt_dynamo/client.py

# Verify no changes
git status
```

---

## The Magic Moment 🎯

**Test 3 is where you'll see the power!**

When you modify `receipt_dynamo`:
1. You wait **15-20 seconds** for `pulumi up` ⚡
2. Close your laptop! ☕
3. Come back in 10 minutes
4. Everything is deployed! 🎉

Without this system, you'd wait **5-10 minutes** for local Docker builds (laptop fans spinning!) then another 5-10 minutes for AWS builds.

**Savings: 5-10 minutes per deployment + no hot laptop!**
