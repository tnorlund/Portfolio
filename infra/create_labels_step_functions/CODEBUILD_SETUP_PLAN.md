# CodeBuild Pipeline Setup Plan for Create Labels Step Function

## Overview

This document outlines how the CodeBuild pipeline approach works for LangGraph/Ollama step functions and the setup plan for the Create Labels step function.

## How CodeBuild Pipeline Works

### Architecture Flow

```
Pulumi Up
  ↓
1. CodeBuildDockerImage Component Created
   - Creates ECR repository
   - Creates S3 bucket for build artifacts
   - Creates CodeBuild project
   - Creates CodePipeline
   ↓
2. Upload Context (command.local.Command)
   - Calculates content hash of Dockerfile + source files
   - Uploads context.zip to S3 (if hash changed)
   - Skips upload if hash matches (fast pulumi up)
   ↓
3. Trigger Pipeline (async by default)
   - Starts CodePipeline execution
   - Pulumi continues (doesn't wait)
   ↓
4. CodePipeline Execution
   - Source Stage: Downloads context.zip from S3
   - Build Stage: Runs CodeBuild project
   ↓
5. CodeBuild Execution
   - Logs into ECR
   - Builds Docker image (multi-stage with caching)
   - Pushes to ECR with content-based tags
   - Updates Lambda function code (if exists)
   ↓
6. Lambda Function
   - Created with bootstrap image initially (if Docker available locally)
   - Updated by CodeBuild with actual image after build completes
```

### Key Components

#### 1. CodeBuildDockerImage Component
**Location**: `infra/codebuild_docker_image.py`

**What it does**:
- Creates ECR repository for Docker images
- Creates S3 bucket for build artifacts
- Creates CodeBuild project with Docker support
- Creates CodePipeline to orchestrate builds
- Handles async/sync build modes
- Updates Lambda function after build completes

**Key Parameters**:
- `dockerfile_path`: Path to Dockerfile (relative to project root)
- `build_context_path`: Build context path (usually "." for project root)
- `source_paths`: Specific paths to include (None = default rsync)
- `lambda_function_name`: Name of Lambda function to create/update
- `lambda_config`: Dict with Lambda configuration (role_arn, timeout, memory, environment)
- `platform`: "linux/arm64" or "linux/amd64"

#### 2. Content Hash Calculation
- Calculates SHA256 hash of:
  - Dockerfile content
  - Source files (receipt_dynamo, receipt_label packages)
  - Handler code
- Only uploads to S3 if hash changed
- Enables fast `pulumi up` when nothing changed

#### 3. Build Context Upload
- Uses `rsync` to create minimal build context
- Includes only:
  - `receipt_dynamo/` package
  - `receipt_label/` package
  - Handler directory (`infra/create_labels_step_functions/handlers/`)
- Excludes: `__pycache__`, `.git`, etc.
- Creates `context.zip` and uploads to S3

#### 4. CodeBuild Buildspec
- **pre_build**: Login to ECR
- **build**: Build Docker image with BuildKit caching
- **post_build**: Push to ECR, update Lambda function

## Current Implementation Status

### ✅ What's Already Done

1. **Dockerfile Created** (`infra/create_labels_step_functions/lambdas/Dockerfile`)
   - ✅ Multi-stage build
   - ✅ Installs receipt_dynamo and receipt_label[full]
   - ✅ Copies handler to correct location
   - ✅ Sets CMD to handler.lambda_handler

2. **Handler Updated** (`infra/create_labels_step_functions/handlers/process_receipt.py`)
   - ✅ Has `lambda_handler` function for container Lambda
   - ✅ Uses LangGraph/Ollama workflow
   - ✅ Sets labels to PENDING status

3. **Infrastructure Updated** (`infra/create_labels_step_functions/infrastructure.py`)
   - ✅ Uses CodeBuildDockerImage component
   - ✅ Container Lambda configured
   - ✅ ECR permissions added

### ⚠️ What Needs Review/Refactoring

1. **Structure Pattern**
   - Current: Function-based (`create_labels_state_machine()`)
   - Other LangGraph/Ollama: ComponentResource-based (`ValidatePendingLabelsStepFunction`)
   - **Decision**: Keep function-based (like Currency Validation) OR refactor to ComponentResource?

2. **Lambda Function Name**
   - Current: `"create-labels-process"` (hardcoded)
   - Should be: `f"{name}-create-labels-process"` (parameterized)
   - **Issue**: May conflict if multiple stacks

3. **Resource Organization**
   - Current: Resources created at module level
   - Better: Use ComponentResource with proper parent relationships
   - **Benefit**: Better dependency management, easier to understand

## Comparison: Working Examples

### Validate Pending Labels (ComponentResource Pattern)

```python
class ValidatePendingLabelsStepFunction(ComponentResource):
    def __init__(self, name, *, dynamodb_table_name, ...):
        super().__init__(...)
        stack = pulumi.get_stack()

        # IAM roles
        lambda_exec_role = Role(f"{name}-lambda-role", ...)
        sfn_role = Role(f"{name}-sfn-role", ...)

        # Zip-based list Lambda
        list_lambda = Function(f"{name}-list-pending-labels", ...)

        # Container-based process Lambda
        process_lambda_config = {
            "role_arn": lambda_exec_role.arn,
            "timeout": 900,
            "memory_size": 2048,
            "ephemeral_storage": 10240,  # 10 GB for ChromaDB
            "environment": {...}
        }

        process_docker_image = CodeBuildDockerImage(
            f"{name}-val-receipt-img",
            dockerfile_path="infra/validate_pending_labels/lambdas/Dockerfile",
            build_context_path=".",
            source_paths=None,
            lambda_function_name=f"{name}-validate-receipt",  # Parameterized!
            lambda_config=process_lambda_config,
            platform="linux/arm64",
        )

        process_lambda = process_docker_image.lambda_function

        # Step Function
        self.state_machine = StateMachine(...)
```

### Currency Validation (Function Pattern)

```python
def create_currency_validation_state_machine(...):
    # Module-level resources
    lambda_role = aws.iam.Role(...)
    list_lambda = aws.lambda_.Function(...)
    process_lambda = aws.lambda_.Function(...)  # Zip-based!

    # Step Function
    state_machine = aws.sfn.StateMachine(...)
    return state_machine
```

**Note**: Currency Validation uses **zip-based** Lambda (with layers), not container-based. That's why it doesn't need CodeBuild.

## Game Plan: Refactor to ComponentResource Pattern

### Option A: Keep Function-Based (Current)
**Pros**:
- Simpler, matches Currency Validation
- Already working structure
- Less refactoring needed

**Cons**:
- Doesn't match other LangGraph/Ollama step functions
- Harder to manage dependencies
- Less reusable

### Option B: Refactor to ComponentResource (Recommended)
**Pros**:
- Matches ValidatePendingLabels and ValidateMetadata patterns
- Better resource organization
- Proper parent/child relationships
- More maintainable
- Consistent with other LangGraph/Ollama step functions

**Cons**:
- More refactoring work
- Need to update __main__.py

## Recommended Refactoring Steps

### Step 1: Convert to ComponentResource Class ✅ (Already Started)

```python
class CreateLabelsStepFunction(ComponentResource):
    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__(f"{__name__}-{name}", name, None, opts)
        stack = pulumi.get_stack()

        # Move all resource creation here
        # Use proper parent relationships
        # Export state_machine_arn, lambda_arns
```

### Step 2: Parameterize Lambda Function Name ✅

```python
lambda_function_name=f"{name}-create-labels-process"  # Instead of hardcoded
```

### Step 3: Add CloudWatch Log Group ✅

```python
log_group = LogGroup(
    f"{name}-sf-logs",
    name=f"/aws/stepfunctions/{name}-sf",
    retention_in_days=14,
)
```

### Step 4: Update Step Function Definition ✅

```python
logging_config = log_group.arn.apply(
    lambda arn: StateMachineLoggingConfigurationArgs(
        level="ALL",
        include_execution_data=True,
        log_destination=f"{arn}:*",
    )
)

self.state_machine = StateMachine(
    ...,
    logging_configuration=logging_config,
    type="STANDARD",  # For long-running executions
)
```

### Step 5: Update __main__.py ✅

```python
from create_labels_step_functions import CreateLabelsStepFunction

create_labels_sf = CreateLabelsStepFunction(
    f"create-labels-{stack}",
    dynamodb_table_name=dynamodb_table.name,
    dynamodb_table_arn=dynamodb_table.arn,
)

pulumi.export("create_labels_sf_arn", create_labels_sf.state_machine_arn)
```

## CodeBuild Pipeline Configuration Details

### Dockerfile Location
- **Path**: `infra/create_labels_step_functions/lambdas/Dockerfile`
- **Build Context**: Project root (`.`)
- **Source Paths**: None (uses default rsync)

### Default Rsync Includes
When `source_paths=None`, CodeBuildDockerImage automatically includes:
- `receipt_dynamo/` package
- `receipt_label/` package
- Handler directory (`infra/create_labels_step_functions/handlers/`)

### Build Process
1. **Content Hash**: Calculates hash of Dockerfile + source files
2. **Upload Check**: Compares with stored hash in S3
3. **Skip if Unchanged**: Fast pulumi up if nothing changed
4. **Upload Context**: Creates context.zip with minimal files
5. **Trigger Pipeline**: Starts CodePipeline execution
6. **Build Image**: CodeBuild builds Docker image
7. **Push to ECR**: Pushes with content-based tag
8. **Update Lambda**: Updates Lambda function code

### Lambda Configuration
```python
lambda_config = {
    "role_arn": lambda_exec_role.arn,
    "timeout": 600,  # 10 minutes
    "memory_size": 1536,  # 1.5 GB
    "tags": {"environment": stack},
    "environment": {
        "DYNAMODB_TABLE_NAME": dynamodb_table_name,
        "OLLAMA_API_KEY": ollama_api_key,
        "LANGCHAIN_API_KEY": langchain_api_key,
        "LANGCHAIN_TRACING_V2": "true",
        "LANGCHAIN_ENDPOINT": "https://api.smith.langchain.com",
        "LANGCHAIN_PROJECT": "create-labels",
    },
}
```

## Verification Checklist

After refactoring, verify:

- [ ] CodeBuild project created
- [ ] ECR repository created
- [ ] S3 bucket for artifacts created
- [ ] CodePipeline created
- [ ] Lambda function created (with bootstrap image initially)
- [ ] Docker image builds successfully
- [ ] Image pushed to ECR
- [ ] Lambda function updated with container image
- [ ] Step function can invoke Lambda
- [ ] Handler can import all dependencies
- [ ] LangGraph/Ollama workflow runs successfully

## Next Steps

1. ✅ Review current implementation
2. ✅ Create refactoring plan
3. ⏳ Refactor to ComponentResource pattern (if desired)
4. ⏳ Deploy and test
5. ⏳ Verify CodeBuild pipeline works
6. ⏳ Test step function execution

