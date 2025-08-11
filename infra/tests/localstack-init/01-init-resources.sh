#!/bin/bash

# LocalStack initialization script
# This runs automatically when LocalStack starts

echo "Initializing LocalStack resources for testing..."

# Set AWS CLI to use LocalStack
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-east-1
export AWS_ENDPOINT_URL=http://localhost:4566

# Create DynamoDB table
echo "Creating DynamoDB table..."
aws dynamodb create-table \
    --table-name test-receipts-table \
    --attribute-definitions \
        AttributeName=PK,AttributeType=S \
        AttributeName=SK,AttributeType=S \
        AttributeName=embedding_status,AttributeType=S \
        AttributeName=batch_id,AttributeType=S \
    --key-schema \
        AttributeName=PK,KeyType=HASH \
        AttributeName=SK,KeyType=RANGE \
    --global-secondary-indexes \
        "IndexName=embedding-status-index,Keys=[{AttributeName=embedding_status,KeyType=HASH}],Projection={ProjectionType=ALL},ProvisionedThroughput={ReadCapacityUnits=5,WriteCapacityUnits=5}" \
        "IndexName=batch-index,Keys=[{AttributeName=batch_id,KeyType=HASH}],Projection={ProjectionType=ALL},ProvisionedThroughput={ReadCapacityUnits=5,WriteCapacityUnits=5}" \
    --billing-mode PAY_PER_REQUEST \
    --endpoint-url http://localhost:4566 2>/dev/null || echo "Table already exists"

# Create S3 buckets
echo "Creating S3 buckets..."
aws s3 mb s3://test-batch-bucket --endpoint-url http://localhost:4566 2>/dev/null || echo "Batch bucket already exists"
aws s3 mb s3://test-chromadb-bucket --endpoint-url http://localhost:4566 2>/dev/null || echo "ChromaDB bucket already exists"

# Create SQS queue for compaction
echo "Creating SQS queue..."
aws sqs create-queue \
    --queue-name test-compaction-queue \
    --attributes "VisibilityTimeout=900,MessageRetentionPeriod=345600" \
    --endpoint-url http://localhost:4566 2>/dev/null || echo "Queue already exists"

# Create IAM role for Lambda
echo "Creating IAM roles..."
aws iam create-role \
    --role-name test-lambda-role \
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }' \
    --endpoint-url http://localhost:4566 2>/dev/null || echo "Lambda role already exists"

# Attach policies to the role
aws iam attach-role-policy \
    --role-name test-lambda-role \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole \
    --endpoint-url http://localhost:4566 2>/dev/null || echo "Policy already attached"

# Create IAM role for Step Functions
aws iam create-role \
    --role-name test-stepfunctions-role \
    --assume-role-policy-document '{
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "states.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }' \
    --endpoint-url http://localhost:4566 2>/dev/null || echo "Step Functions role already exists"

# Create ECR repository
echo "Creating ECR repository..."
aws ecr create-repository \
    --repository-name test-unified-embedding \
    --image-scanning-configuration scanOnPush=true \
    --endpoint-url http://localhost:4566 2>/dev/null || echo "ECR repository already exists"

# Create sample Lambda functions for testing
echo "Creating sample Lambda functions..."

# Create a simple Lambda function for testing
cat > /tmp/lambda_function.py << 'EOF'
def lambda_handler(event, context):
    import json
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Test Lambda response', 'event': event})
    }
EOF

cd /tmp && zip lambda_function.zip lambda_function.py

# Create test Lambda functions
for func in list-pending find-unembedded submit-openai line-polling word-polling compaction; do
    aws lambda create-function \
        --function-name "$func-test" \
        --runtime python3.12 \
        --role arn:aws:iam::000000000000:role/test-lambda-role \
        --handler lambda_function.lambda_handler \
        --zip-file fileb:///tmp/lambda_function.zip \
        --timeout 30 \
        --memory-size 128 \
        --endpoint-url http://localhost:4566 2>/dev/null || echo "Function $func-test already exists"
done

# Clean up
rm -f /tmp/lambda_function.py /tmp/lambda_function.zip

echo "LocalStack initialization complete!"