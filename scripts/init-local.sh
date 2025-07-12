#!/bin/sh
#
# Initialize local development environment with DynamoDB tables and LocalStack resources
#

set -e

echo "Waiting for services to be ready..."
sleep 5

# DynamoDB endpoint
DYNAMO_ENDPOINT="http://dynamodb-local:8000"
LOCALSTACK_ENDPOINT="http://localstack:4566"

echo "Creating DynamoDB table..."
aws dynamodb create-table \
    --table-name portfolio-metadata \
    --attribute-definitions \
        AttributeName=PK,AttributeType=S \
        AttributeName=SK,AttributeType=S \
        AttributeName=GSI1PK,AttributeType=S \
        AttributeName=GSI1SK,AttributeType=S \
        AttributeName=GSI2PK,AttributeType=S \
        AttributeName=GSI2SK,AttributeType=S \
        AttributeName=GSI3PK,AttributeType=S \
        AttributeName=GSI3SK,AttributeType=S \
        AttributeName=TYPE,AttributeType=S \
    --key-schema \
        AttributeName=PK,KeyType=HASH \
        AttributeName=SK,KeyType=RANGE \
    --global-secondary-indexes \
        '[
            {
                "IndexName": "GSI1",
                "Keys": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"},
                "BillingMode": "PAY_PER_REQUEST"
            },
            {
                "IndexName": "GSI2",
                "Keys": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"},
                "BillingMode": "PAY_PER_REQUEST"
            },
            {
                "IndexName": "GSI3",
                "Keys": [
                    {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI3SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"},
                "BillingMode": "PAY_PER_REQUEST"
            },
            {
                "IndexName": "GSITYPE",
                "Keys": [
                    {"AttributeName": "TYPE", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"}
                ],
                "Projection": {"ProjectionType": "ALL"},
                "BillingMode": "PAY_PER_REQUEST"
            }
        ]' \
    --billing-mode PAY_PER_REQUEST \
    --endpoint-url $DYNAMO_ENDPOINT \
    || echo "Table already exists"

echo "Creating S3 buckets in LocalStack..."
aws s3 mb s3://receipt-images --endpoint-url $LOCALSTACK_ENDPOINT || echo "Bucket already exists"
aws s3 mb s3://receipt-batch-files --endpoint-url $LOCALSTACK_ENDPOINT || echo "Bucket already exists"

echo "Creating SNS topics in LocalStack..."
aws sns create-topic --name receipt-processing-errors --endpoint-url $LOCALSTACK_ENDPOINT || echo "Topic already exists"
aws sns create-topic --name step-function-notifications --endpoint-url $LOCALSTACK_ENDPOINT || echo "Topic already exists"

echo "Local environment initialized successfully!"
echo ""
echo "Services available at:"
echo "  DynamoDB: http://localhost:8000"
echo "  DynamoDB Admin: http://localhost:8001"
echo "  LocalStack: http://localhost:4566"
echo ""
echo "To test DynamoDB connection:"
echo "  aws dynamodb list-tables --endpoint-url http://localhost:8000"
