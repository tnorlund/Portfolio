# Validate Pending Labels: Infrastructure Plan

## Overview

This document outlines the infrastructure needed for the `validate_pending_labels` Step Function, which validates PENDING `ReceiptWordLabel` entities using LangGraph + CoVe + ChromaDB similarity search.

## Architecture Components

### 1. Step Function
- **Name**: `validate-pending-labels-sf-{stack}`
- **Type**: Express Workflow (for faster execution)
- **States**:
  - `ListPendingLabels` (Lambda)
  - `CheckReceipts` (Choice)
  - `ProcessReceipts` (Map, MaxConcurrency: 10)
  - `ValidateReceipt` (Lambda, per receipt)

### 2. Lambda Functions

#### 2.1. `list_pending_labels` Lambda
- **Type**: Zip-based Lambda (lightweight, no ChromaDB needed)
- **Purpose**: Query DynamoDB for PENDING labels, group by receipt, create manifest
- **Handler**: `infra/validate_pending_labels/handlers/list_pending_labels.py`
- **Timeout**: 60 seconds
- **Memory**: 256 MB
- **Dependencies**: DynamoDB access only

#### 2.2. `validate_receipt` Lambda
- **Type**: Container-based Lambda (needs ChromaDB)
- **Purpose**: Validate PENDING labels for a single receipt using LangGraph + CoVe + ChromaDB
- **Handler**: `infra/validate_pending_labels/container/handler.py`
- **Dockerfile**: `infra/validate_pending_labels/container/Dockerfile`
- **Timeout**: 900 seconds (15 minutes)
- **Memory**: 2048 MB
- **Ephemeral Storage**: 10240 MB (10 GB for ChromaDB snapshot)
- **Dependencies**:
  - DynamoDB access
  - S3 access (ChromaDB bucket + manifest bucket)
  - LangGraph + CoVe (Ollama API)
  - ChromaDB snapshot download

### 3. IAM Roles

#### 3.1. Step Function Role
- **Name**: `validate-pending-labels-sf-role-{stack}`
- **Permissions**:
  - Invoke `list_pending_labels` Lambda
  - Invoke `validate_receipt` Lambda
  - Read/write S3 manifest bucket

#### 3.2. Lambda Execution Role
- **Name**: `validate-pending-labels-lambda-role-{stack}`
- **Permissions**:
  - Basic Lambda execution (CloudWatch Logs)
  - DynamoDB read/write access
  - S3 read access (ChromaDB bucket for snapshots)
  - S3 read/write access (manifest bucket)
  - VPC access (if VPC configured)

### 4. S3 Buckets

#### 4.1. Manifest Bucket
- **Purpose**: Store manifest files for Step Function execution
- **Lifecycle**: TTL-based cleanup (7 days)
- **Path**: `pending_labels/{execution_id}/manifest.json`

#### 4.2. ChromaDB Bucket (Existing)
- **Purpose**: Store ChromaDB snapshots
- **Access**: Read-only for Lambda (download snapshots)
- **Path**: `chromadb_snapshots/words/...`

### 5. Environment Variables

#### 5.1. `list_pending_labels` Lambda
```python
{
    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
    "S3_BUCKET": manifest_bucket.bucket,  # For manifest storage
}
```

#### 5.2. `validate_receipt` Lambda
```python
{
    "DYNAMODB_TABLE_NAME": dynamodb_table.name,
    "CHROMADB_BUCKET": chromadb_bucket_name,  # For ChromaDB snapshots
    "S3_BUCKET": manifest_bucket.bucket,  # For manifest downloads
    "OLLAMA_API_KEY": ollama_api_key,  # From Pulumi secrets
    "LANGCHAIN_API_KEY": langchain_api_key,  # From Pulumi secrets
    "GOOGLE_PLACES_API_KEY": google_places_api_key,  # From Pulumi secrets (optional, for metadata updates)
}
```

## Infrastructure Structure

```
infra/validate_pending_labels/
├── __init__.py
├── infrastructure.py              # Main Pulumi component
├── handlers/
│   ├── __init__.py
│   └── list_pending_labels.py    # Zip-based Lambda handler
└── container/
    ├── Dockerfile                 # Container Lambda Dockerfile
    └── handler/
        ├── __init__.py
        └── handler.py             # Container Lambda handler
```

## Implementation Details

### 1. Infrastructure Component (`infra/validate_pending_labels/infrastructure.py`)

```python
class ValidatePendingLabelsStepFunction(ComponentResource):
    """
    Step Function infrastructure for validating PENDING ReceiptWordLabel entities.

    Uses LangGraph + CoVe + ChromaDB similarity search to validate labels.
    """

    def __init__(
        self,
        name: str,
        *,
        dynamodb_table_name: pulumi.Input[str],
        dynamodb_table_arn: pulumi.Input[str],
        chromadb_bucket_name: pulumi.Input[str],
        chromadb_bucket_arn: pulumi.Input[str],
        vpc_subnet_ids: pulumi.Input[list[str]] | None = None,
        security_group_id: pulumi.Input[str] | None = None,
        opts: Optional[ResourceOptions] = None,
    ):
        # 1. Create manifest S3 bucket
        # 2. Create IAM roles (Step Function + Lambda)
        # 3. Create zip-based list_pending_labels Lambda
        # 4. Create container-based validate_receipt Lambda (using CodeBuildDockerImage)
        # 5. Create Step Function state machine
        # 6. Set up S3 lifecycle policy for cleanup
```

### 2. List Pending Labels Lambda (Zip-based)

**Handler**: `infra/validate_pending_labels/handlers/list_pending_labels.py`

```python
def handler(event, context):
    """
    Query DynamoDB for PENDING labels, group by receipt, create manifest.

    Returns:
        {
            "manifest_s3_key": "...",
            "manifest_s3_bucket": "...",
            "execution_id": "...",
            "total_receipts": 100,
            "total_pending_labels": 450,
            "receipt_indices": [0, 1, 2, ..., 99]
        }
    """
    # 1. Query DynamoDB for PENDING labels
    # 2. Group by (image_id, receipt_id)
    # 3. Create manifest with receipt identifiers
    # 4. Upload manifest to S3
    # 5. Return manifest info + receipt_indices array
```

### 3. Validate Receipt Lambda (Container-based)

**Handler**: `infra/validate_pending_labels/container/handler/handler.py`

```python
async def handler(event, context):
    """
    Validate PENDING labels for a single receipt.

    Input:
        {
            "index": 0,
            "manifest_s3_key": "...",
            "manifest_s3_bucket": "...",
            "execution_id": "..."
        }

    Returns:
        {
            "success": True,
            "index": 0,
            "image_id": "...",
            "receipt_id": 1,
            "labels_validated": 5,
            "labels_updated": 3,
            "labels_added": 2
        }
    """
    # 1. Download manifest from S3
    # 2. Look up receipt: manifest["receipts"][index]
    # 3. Fetch receipt data from DynamoDB
    # 4. Download ChromaDB snapshot (words collection)
    # 5. Run LangGraph + CoVe + ChromaDB validation
    # 6. Compare and update labels in DynamoDB
    # 7. Return results
```

**Dockerfile**: `infra/validate_pending_labels/container/Dockerfile`

```dockerfile
FROM public.ecr.aws/lambda/python:3.12-arm64

# Install dependencies
COPY receipt_label/ /var/task/receipt_label/
COPY receipt_dynamo/ /var/task/receipt_dynamo/
COPY infra/validate_pending_labels/container/handler/ ${LAMBDA_TASK_ROOT}/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

CMD ["handler.handler"]
```

### 4. Step Function Definition

```json
{
  "Comment": "Validate PENDING ReceiptWordLabel entities",
  "StartAt": "ListPendingLabels",
  "States": {
    "ListPendingLabels": {
      "Type": "Task",
      "Resource": "${ListPendingLabelsLambdaArn}",
      "ResultPath": "$.list_result",
      "Next": "CheckReceipts",
      "Retry": [...]
    },
    "CheckReceipts": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.list_result.total_receipts",
          "NumericGreaterThan": 0,
          "Next": "ProcessReceipts"
        }
      ],
      "Default": "NoReceipts"
    },
    "ProcessReceipts": {
      "Type": "Map",
      "ItemsPath": "$.list_result.receipt_indices",
      "MaxConcurrency": 10,
      "Parameters": {
        "index.$": "$$.Map.Item.Value",
        "manifest_s3_key.$": "$.list_result.manifest_s3_key",
        "manifest_s3_bucket.$": "$.list_result.manifest_s3_bucket",
        "execution_id.$": "$.list_result.execution_id"
      },
      "Iterator": {
        "StartAt": "ValidateReceipt",
        "States": {
          "ValidateReceipt": {
            "Type": "Task",
            "Resource": "${ValidateReceiptLambdaArn}",
            "End": true,
            "Retry": [...],
            "Catch": [...]
          }
        }
      },
      "End": true
    },
    "NoReceipts": {
      "Type": "Pass",
      "Result": "No receipts with PENDING labels",
      "End": true
    }
  }
}
```

## IAM Policies

### Step Function Role Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["lambda:InvokeFunction"],
      "Resource": [
        "${ListPendingLabelsLambdaArn}",
        "${ValidateReceiptLambdaArn}"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "${ManifestBucketArn}",
        "${ManifestBucketArn}/*"
      ]
    }
  ]
}
```

### Lambda Execution Role Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:BatchGetItem",
        "dynamodb:BatchWriteItem"
      ],
      "Resource": [
        "${DynamoDBTableArn}",
        "${DynamoDBTableArn}/index/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "${ChromaDBBucketArn}",
        "${ChromaDBBucketArn}/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "${ManifestBucketArn}",
        "${ManifestBucketArn}/*"
      ]
    }
  ]
}
```

## CodeBuildDockerImage Configuration

For the `validate_receipt` container Lambda:

```python
validate_receipt_lambda_config = {
    "role_arn": lambda_exec_role.arn,
    "timeout": 900,  # 15 minutes
    "memory_size": 2048,  # 2 GB
    "ephemeral_storage": 10240,  # 10 GB for ChromaDB snapshot
    "environment": {
        "DYNAMODB_TABLE_NAME": dynamodb_table_name,
        "CHROMADB_BUCKET": chromadb_bucket_name,
        "S3_BUCKET": manifest_bucket.bucket,
        "OLLAMA_API_KEY": ollama_api_key,  # From Pulumi secrets
        "LANGCHAIN_API_KEY": langchain_api_key,  # From Pulumi secrets
        "GOOGLE_PLACES_API_KEY": google_places_api_key,  # From Pulumi secrets (optional)
    },
}

# Add VPC config if provided
if vpc_subnet_ids and security_group_id:
    validate_receipt_lambda_config["vpc_config"] = {
        "subnet_ids": vpc_subnet_ids,
        "security_group_ids": [security_group_id],
    }

# Create container Lambda using CodeBuildDockerImage
validate_receipt_docker_image = CodeBuildDockerImage(
    f"{name}-validate-receipt-image",
    dockerfile_path="infra/validate_pending_labels/container/Dockerfile",
    build_context_path=".",  # Project root
    source_paths=None,  # Use default rsync with exclusions
    lambda_function_name=f"{name}-validate-receipt-{stack}",
    lambda_config=validate_receipt_lambda_config,
    platform="linux/arm64",
    opts=ResourceOptions(parent=self, depends_on=[lambda_exec_role]),
)

validate_receipt_lambda = validate_receipt_docker_image.lambda_function
```

## Pulumi Secrets

API keys are loaded from Pulumi secrets:

```python
config = Config("portfolio")
ollama_api_key = config.require_secret("OLLAMA_API_KEY")
langchain_api_key = config.require_secret("LANGCHAIN_API_KEY")
google_places_api_key = config.require_secret("GOOGLE_PLACES_API_KEY")  # Optional
```

## S3 Lifecycle Policy

Manifest bucket cleanup (7-day TTL):

```python
aws.s3.BucketLifecycleConfigurationV2(
    f"{name}-manifest-bucket-lifecycle",
    bucket=manifest_bucket.id,
    rules=[
        aws.s3.BucketLifecycleConfigurationV2RuleArgs(
            id="delete-old-manifests",
            status="Enabled",
            expiration=aws.s3.BucketLifecycleConfigurationV2RuleExpirationArgs(
                days=7
            ),
            filter=aws.s3.BucketLifecycleConfigurationV2RuleFilterArgs(
                prefix="pending_labels/"
            ),
        )
    ],
    opts=ResourceOptions(parent=self),
)
```

## Integration Points

### 1. DynamoDB Table
- **Table**: `ReceiptWordLabel` entities
- **Query**: `validation_status="PENDING"`
- **Update**: Set `validation_status="VALID"` or `"INVALID"`

### 2. ChromaDB S3 Bucket
- **Purpose**: Download "words" collection snapshot
- **Access**: Read-only
- **Path**: `chromadb_snapshots/words/{timestamp}/...`

### 3. Manifest S3 Bucket
- **Purpose**: Store manifest files for Step Function execution
- **Access**: Read/write
- **Path**: `pending_labels/{execution_id}/manifest.json`

## Deployment Steps

1. **Create infrastructure component** (`infra/validate_pending_labels/infrastructure.py`)
2. **Create Lambda handlers**:
   - `handlers/list_pending_labels.py` (zip-based)
   - `container/handler/handler.py` (container-based)
3. **Create Dockerfile** (`container/Dockerfile`)
4. **Create requirements.txt** (`container/requirements.txt`)
5. **Integrate into main infrastructure** (add to `infra/__main__.py`)
6. **Deploy**: `pulumi up`

## Testing

1. **Unit tests**: Test Lambda handlers locally
2. **Integration tests**: Test Step Function with small number of receipts
3. **Monitor**: CloudWatch Logs, Step Function execution history
4. **Validate**: Check DynamoDB for updated `validation_status` values

## Monitoring

- **CloudWatch Logs**: Lambda execution logs
- **Step Function**: Execution history, success/failure rates
- **CloudWatch Metrics**: Lambda duration, memory usage, errors
- **DynamoDB**: Track `validation_status` changes over time

## Cost Considerations

- **Lambda**: Pay per invocation (container Lambda: ~$0.0000166667 per GB-second)
- **Step Function**: Express Workflow: $0.000025 per state transition
- **S3**: Storage + requests (minimal for manifest files)
- **DynamoDB**: Read/write units (query + update operations)

## Next Steps

1. Create infrastructure component
2. Implement Lambda handlers
3. Create Dockerfile and requirements.txt
4. Integrate into main infrastructure
5. Deploy and test

