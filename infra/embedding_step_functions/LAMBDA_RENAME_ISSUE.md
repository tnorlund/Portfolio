# Lambda Rename Issue

## Problem

When renaming Lambda functions (e.g., `embedding-line-poll` → `embedding-poll-lines`), Pulumi treats them as new resources. This causes:

1. **New ECR repositories** to be created for the new names
2. **New Lambda functions** to be attempted with Docker images that don't exist yet
3. **Bootstrap image push** may fail (Docker not available, network issues, etc.)
4. **Lambda creation fails** because the image doesn't exist in the new ECR repo

## Error

```
InvalidParameterValueException: Source image ... does not exist. Provide a valid source image.
```

## Solutions

### Option 1: Use Pulumi State Rename (Recommended)

Rename the resources in Pulumi state instead of creating new ones:

```bash
cd /Users/tnorlund/Portfolio/infra

# Get the URNs of the old Lambda functions
pulumi stack --show-urns | grep "embedding-line-poll-lambda-dev"

# Rename the resources in Pulumi state
pulumi state rename \
  'urn:pulumi:dev::portfolio::custom:embedding:LambdaFunctions$codebuild-docker:embedding-line-poll-docker$aws:lambda/function:Function::embedding-line-poll-docker-function' \
  'urn:pulumi:dev::portfolio::custom:embedding:LambdaFunctions$codebuild-docker:embedding-poll-lines-docker$aws:lambda/function:Function::embedding-poll-lines-docker-function'

# Repeat for other renamed functions
```

### Option 2: Make Bootstrap Image Push Required

Modify `codebuild_docker_image.py` to fail if bootstrap image push fails:

```python
# In _push_bootstrap_image, change exit codes:
# Instead of `exit 0` on failure, use `exit 1`
```

### Option 3: Wait for CodeBuild Pipeline

Use sync mode or make Lambda creation wait for the CodeBuild pipeline to complete:

```python
# In _create_lambda_function, always wait for pipeline:
depends_on_list = [pipeline_trigger_cmd] if pipeline_trigger_cmd else [bootstrap_cmd]
```

## Current Status

The CodeBuild component has been updated to:
- Always wait for bootstrap_cmd to complete (ensures ECR repo exists)
- Support lambda_aliases parameter for Pulumi resource aliases

However, the bootstrap push can still fail gracefully, causing Lambda creation to fail.

## Recommended Next Steps

1. **Use Option 1 (State Rename)** - Cleanest approach, no resource recreation
2. If state rename is not possible, **manually push bootstrap images** before running `pulumi up`:
   ```bash
   # For each renamed function, push bootstrap image manually
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ECR_REPO_URL>
   docker pull public.ecr.aws/lambda/python:3.12-arm64
   docker tag public.ecr.aws/lambda/python:3.12-arm64 <ECR_REPO_URL>:latest
   docker push <ECR_REPO_URL>:latest
   ```

