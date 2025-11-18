# Create Labels Step Function - Refactoring Plan

## Current State Analysis

### What We Have Now
- ✅ Basic step function structure
- ✅ List receipts Lambda (zip-based) - **GOOD**
- ✅ Process receipt Lambda - **CONVERTED to container-based** but needs refactoring
- ✅ Dockerfile created
- ❌ **NOT using ComponentResource pattern** (like other LangGraph/Ollama step functions)
- ❌ **Function-based approach** instead of class-based
- ❌ Missing proper resource organization

### What Other LangGraph/Ollama Step Functions Use

**Pattern**: `ValidatePendingLabelsStepFunction` and `ValidateMetadataStepFunction`

1. **ComponentResource Class** - Encapsulates all resources
2. **CodeBuildDockerImage** - For container Lambda with LangGraph/Ollama dependencies
3. **Proper Structure**:
   - IAM roles (Lambda execution role, Step Function role)
   - Zip-based Lambda for list operation
   - Container-based Lambda using CodeBuildDockerImage
   - Step Function state machine with logging
   - CloudWatch Log Groups
   - S3 bucket for manifests (if needed)
   - Proper parent/child resource relationships

## Refactoring Plan

### Phase 1: Convert to ComponentResource Pattern ✅

**Goal**: Match the structure of `ValidatePendingLabelsStepFunction`

#### Step 1.1: Create ComponentResource Class
- [x] Create `CreateLabelsStepFunction` class extending `ComponentResource`
- [x] Move all resource creation into `__init__` method
- [x] Accept parameters: `name`, `dynamodb_table_name`, `dynamodb_table_arn`
- [x] Use proper resource organization with `opts=ResourceOptions(parent=self)`

#### Step 1.2: Organize IAM Roles
- [x] Create Lambda execution role (`{name}-lambda-role`)
- [x] Create Step Function role (`{name}-sfn-role`)
- [x] Add ECR permissions for container Lambda
- [x] Add DynamoDB access policy
- [x] Add Step Function invoke permissions

#### Step 1.3: Create Lambda Functions
- [x] **List Lambda**: Zip-based (keep as-is, lightweight)
- [x] **Process Lambda**: Container-based using `CodeBuildDockerImage`
  - [x] Use `CodeBuildDockerImage` component
  - [x] Configure with proper lambda_config dict
  - [x] Set platform to `linux/arm64`
  - [x] Dependencies: `receipt_dynamo` and `receipt_label[full]`

#### Step 1.4: Create Step Function
- [x] Create state machine with proper definition
- [x] Add CloudWatch Log Group for execution logs
- [x] Configure logging (level: ALL, include_execution_data: true)
- [x] Set type to STANDARD (for long-running executions)

#### Step 1.5: Export Outputs
- [x] Export state_machine_arn
- [x] Export lambda ARNs
- [x] Register outputs with ComponentResource

### Phase 2: Update Main Infrastructure ✅

#### Step 2.1: Update `infra/__main__.py`
- [x] Import `CreateLabelsStepFunction` class
- [x] Instantiate with proper parameters
- [x] Export ARNs

### Phase 3: Verify CodeBuild Pipeline Setup ✅

#### Step 3.1: Verify Dockerfile
- [x] Multi-stage build (dependencies + handler)
- [x] Installs `receipt_dynamo` and `receipt_label[full]`
- [x] Copies handler to correct location
- [x] Sets CMD to `handler.lambda_handler`

#### Step 3.2: Verify CodeBuildDockerImage Configuration
- [x] `dockerfile_path`: Points to correct Dockerfile
- [x] `build_context_path`: "." (project root)
- [x] `source_paths`: None (uses default rsync)
- [x] `lambda_function_name`: Proper naming
- [x] `lambda_config`: Complete with role_arn, timeout, memory, environment
- [x] `platform`: "linux/arm64"

### Phase 4: Testing & Validation

#### Step 4.1: Deploy and Test
- [ ] Deploy infrastructure with `pulumi up`
- [ ] Verify CodeBuild pipeline runs successfully
- [ ] Verify container image is pushed to ECR
- [ ] Verify Lambda function is created/updated with container image
- [ ] Test step function execution

#### Step 4.2: Verify Dependencies
- [ ] Check Lambda logs for import errors
- [ ] Verify LangGraph/Ollama dependencies are available
- [ ] Test with a single receipt first

## Key Differences from Current Implementation

### Current (Function-based)
```python
def create_labels_state_machine(...):
    # Creates resources at module level
    lambda_role = aws.iam.Role(...)
    create_labels_list_lambda = aws.lambda_.Function(...)
    # ...
    return state_machine
```

### Target (ComponentResource-based)
```python
class CreateLabelsStepFunction(ComponentResource):
    def __init__(self, name, *, dynamodb_table_name, ...):
        super().__init__(...)
        # All resources created here with proper parent relationships
        self.state_machine = ...
        self.state_machine_arn = ...
```

## CodeBuild Pipeline Flow

1. **Upload Context** (`command.local.Command`):
   - Calculates content hash of Dockerfile + source files
   - Uploads build context to S3 (if changed)
   - Skips upload if hash matches (fast pulumi up)

2. **CodePipeline**:
   - Source stage: Downloads context.zip from S3
   - Build stage: Runs CodeBuild project

3. **CodeBuild**:
   - Logs into ECR
   - Builds Docker image with multi-stage caching
   - Pushes to ECR with content-based tags
   - Updates Lambda function code (if exists)

4. **Lambda Function**:
   - Created with bootstrap image initially (if Docker available locally)
   - Updated by CodeBuild with actual image after build completes

## Benefits of ComponentResource Pattern

1. **Better Organization**: All related resources grouped together
2. **Proper Dependencies**: Parent/child relationships ensure correct ordering
3. **Reusability**: Can instantiate multiple times if needed
4. **Consistency**: Matches pattern used by other step functions
5. **Maintainability**: Easier to understand and modify

## Next Steps

1. ✅ Refactor infrastructure.py to use ComponentResource
2. ✅ Update Dockerfile and handler
3. ✅ Update __main__.py to use new class
4. ⏳ Deploy and test
5. ⏳ Verify CodeBuild pipeline works correctly

