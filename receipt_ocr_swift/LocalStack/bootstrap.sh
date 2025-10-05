#!/usr/bin/env bash
set -euo pipefail

ENDPOINT_URL=${LOCALSTACK_ENDPOINT:-http://localhost:4566}
AWS=${AWS_CLI:-aws}
REGION=${AWS_REGION:-us-west-2}

echo "Bootstrapping LocalStack resources at $ENDPOINT_URL in $REGION"

BUCKET=${S3_BUCKET:-receipt-ocr-dev}
TABLE=${DYNAMO_TABLE_NAME:-receipt-dev}
JOB_Q=${OCR_JOB_QUEUE_NAME:-ocr-job-queue}
RESULTS_Q=${OCR_RESULTS_QUEUE_NAME:-ocr-results-queue}

set -x
$AWS --endpoint-url "$ENDPOINT_URL" --region "$REGION" s3api create-bucket \
  --bucket "$BUCKET" \
  --create-bucket-configuration LocationConstraint="$REGION" >/dev/null 2>&1 || true

# wait until bucket exists
for i in {1..10}; do
  if $AWS --endpoint-url "$ENDPOINT_URL" --region "$REGION" s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

$AWS --endpoint-url "$ENDPOINT_URL" --region "$REGION" dynamodb create-table \
  --table-name "$TABLE" \
  --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S AttributeName=GSI1PK,AttributeType=S AttributeName=GSI1SK,AttributeType=S \
  --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
  --global-secondary-indexes 'IndexName=GSI1,KeySchema=[{AttributeName=GSI1PK,KeyType=HASH},{AttributeName=GSI1SK,KeyType=RANGE}],Projection={ProjectionType=ALL},ProvisionedThroughput={ReadCapacityUnits=5,WriteCapacityUnits=5}' \
  --billing-mode PAY_PER_REQUEST >/dev/null 2>&1 || true

JOB_URL=$($AWS --endpoint-url "$ENDPOINT_URL" --region "$REGION" sqs create-queue --queue-name "$JOB_Q" --query QueueUrl --output text)
RESULTS_URL=$($AWS --endpoint-url "$ENDPOINT_URL" --region "$REGION" sqs create-queue --queue-name "$RESULTS_Q" --query QueueUrl --output text)

export OCR_JOB_QUEUE_URL="$JOB_URL"
export OCR_RESULTS_QUEUE_URL="$RESULTS_URL"
export DYNAMO_TABLE_NAME="$TABLE"
export S3_BUCKET="$BUCKET"
export LOCALSTACK_ENDPOINT="$ENDPOINT_URL"
export AWS_REGION="$REGION"

# Upload a sample image and seed an OCR_JOB row
IMG_KEY=receipts/sample.png
touch /tmp/sample.png
$AWS --endpoint-url "$ENDPOINT_URL" --region "$REGION" s3 cp /tmp/sample.png s3://$BUCKET/$IMG_KEY

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
IMAGE_ID=test-image-1
JOB_ID=job-1

$AWS --endpoint-url "$ENDPOINT_URL" --region "$REGION" dynamodb put-item --table-name "$TABLE" --item "{
  \"PK\": {\"S\": \"IMAGE#$IMAGE_ID\"},
  \"SK\": {\"S\": \"OCR_JOB#$JOB_ID\"},
  \"TYPE\": {\"S\": \"OCR_JOB\"},
  \"s3_bucket\": {\"S\": \"$BUCKET\"},
  \"s3_key\": {\"S\": \"$IMG_KEY\"},
  \"created_at\": {\"S\": \"$NOW\"},
  \"updated_at\": {\"S\": \"$NOW\"},
  \"status\": {\"S\": \"PENDING\"}
}"

echo "Export these to run e2e:"
echo "export OCR_JOB_QUEUE_URL=$JOB_URL"
echo "export OCR_RESULTS_QUEUE_URL=$RESULTS_URL"
echo "export DYNAMO_TABLE_NAME=$TABLE"
echo "export LOCALSTACK_ENDPOINT=$ENDPOINT_URL"
echo "export AWS_REGION=$REGION"
echo "export E2E_LOCALSTACK=1"


