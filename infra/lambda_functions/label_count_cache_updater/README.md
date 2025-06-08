# Label Count Cache Updater

This Lambda function runs on a schedule to update the `LabelCountCache` for all `CORE_LABELS` every 5 minutes.

## Overview

The function serves as a background process that:

1. **Fetches validation counts** for each core label by querying `ReceiptWordLabel` records
2. **Stores results in cache** using the `LabelCountCache` entity
3. **Sets TTL to 6 minutes** so cache automatically expires 1 minute after the next scheduled run
4. **Runs every 5 minutes** via CloudWatch Events

## Architecture

- **Runtime**: Python 3.12
- **Memory**: 2048 MB
- **Timeout**: 5 minutes
- **Trigger**: CloudWatch Events (rate: 5 minutes)
- **Layers**: dynamo_layer (for receipt_dynamo package)

## Cache Strategy

The function implements a TTL-based cache strategy:

- Cache entries are set with a 6-minute TTL
- Function runs every 5 minutes
- This ensures cache is always fresh with a 1-minute overlap for safety
- DynamoDB automatically removes expired entries

## Performance Optimizations

1. **Concurrent processing**: Uses ThreadPoolExecutor with 5 workers to fetch counts for multiple labels simultaneously
2. **Efficient pagination**: Handles large result sets by paginating through `getReceiptWordLabelsByLabel` calls
3. **Graceful error handling**: Continues processing other labels even if one fails

## Error Handling

- Individual label fetch failures are logged but don't stop the overall process
- Cache update failures are logged with specific error details
- Function returns summary of successful vs failed updates

## Dependencies

- `receipt_dynamo`: For DynamoDB operations and entity classes
- `concurrent.futures`: For parallel processing
- `time`: For TTL calculations
- `datetime`: For timestamp generation

## Environment Variables

- `DYNAMODB_TABLE_NAME`: The name of the DynamoDB table to access

## IAM Permissions

The function requires the following DynamoDB permissions:

- `dynamodb:Query` - To fetch receipt word labels by label
- `dynamodb:GetItem` - To check existing cache entries
- `dynamodb:PutItem` - To add new cache entries
- `dynamodb:UpdateItem` - To update existing cache entries
- `dynamodb:DescribeTable` - For table metadata

## Integration

This function works in conjunction with the `label_validation_count` API route:

1. Cache updater populates the cache every 5 minutes
2. API route checks cache first before falling back to real-time queries
3. This dramatically improves API response times for frequently accessed data

## Monitoring

- CloudWatch logs provide detailed execution information
- Metrics include cache hit/miss ratios for each label
- Function execution duration and success/failure rates
- Log retention: 30 days
