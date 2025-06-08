# Label Validation Counts

This API route provides validation count statistics for all `CORE_LABELS` with intelligent caching for optimal performance.

## Overview

The route returns validation counts (VALID, INVALID, PENDING, NEEDS_REVIEW, NONE) for each core label used in receipt processing. It leverages a two-tier caching strategy to provide fast response times while ensuring data freshness.

## Caching Strategy

### Cache-First Approach

1. **Efficient cache lookup**: Single `listLabelCountCaches` query retrieves all cached counts at once
2. **Real-time fallback**: For cache misses, it fetches counts in real-time from `ReceiptWordLabel` records
3. **Hybrid response**: Returns cached data for available labels and real-time data for others

### Cache Population

- A scheduled Lambda function (`label_count_cache_updater`) runs every 5 minutes
- Updates all core label counts with a 6-minute TTL
- Ensures cache is always fresh with automatic expiration

## API Endpoints

### GET /label-validation-count

Returns validation counts for all core labels.

**Response Format:**

```json
{
  "ADDRESS_LINE": {
    "VALID": 150,
    "INVALID": 25,
    "PENDING": 10,
    "NEEDS_REVIEW": 5,
    "NONE": 0
  },
  "DATE": {
    "VALID": 200,
    "INVALID": 15,
    "PENDING": 8,
    "NEEDS_REVIEW": 2,
    "NONE": 1
  }
  // ... other core labels
}
```

## Core Labels

The route processes counts for the following core labels:

**Merchant & Store Info:**

- MERCHANT_NAME
- STORE_HOURS
- PHONE_NUMBER
- WEBSITE
- LOYALTY_ID

**Location/Address:**

- ADDRESS_LINE

**Transaction Info:**

- DATE
- TIME
- PAYMENT_METHOD
- COUPON
- DISCOUNT

**Line Items:**

- PRODUCT_NAME
- QUANTITY
- UNIT_PRICE
- LINE_TOTAL

**Totals & Taxes:**

- SUBTOTAL
- TAX
- GRAND_TOTAL

## Performance Characteristics

- **Full cache hit**: ~10-50ms response time (single DynamoDB query for all labels)
- **Cache miss**: 1-5 seconds (depends on data volume)
- **Hybrid**: Proportional to cache miss ratio
- **Concurrent processing**: Up to 5 labels fetched simultaneously for real-time queries
- **Cache efficiency**: Single `listLabelCountCaches` query instead of 35+ individual queries

## Error Handling

- Individual label fetch failures are logged but don't affect other labels
- Returns partial results if some labels fail
- 405 Method Not Allowed for non-GET requests

## Dependencies

- `receipt_dynamo`: DynamoDB operations
- `concurrent.futures`: Parallel processing for cache misses
- DynamoDB table with GSI indexes for efficient querying

## Related Components

- **Cache Updater**: `infra/lambda_functions/label_count_cache_updater/`
- **Entity**: `receipt_dynamo/entities/label_count_cache.py`
- **DynamoDB Operations**: `receipt_dynamo/data/_label_count_cache.py`
