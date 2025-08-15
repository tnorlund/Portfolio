# Testing FastLambdaLayer Component

This document explains how to test the `FastLambdaLayer` component independently from the main infrastructure stack.

## Stack Isolation

This test setup is **completely isolated** from the main `dev` and `prod` stacks:

- **Unique Stack Name**: Automatically generated as `test-layer-<username>-<branch>`
  - Example: `test-layer-tnorlund-feat-remove-docker-containers`
- **Isolated Resources**: All AWS resources are prefixed with the stack name
- **No Conflicts**: Can run simultaneously with other developers on different branches
- **Safe Testing**: Won't interfere with `dev` or `prod` stacks

## Quick Start

```bash
cd infra/

# Run basic test (async mode - fast)
./test_layer_stack.sh

# Run with sync mode (waits for build completion)
./test_layer_stack.sh --sync

# Force rebuild even if no changes detected
./test_layer_stack.sh --force-rebuild

# Clean up after testing
./test_layer_stack.sh --destroy-after

# Use a custom stack name (overrides auto-generated name)
./test_layer_stack.sh --stack my-custom-test
```

## Files Created for Testing

1. **`test_fast_lambda_layer.py`** - Minimal Pulumi program that creates:
   - One FastLambdaLayer instance
   - A test Lambda function that uses the layer
   - Basic IAM roles and policies

2. **`Pulumi.test-layer.yaml`** - Configuration for the test stack

3. **`test_layer_stack.sh`** - Convenience script to run tests

## Check Your Stack Name

To see what stack name will be used:

```bash
cd infra/

# Show the auto-generated stack name
echo "Stack name: test-layer-$(whoami | sed 's/[^a-zA-Z0-9-]/-/g' | tr '[:upper:]' '[:lower:]')-$(git rev-parse --abbrev-ref HEAD | sed 's/[^a-zA-Z0-9-]/-/g' | tr '[:upper:]' '[:lower:]')"

# List all stacks (yours will be prefixed with test-layer)
pulumi stack ls | grep test-layer
```

## Manual Testing

If you prefer to run commands manually:

```bash
cd infra/

# Get your unique stack name
BRANCH=$(git rev-parse --abbrev-ref HEAD | sed 's/[^a-zA-Z0-9-]/-/g' | tr '[:upper:]' '[:lower:]')
USER=$(whoami | sed 's/[^a-zA-Z0-9-]/-/g' | tr '[:upper:]' '[:lower:]')
STACK_NAME="test-layer-${USER}-${BRANCH}"

# Initialize stack
pulumi stack init $STACK_NAME

# Set configuration
pulumi config set aws:region us-east-1
pulumi config set lambda-layer:sync-mode false
pulumi config set lambda-layer:force-rebuild false

# Deploy
pulumi up -f test_fast_lambda_layer.py

# Test the Lambda
aws lambda invoke \
  --function-name $(pulumi stack output test_lambda_name | tr -d '"') \
  --payload '{"test": true}' \
  /tmp/response.json

cat /tmp/response.json

# Clean up
pulumi destroy
pulumi stack rm $STACK_NAME
```

## Testing Different Packages

You can test building different packages:

```bash
# Test receipt_label package
pulumi config set test-package receipt_label
pulumi up

# Test receipt_upload package (includes Pillow)
pulumi config set test-package receipt_upload
pulumi up
```

## Monitoring Build Progress

When using async mode (default), the build happens in the background:

1. Check CodeBuild console: https://console.aws.amazon.com/codesuite/codebuild/projects
2. Look for projects named `test-fast-layer-build-py312-<stack>`
3. Check CodePipeline for the full pipeline: `test-fast-layer-pipeline-<stack>`

## Troubleshooting

### Build Fails
```bash
# Check CodeBuild logs
aws codebuild batch-get-builds --ids <build-id>

# Force rebuild
pulumi config set lambda-layer:force-rebuild true
pulumi up
```

### Layer Not Found
```bash
# Use sync mode to ensure build completes
pulumi config set lambda-layer:sync-mode true
pulumi up
```

### S3 Bucket Issues
```bash
# Check S3 bucket contents
aws s3 ls s3://fast-lambda-layer-test-fast-layer-artifacts-<stack>/
```

## How It Works

1. **Package Detection**: Calculates hash of Python files to detect changes
2. **Source Upload**: Uploads source code to S3 only when changes detected
3. **CodePipeline**: Orchestrates the build process:
   - Source stage: Gets code from S3
   - Build stage: Runs CodeBuild projects in parallel for each Python version
   - Deploy stage: Publishes layer and updates Lambda functions
4. **Build Mode**:
   - **Async** (default): Fast `pulumi up`, builds in background
   - **Sync**: Waits for build completion before finishing

## Next Steps

Once testing is complete, you can:
1. Integrate the working configuration into the main stack
2. Remove Docker-based layer building from the main infrastructure
3. Update all Lambda functions to use the new fast layers