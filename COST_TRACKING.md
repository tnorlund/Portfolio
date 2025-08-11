# AI Cost Tracking Setup

## Overview

The AI usage tracker automatically saves costs for OpenAI API calls to DynamoDB. Each Pulumi stack has its own table with costs isolated by environment.

## Environment Variables

To enable cost tracking in your Lambda functions, set these environment variables:

```bash
# Required
DYNAMODB_TABLE_NAME=<your-table-name>  # e.g., ReceiptsTable-dc5be22
OPENAI_API_KEY=<your-openai-key>

# Optional (defaults shown)
TRACK_AI_USAGE=true                    # Enable/disable tracking
USE_RESILIENT_TRACKER=true             # Use resilient version with retries
TRACK_TO_FILE=false                    # Also log to file (for debugging)
```

Note: Table validation has been removed since Pulumi already handles environment isolation through stack naming.

## Lambda Configuration

In your Pulumi infrastructure code, add these environment variables to Lambda functions:

```python
lambda_function = aws.lambda_.Function(
    "my-function",
    environment={
        "variables": {
            "DYNAMODB_TABLE_NAME": receipts_table.name,
            "OPENAI_API_KEY": openai_api_key,
            "TRACK_AI_USAGE": "true",
        }
    },
    # ... other config
)
```

## How It Works

1. **Automatic Tracking**: The `ClientManager` automatically wraps OpenAI clients with usage tracking
2. **Per-Stack Isolation**: Each Pulumi stack gets its own table (e.g., `ReceiptsTable-dev-abc123`, `ReceiptsTable-prod-xyz789`)
3. **Cost Calculation**: Tracks tokens, model, operation type, and calculates costs based on current OpenAI pricing
4. **Resilient Storage**: Uses circuit breakers and retries to ensure metrics are saved even during transient failures

## What Gets Tracked

For each API call, the following is recorded:

- **Service**: openai, anthropic, google_places, etc.
- **Model**: gpt-3.5-turbo, text-embedding-3-small, etc.
- **Operation**: completion, embedding, code_review
- **Tokens**: input_tokens, output_tokens, total_tokens
- **Cost**: Calculated USD cost
- **Metadata**: timestamp, user_id, job_id, batch_id, latency_ms
- **Context**: Environment tags, function name, error info

## Querying Costs

### By Environment

Since each stack has its own table, you can query costs per environment:

```python
import boto3

dynamodb = boto3.client('dynamodb')

# Dev environment costs
dev_costs = dynamodb.query(
    TableName='ReceiptsTable-dev-abc123',
    IndexName='GSI2',  # Or appropriate index
    KeyConditionExpression='GSI2PK = :pk',
    ExpressionAttributeValues={':pk': {'S': 'AI_USAGE'}}
)

# Prod environment costs  
prod_costs = dynamodb.query(
    TableName='ReceiptsTable-prod-xyz789',
    # ... same query
)
```

### Cost Analysis

You can aggregate costs by:
- Time period (daily, weekly, monthly)
- Model (GPT-3.5 vs GPT-4)
- Operation (embeddings vs completions)
- User or job

## Example Usage

```python
from receipt_label.utils import get_client_manager

# Client manager handles everything automatically
client_manager = get_client_manager()

# This call is automatically tracked
response = client_manager.openai.embeddings.create(
    input=["Hello world"],
    model="text-embedding-3-small"
)
# Cost is saved to DynamoDB with all metadata

# Set context for batch operations
client_manager.set_tracking_context(
    job_id="batch-123",
    batch_id="embedding-batch-456"
)
```

## Cost Management Benefits

1. **Environment Isolation**: Dev experiments don't affect prod cost tracking
2. **Granular Tracking**: See exactly which operations cost the most
3. **Optimization Opportunities**: Identify expensive patterns
4. **Budget Monitoring**: Set up CloudWatch alarms on cost thresholds
5. **Multi-Stack Support**: Each developer can have their own stack with isolated costs

## Troubleshooting

If costs aren't being tracked:

1. Check environment variables are set correctly
2. Ensure `SKIP_TABLE_VALIDATION=true` for Pulumi tables
3. Verify DynamoDB table exists and Lambda has write permissions
4. Check CloudWatch logs for any errors

## Testing

Use the provided test script to verify setup:

```bash
python test_cost_tracking.py
```

This will create a test metric and verify it's saved to DynamoDB.