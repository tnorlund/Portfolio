# ChromaDB Compaction Infrastructure Implementation Plan

## Overview

This document outlines the step-by-step implementation plan for the ChromaDB compaction infrastructure using Pulumi.

## Directory Structure

```
infra/chromadb_compaction/
├── README.md                    # Overview and operations guide
├── TECHNICAL_DESIGN.md         # Detailed technical design
├── IMPLEMENTATION_PLAN.md      # This file
├── __init__.py                 # Package initialization
├── s3_buckets.py              # S3 bucket resources
├── sqs_queue.py               # SQS queue for delta notifications
├── producer_lambda.py          # Lambda for producing embeddings
├── compactor_lambda.py         # Lambda for compacting deltas
├── query_lambda.py             # Lambda for querying vectors
├── eventbridge_rules.py        # Scheduled triggers
├── iam_roles.py               # IAM roles and policies
├── monitoring.py               # CloudWatch alarms and dashboards
└── handlers/
    ├── __init__.py
    ├── producer_handler.py     # Producer Lambda code
    ├── compactor_handler.py    # Compactor Lambda code
    └── query_handler.py        # Query Lambda code
```

## Implementation Steps

### Step 1: S3 Buckets

**File**: `s3_buckets.py`

```python
import pulumi
import pulumi_aws as aws
from pulumi import Output

# Get current account and region
account_id = aws.get_caller_identity().account_id
region = aws.get_region().name
stack = pulumi.get_stack()

# Main bucket for vectors
chromadb_bucket = aws.s3.Bucket(
    "chromadb-vectors",
    bucket=f"chromadb-vectors-{stack}-{account_id}",
    versioning=aws.s3.BucketVersioningArgs(
        enabled=True,
    ),
    server_side_encryption_configuration=aws.s3.BucketServerSideEncryptionConfigurationArgs(
        rule=aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
            apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                sse_algorithm="AES256",
            ),
        ),
    ),
    lifecycle_rules=[
        # Clean up old deltas
        aws.s3.BucketLifecycleRuleArgs(
            id="delete-old-deltas",
            enabled=True,
            prefix="delta/",
            expiration=aws.s3.BucketLifecycleRuleExpirationArgs(
                days=7,
            ),
        ),
        # Archive old snapshots
        aws.s3.BucketLifecycleRuleArgs(
            id="archive-old-snapshots",
            enabled=True,
            prefix="snapshot/",
            transitions=[
                aws.s3.BucketLifecycleRuleTransitionArgs(
                    days=30,
                    storage_class="STANDARD_IA",
                ),
            ],
        ),
    ],
    tags={
        "Project": "ChromaDB",
        "Environment": stack,
    },
)

# Export bucket name
pulumi.export("chromadb_bucket_name", chromadb_bucket.id)
pulumi.export("chromadb_bucket_arn", chromadb_bucket.arn)
```

### Step 2: SQS Queue

**File**: `sqs_queue.py`

```python
import pulumi
import pulumi_aws as aws

# Dead letter queue for failed messages
dlq = aws.sqs.Queue(
    "chromadb-delta-dlq",
    message_retention_seconds=1209600,  # 14 days
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

# Main queue for delta notifications
delta_queue = aws.sqs.Queue(
    "chromadb-delta-queue",
    visibility_timeout_seconds=900,  # 15 minutes (match compaction timeout)
    message_retention_seconds=345600,  # 4 days
    redrive_policy=Output.all(dlq.arn).apply(
        lambda args: json.dumps({
            "deadLetterTargetArn": args[0],
            "maxReceiveCount": 3,
        })
    ),
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

# Export queue URLs
pulumi.export("delta_queue_url", delta_queue.url)
pulumi.export("delta_queue_arn", delta_queue.arn)
pulumi.export("dlq_url", dlq.url)
```

### Step 3: IAM Roles

**File**: `iam_roles.py`

```python
import pulumi
import pulumi_aws as aws
import json

# Producer Lambda role
producer_assume_role_policy = aws.iam.get_policy_document(
    statements=[{
        "effect": "Allow",
        "principals": [{
            "type": "Service",
            "identifiers": ["lambda.amazonaws.com"],
        }],
        "actions": ["sts:AssumeRole"],
    }]
)

producer_role = aws.iam.Role(
    "chromadb-producer-role",
    assume_role_policy=producer_assume_role_policy.json,
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

# Producer Lambda policy
producer_policy = aws.iam.Policy(
    "chromadb-producer-policy",
    policy=Output.all(
        chromadb_bucket.arn,
        delta_queue.arn,
    ).apply(lambda args: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "arn:aws:logs:*:*:*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:PutObject",
                    "s3:PutObjectAcl",
                ],
                "Resource": f"{args[0]}/delta/*",
            },
            {
                "Effect": "Allow",
                "Action": "sqs:SendMessage",
                "Resource": args[1],
            },
        ],
    })),
)

# Attach policy to role
aws.iam.RolePolicyAttachment(
    "chromadb-producer-attachment",
    role=producer_role.name,
    policy_arn=producer_policy.arn,
)

# Compactor Lambda role (with DynamoDB access)
compactor_role = aws.iam.Role(
    "chromadb-compactor-role",
    assume_role_policy=producer_assume_role_policy.json,
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

# Import DynamoDB table name from main infra
dynamo_table_name = f"portfolio-{pulumi.get_stack()}"

compactor_policy = aws.iam.Policy(
    "chromadb-compactor-policy",
    policy=Output.all(
        chromadb_bucket.arn,
        delta_queue.arn,
        account_id,
    ).apply(lambda args: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "arn:aws:logs:*:*:*",
            },
            {
                "Effect": "Allow",
                "Action": "s3:*",
                "Resource": [
                    args[0],
                    f"{args[0]}/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "sqs:ReceiveMessage",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes",
                ],
                "Resource": args[1],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:GetItem",
                ],
                "Resource": f"arn:aws:dynamodb:*:{args[2]}:table/{dynamo_table_name}",
                "Condition": {
                    "ForAllValues:StringEquals": {
                        "dynamodb:LeadingKeys": ["LOCK#chroma-main-snapshot"],
                    },
                },
            },
        ],
    })),
)

aws.iam.RolePolicyAttachment(
    "chromadb-compactor-attachment",
    role=compactor_role.name,
    policy_arn=compactor_policy.arn,
)

# Query Lambda role (read-only)
query_role = aws.iam.Role(
    "chromadb-query-role",
    assume_role_policy=producer_assume_role_policy.json,
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

query_policy = aws.iam.Policy(
    "chromadb-query-policy",
    policy=Output.all(chromadb_bucket.arn).apply(lambda args: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "arn:aws:logs:*:*:*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:ListBucket",
                ],
                "Resource": [
                    args[0],
                    f"{args[0]}/*",
                ],
            },
        ],
    })),
)

aws.iam.RolePolicyAttachment(
    "chromadb-query-attachment",
    role=query_role.name,
    policy_arn=query_policy.arn,
)

# Export role ARNs
pulumi.export("producer_role_arn", producer_role.arn)
pulumi.export("compactor_role_arn", compactor_role.arn)
pulumi.export("query_role_arn", query_role.arn)
```

### Step 4: Lambda Functions

**File**: `producer_lambda.py`

```python
import pulumi
import pulumi_aws as aws
from pulumi import Output

# Producer Lambda
producer_lambda = aws.lambda_.Function(
    "chromadb-producer",
    runtime="python3.12",
    handler="producer_handler.handler",
    role=producer_role.arn,
    code=pulumi.AssetArchive({
        ".": pulumi.FileArchive("./handlers"),
    }),
    timeout=300,  # 5 minutes
    memory_size=1024,  # 1GB
    environment=aws.lambda_.FunctionEnvironmentArgs(
        variables={
            "CHROMADB_BUCKET": chromadb_bucket.id,
            "DELTA_QUEUE_URL": delta_queue.url,
            "OPENAI_API_KEY": pulumi.Config("openai").require("api_key"),
        },
    ),
    layers=[
        # Reference existing layers from main infra
        f"arn:aws:lambda:{region}:{account_id}:layer:receipt-dynamo-layer-{stack}:latest",
        f"arn:aws:lambda:{region}:{account_id}:layer:receipt-label-layer-{stack}:latest",
    ],
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

# Export Lambda ARN
pulumi.export("producer_lambda_arn", producer_lambda.arn)
pulumi.export("producer_lambda_name", producer_lambda.name)
```

**File**: `compactor_lambda.py`

```python
# Compactor Lambda with Docker image for ChromaDB
compactor_image = aws.ecr.Repository(
    "chromadb-compactor-repo",
    name=f"chromadb-compactor-{pulumi.get_stack()}",
    image_tag_mutability="MUTABLE",
    image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
        scan_on_push=True,
    ),
)

# Build and push Docker image
compactor_docker_image = pulumi_docker.Image(
    "chromadb-compactor-image",
    build=pulumi_docker.DockerBuildArgs(
        context="./docker/compactor",
        platform="linux/amd64",
    ),
    image_name=Output.concat(
        compactor_image.repository_url, ":latest"
    ),
    registry=pulumi_docker.RegistryArgs(
        server=compactor_image.repository_url,
        username="AWS",
        password=pulumi.Output.secret(
            aws.ecr.get_authorization_token().password
        ),
    ),
)

compactor_lambda = aws.lambda_.Function(
    "chromadb-compactor",
    package_type="Image",
    image_uri=pulumi.Output.concat(
        compactor_image.repository_url, ":latest"
    ),
    role=compactor_role.arn,
    timeout=900,  # 15 minutes
    memory_size=3008,  # 3GB (max for Lambda)
    ephemeral_storage=aws.lambda_.FunctionEphemeralStorageArgs(
        size=10240,  # 10GB
    ),
    environment=aws.lambda_.FunctionEnvironmentArgs(
        variables={
            "CHROMADB_BUCKET": chromadb_bucket.id,
            "DELTA_QUEUE_URL": delta_queue.url,
            "DYNAMODB_TABLE": dynamo_table_name,
            "LOCK_ID": "chroma-main-snapshot",
            "LOCK_TIMEOUT_MINUTES": "15",
        },
    ),
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

# SQS trigger for compactor
sqs_event_source = aws.lambda_.EventSourceMapping(
    "chromadb-compactor-sqs-trigger",
    event_source_arn=delta_queue.arn,
    function_name=compactor_lambda.name,
    batch_size=10,
    maximum_batching_window_in_seconds=5,
)
```

### Step 5: EventBridge Rules

**File**: `eventbridge_rules.py`

```python
# Scheduled compaction rule
compaction_schedule = aws.cloudwatch.EventRule(
    "chromadb-compaction-schedule",
    description="Trigger ChromaDB compaction every 15 minutes",
    schedule_expression="rate(15 minutes)",
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

# Lambda permission for EventBridge
compaction_permission = aws.lambda_.Permission(
    "chromadb-compaction-permission",
    action="lambda:InvokeFunction",
    function=compactor_lambda.name,
    principal="events.amazonaws.com",
    source_arn=compaction_schedule.arn,
)

# EventBridge target
compaction_target = aws.cloudwatch.EventTarget(
    "chromadb-compaction-target",
    rule=compaction_schedule.name,
    arn=compactor_lambda.arn,
    input=json.dumps({
        "source": "scheduled",
        "action": "compact",
    }),
)
```

### Step 6: Monitoring

**File**: `monitoring.py`

```python
# Lock contention alarm
lock_contention_alarm = aws.cloudwatch.MetricAlarm(
    "chromadb-lock-contention",
    alarm_description="ChromaDB compactor lock contention is high",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=2,
    metric_name="LockAcquisitionFailed",
    namespace="ChromaDB/Compaction",
    period=300,
    statistic="Sum",
    threshold=5,
    treat_missing_data="notBreaching",
    tags={
        "Project": "ChromaDB",
        "Environment": pulumi.get_stack(),
    },
)

# Delta accumulation alarm
delta_accumulation_alarm = aws.cloudwatch.MetricAlarm(
    "chromadb-delta-accumulation",
    alarm_description="Too many ChromaDB deltas pending compaction",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=1,
    dimensions={
        "QueueName": delta_queue.name,
    },
    metric_name="ApproximateNumberOfMessagesVisible",
    namespace="AWS/SQS",
    period=300,
    statistic="Maximum",
    threshold=1000,
    treat_missing_data="notBreaching",
)

# Compaction duration alarm
compaction_duration_alarm = aws.cloudwatch.MetricAlarm(
    "chromadb-compaction-duration",
    alarm_description="ChromaDB compaction is taking too long",
    comparison_operator="GreaterThanThreshold",
    evaluation_periods=1,
    metric_name="Duration",
    namespace="AWS/Lambda",
    dimensions={
        "FunctionName": compactor_lambda.name,
    },
    period=300,
    statistic="Maximum",
    threshold=600000,  # 10 minutes in milliseconds
    treat_missing_data="notBreaching",
)

# Dashboard
dashboard = aws.cloudwatch.Dashboard(
    "chromadb-dashboard",
    dashboard_name=f"ChromaDB-{pulumi.get_stack()}",
    dashboard_body=Output.all(
        compactor_lambda.name,
        producer_lambda.name,
        query_lambda.name,
        delta_queue.name,
    ).apply(lambda args: json.dumps({
        "widgets": [
            {
                "type": "metric",
                "properties": {
                    "metrics": [
                        ["ChromaDB/Compaction", "CompactionDuration"],
                        [".", "DeltasProcessed"],
                        [".", "LockAcquisitionFailed"],
                    ],
                    "period": 300,
                    "stat": "Average",
                    "region": region,
                    "title": "Compaction Metrics",
                },
            },
            {
                "type": "metric",
                "properties": {
                    "metrics": [
                        ["AWS/SQS", "ApproximateNumberOfMessagesVisible", {"QueueName": args[3]}],
                        [".", "ApproximateAgeOfOldestMessage", {"QueueName": args[3]}],
                    ],
                    "period": 300,
                    "stat": "Average",
                    "region": region,
                    "title": "Delta Queue Metrics",
                },
            },
        ],
    })),
)
```

## Next Steps

1. **Create Docker images** for Lambda functions that need ChromaDB
2. **Write handler code** for each Lambda function
3. **Create integration tests** for the complete pipeline
4. **Document operational procedures** for monitoring and troubleshooting
5. **Plan migration strategy** from existing Pinecone setup

## Testing Strategy

### Unit Tests
- Test each Lambda handler in isolation
- Mock S3, SQS, and DynamoDB interactions
- Validate ChromaDB operations

### Integration Tests
- Deploy to dev environment
- Test complete flow: produce → queue → compact → query
- Validate lock behavior under contention
- Test failure scenarios

### Load Tests
- Simulate high producer throughput
- Measure compaction performance
- Monitor resource utilization

## Deployment Commands

```bash
# Deploy all infrastructure
cd infra/chromadb_compaction
pulumi up

# Deploy specific component
pulumi up -t aws:lambda/function:chromadb-compactor

# View outputs
pulumi stack output

# Destroy infrastructure
pulumi destroy
```