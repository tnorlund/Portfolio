#!/bin/bash

# Bootstrap LocalStack with test data for OCR integration tests

set -e

echo "🚀 Bootstrapping LocalStack for OCR integration tests..."

# Wait for LocalStack to be ready
echo "⏳ Waiting for LocalStack to be ready..."
for i in {1..30}; do
    if curl -f http://localhost:4566/_localstack/health >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
echo "✅ LocalStack is ready!"

# Set up AWS CLI to use LocalStack
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_DEFAULT_REGION=us-west-2
export AWS_ENDPOINT_URL=http://localhost:4566

# Create S3 bucket
echo "📦 Creating S3 bucket..."
aws s3 mb s3://receipt-ocr-dev --endpoint-url=http://localhost:4566

# Upload test image to S3
echo "📸 Uploading test image to S3..."
# Create a small test image (1x1 pixel PNG)
echo -e "\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\nIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xdd\x8d\xb4\x1c\x00\x00\x00\x00IEND\xaeB`\x82" > /tmp/test-image.png
aws s3 cp /tmp/test-image.png s3://receipt-ocr-dev/receipts/test-image-1.png --endpoint-url=http://localhost:4566
rm /tmp/test-image.png

# Create DynamoDB table
echo "🗄️ Creating DynamoDB table..."
aws dynamodb create-table \
    --table-name receipt-ocr-dev \
    --attribute-definitions \
        AttributeName=PK,AttributeType=S \
        AttributeName=SK,AttributeType=S \
        AttributeName=GSI1PK,AttributeType=S \
        AttributeName=GSI1SK,AttributeType=S \
    --key-schema \
        AttributeName=PK,KeyType=HASH \
        AttributeName=SK,KeyType=RANGE \
    --global-secondary-indexes \
        IndexName=GSI1,KeySchema='[{AttributeName=GSI1PK,KeyType=HASH},{AttributeName=GSI1SK,KeyType=RANGE}]',Projection='{ProjectionType=ALL}',ProvisionedThroughput='{ReadCapacityUnits=5,WriteCapacityUnits=5}' \
    --provisioned-throughput ReadCapacityUnits=5,WriteCapacityUnits=5 \
    --endpoint-url=http://localhost:4566

# Wait for table to be active
echo "⏳ Waiting for DynamoDB table to be active..."
aws dynamodb wait table-exists --table-name receipt-ocr-dev --endpoint-url=http://localhost:4566

# Insert OCR job record
echo "📝 Inserting OCR job record..."
aws dynamodb put-item \
    --table-name receipt-ocr-dev \
    --item '{
        "PK": {"S": "IMAGE#test-image-1"},
        "SK": {"S": "OCR_JOB#job-1"},
        "TYPE": {"S": "OCR_JOB"},
        "s3_bucket": {"S": "receipt-ocr-dev"},
        "s3_key": {"S": "receipts/test-image-1.png"},
        "created_at": {"S": "2025-01-01T00:00:00.000000+00:00"},
        "updated_at": {"S": "2025-01-01T00:00:00.000000+00:00"},
        "status": {"S": "PENDING"}
    }' \
    --endpoint-url=http://localhost:4566

# Create SQS queues
echo "📬 Creating SQS queues..."
OCR_JOB_QUEUE_URL=$(aws sqs create-queue --queue-name ocr-job-queue --endpoint-url=http://localhost:4566 --query 'QueueUrl' --output text)
OCR_RESULTS_QUEUE_URL=$(aws sqs create-queue --queue-name ocr-results-queue --endpoint-url=http://localhost:4566 --query 'QueueUrl' --output text)

# Export environment variables for tests
echo "🔧 Setting up environment variables..."
export E2E_LOCALSTACK=1
export AWS_REGION=us-west-2
export LOCALSTACK_ENDPOINT=http://localhost:4566
export OCR_JOB_QUEUE_URL="$OCR_JOB_QUEUE_URL"
export OCR_RESULTS_QUEUE_URL="$OCR_RESULTS_QUEUE_URL"
export DYNAMO_TABLE_NAME=receipt-ocr-dev

echo "✅ LocalStack bootstrap complete!"
echo "📋 Environment variables set:"
echo "   E2E_LOCALSTACK=$E2E_LOCALSTACK"
echo "   AWS_REGION=$AWS_REGION"
echo "   LOCALSTACK_ENDPOINT=$LOCALSTACK_ENDPOINT"
echo "   OCR_JOB_QUEUE_URL=$OCR_JOB_QUEUE_URL"
echo "   OCR_RESULTS_QUEUE_URL=$OCR_RESULTS_QUEUE_URL"
echo "   DYNAMO_TABLE_NAME=$DYNAMO_TABLE_NAME"
echo ""
echo "🧪 Run tests with:"
echo "   swift test --filter IntegrationTests"
