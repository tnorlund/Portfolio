# AWS Support Ticket: Lambda Function Not Invoking from SQS Event Source Mapping

## Executive Summary

We have a Lambda function that is configured with an SQS event source mapping but is not being invoked when messages are sent to the queue. The Lambda is deployed in a VPC with EFS, and there appear to be networking or event source mapping configuration issues preventing message processing.

---

## Issue Description

**Problem**: The Lambda function `upload-images-process-ocr-image-dev` has an event source mapping configured to trigger on messages from the SQS queue `upload-images-dev-ocr-results-queue`. Messages are successfully sent to the queue (we can confirm via CloudWatch metrics), but the Lambda function is not being invoked.

**Error/Behavior**:
- Messages appear in the queue (ApproximateNumberOfMessagesNotVisible: 8)
- Event Source Mapping is in "Enabled" state
- No Lambda invocations in CloudWatch metrics for the past 24 hours
- Event Source Mapping LastProcessingResult is null
- No errors in Lambda function logs (function hasn't been invoked)

**Impact**: This is a production system that processes OCR results for receipt images. Without Lambda invocations, receipt processing is blocked.

---

## AWS Resources Information

### Lambda Function
- **Function Name**: `upload-images-process-ocr-image-dev`
- **Function ARN**: `arn:aws:lambda:us-east-1:681647709217:function:upload-images-process-ocr-image-dev`
- **IAM Role**: `arn:aws:iam::681647709217:role/upload-images-process-ocr-role-396d64d`
- **Last Modified**: 2025-10-26T22:36:59.000+0000
- **Timeout**: 600 seconds (10 minutes)
- **Memory**: 2048 MB
- **Runtime**: Container image (not standard runtime)

### Lambda VPC Configuration
- **VPC ID**: `vpc-0ff3605a5428b9727`
- **Subnets**:
  - `subnet-02f1818a1e813b126` (us-east-1f, CIDR: 10.0.101.0/24)
  - `subnet-0bb59954a84999494` (us-east-1f, CIDR: 10.0.102.0/24)
- **Security Groups**:
  - `sg-025b2a030a20e6037` (chroma-sg-lambda)
- **EFS Configuration**:
  - Access Point ARN: `arn:aws:elasticfilesystem:us-east-1:681647709217:access-point/fsap-06905e1f62ead9feb`
  - Mount Path: `/mnt/chroma`
  - File System ID: `fs-0b7608126227439b9`
  - Mount Target: `fsmt-09d0e5038e9675d23`
  - Mount Target State: available

### SQS Queue
- **Queue Name**: `upload-images-dev-ocr-results-queue`
- **Queue URL**: `https://sqs.us-east-1.amazonaws.com/681647709217/upload-images-dev-ocr-results-queue`
- **Queue ARN**: `arn:aws:sqs:us-east-1:681647709217:upload-images-dev-ocr-results-queue`
- **Visibility Timeout**: 900 seconds (15 minutes)
- **Message Retention**: 345600 seconds (4 days)
- **Current Status**:
  - ApproximateNumberOfMessages: 0
  - ApproximateNumberOfMessagesNotVisible: 8

### Event Source Mapping
- **UUID**: `c53219f6-47c9-4b44-8f5c-0efc0f180ba3`
- **Event Source ARN**: `arn:aws:sqs:us-east-1:681647709217:upload-images-dev-ocr-results-queue`
- **Function ARN**: `arn:aws:lambda:us-east-1:681647709217:function:upload-images-process-ocr-image-dev`
- **State**: Enabled
- **State Transition Reason**: USER_INITIATED
- **Last Modified**: 2025-10-26T16:14:15.585000-07:00
- **Batch Size**: 10
- **Maximum Batching Window**: 0 seconds
- **Last Processing Result**: null
- **Maximum Retry Attempts**: Not configured (default)

### VPC Endpoints
The Lambda is in private subnets and uses VPC endpoints for AWS service access:

**Interface Endpoints**:
1. **SQS Endpoint**: `vpce-0145e81bc1cd8163d`
   - Service: `com.amazonaws.us-east-1.sqs`
   - Type: Interface
   - State: available
   
2. **CloudWatch Logs Endpoint**: `vpce-055ab1051333bad28`
   - Service: `com.amazonaws.us-east-1.logs`
   - Type: Interface
   - State: available

**Gateway Endpoints**:
1. **S3 Endpoint**: `vpce-0e21e1abf3d74067e`
   - Service: `com.amazonaws.us-east-1.s3`
   - Type: Gateway
   - State: available

2. **DynamoDB Endpoint**: `vpce-0532ba1c83b5bd5e0`
   - Service: `com.amazonaws.us-east-1.dynamodb`
   - Type: Gateway
   - State: available

### DynamoDB Table
- **Table Name**: `ReceiptsTable-dc5be22`
- **Table ARN**: `arn:aws:dynamodb:us-east-1:681647709217:table/ReceiptsTable-dc5be22`
- **Billing Mode**: PAY_PER_REQUEST

### EFS Mount Targets
- **Mount Target ID**: `fsmt-09d0e5038e9675d23`
- **Subnet**: `subnet-02f1818a1e813b126` (same subnet as Lambda primary subnet)
- **Availability Zone**: us-east-1f
- **State**: available

---

## Architecture Context

This Lambda is part of a receipt processing pipeline:

1. Images are uploaded to S3
2. Messages are sent to `upload-images-dev-ocr-queue` (OCR job queue)
3. External OCR workers (mac_ocr) process messages and upload results to S3
4. Workers send messages to `upload-images-dev-ocr-results-queue` (OCR results queue)
5. **This Lambda** (`upload-images-process-ocr-image-dev`) should process those messages
6. Lambda updates DynamoDB with receipt data

### Key Details
- Lambda is deployed in **private subnets**
- Lambda has **EFS access** configured (though currently using S3 mode due to performance)
- Lambda needs internet access via NAT instance (though we don't see NAT instance in running state)
- Both subnets are in the **same availability zone** (us-east-1f)

---

## Troubleshooting Steps Already Taken

1. ✅ Verified event source mapping exists and is enabled
2. ✅ Verified SQS queue has messages (`ApproximateNumberOfMessagesNotVisible: 8`)
3. ✅ Checked Lambda function configuration
4. ✅ Verified VPC endpoints are available
5. ✅ Confirmed EFS mount target is in the same subnet
6. ❌ Cannot confirm NAT instance is running (describe-instances returned no results)
7. ❌ Event Source Mapping LastProcessingResult is null (no error message available)

---

## Diagnostic Request

We need help understanding:

1. **Why is the Lambda not being invoked?**
   - Is this a networking issue with the VPC configuration?
   - Is this an event source mapping issue?
   - Are there IAM permissions that might be blocking?

2. **What should we check?**
   - Should we disable and re-enable the event source mapping?
   - Do we need to configure CloudWatch Logs VPC endpoint for Lambda invocations?
   - Is there an issue with Lambda being in private subnets?

3. **Is the NAT instance configuration preventing invocations?**
   - Our NAT instance appears to not be running or not accessible
   - Could this prevent event source mapping from working?

4. **What logs or metrics should we examine?**
   - CloudWatch metrics for the event source mapping
   - SQS dead letter queue metrics
   - Lambda networking logs

---

## Additional Information

### Lambda Environment Variables
```
DYNAMO_TABLE_NAME=ReceiptsTable-dc5be22
S3_BUCKET=upload-images-image-bucket-4bcea7e
RAW_BUCKET=raw-image-bucket-c779c32
SITE_BUCKET=sitebucket-ad92f1f
ARTIFACTS_BUCKET=upload-images-artifacts-bucket-afc4b65
OCR_JOB_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/681647709217/upload-images-dev-ocr-queue
OCR_RESULTS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/681647709217/upload-images-dev-ocr-results-queue
CHROMADB_BUCKET=chromadb-dev-shared-buckets-vectors-c239843
CHROMADB_STORAGE_MODE=s3
CHROMA_ROOT=/mnt/chroma
CHROMA_HTTP_ENDPOINT=chroma-dev.chroma-dev.svc.local:8000
GOOGLE_PLACES_API_KEY=(configured)
OPENAI_API_KEY=(configured)
```

### Key Insights
- 8 messages are "not visible" in the queue (likely stuck in visibility timeout)
- Event source mapping is enabled but shows no processing activity
- Lambda hasn't been invoked in the past 24 hours
- Lambda is in private subnets with VPC endpoints configured

---

## Account Information
- **AWS Account ID**: 681647709217
- **Region**: us-east-1
- **Stack**: dev

---

## Requested Action
Please investigate why the Lambda function is not being invoked by the SQS event source mapping despite:
1. Event source mapping being enabled
2. Messages existing in the queue
3. Lambda function being configured correctly
4. VPC endpoints being in available state

Thank you for your assistance.

