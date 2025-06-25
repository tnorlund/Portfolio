# AI Usage Metrics Route

This route provides AI service usage metrics and cost tracking data.

## Overview

The `/ai_usage` endpoint aggregates and returns usage data for AI services including OpenAI, Anthropic, and Google Places API. It queries the AIUsageMetric entities stored in DynamoDB to provide cost analysis and usage statistics.

## API Endpoint

```
GET /ai_usage
```

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start_date` | string | 30 days ago | Start date in YYYY-MM-DD format |
| `end_date` | string | today | End date in YYYY-MM-DD format |
| `service` | string | all | Filter by service: openai, anthropic, google_places |
| `operation` | string | all | Filter by operation: completion, embedding, code_review, place_lookup |
| `aggregation` | string | day,service,model,operation | Comma-separated aggregation levels |

### Response Format

```json
{
  "summary": {
    "total_cost_usd": 127.43,
    "total_tokens": 450000,
    "total_api_calls": 892,
    "average_cost_per_call": 0.143,
    "date_range": {
      "start": "2024-11-25",
      "end": "2024-12-25"
    }
  },
  "aggregations": {
    "by_day": {
      "2024-12-24": {
        "cost_usd": 15.67,
        "tokens": 45000,
        "api_calls": 78,
        "services": ["openai", "anthropic"]
      }
    },
    "by_service": {
      "openai": {
        "cost_usd": 89.12,
        "tokens": 380000,
        "api_calls": 712,
        "models": ["gpt-3.5-turbo", "text-embedding-3-small"],
        "operations": ["completion", "embedding"],
        "average_cost_per_call": 0.125
      }
    },
    "by_model": {
      "gpt-3.5-turbo": {
        "cost_usd": 45.23,
        "tokens": 200000,
        "api_calls": 412,
        "service": "openai",
        "average_tokens_per_call": 485
      }
    },
    "by_operation": {
      "completion": {
        "cost_usd": 98.34,
        "tokens": 410000,
        "api_calls": 523,
        "services": ["openai", "anthropic"]
      }
    }
  },
  "query": {
    "service": null,
    "operation": null,
    "aggregation": ["day", "service", "model", "operation"]
  }
}
```

## Usage Examples

### Get all usage for the last 7 days
```bash
curl "https://api.tylernorlund.com/ai_usage?start_date=$(date -d '7 days ago' +%Y-%m-%d)"
```

### Get OpenAI usage for December
```bash
curl "https://api.tylernorlund.com/ai_usage?start_date=2024-12-01&end_date=2024-12-31&service=openai"
```

### Get daily costs only
```bash
curl "https://api.tylernorlund.com/ai_usage?aggregation=day"
```

### Get costs by model for a specific date
```bash
curl "https://api.tylernorlund.com/ai_usage?start_date=2024-12-25&end_date=2024-12-25&aggregation=model"
```

## Data Source

The endpoint queries AIUsageMetric entities from DynamoDB using GSI indexes:
- **GSI1**: Query by service and date range
- **GSI2**: Query total costs by date
- **GSI3**: Query by job or batch ID

## Performance

- Response time: ~200-500ms depending on date range
- Handles pagination internally for large date ranges
- Caches aggregations for repeated queries (15-minute TTL)

## Error Handling

| Status Code | Description |
|-------------|-------------|
| 200 | Success |
| 400 | Invalid date format or parameters |
| 500 | Internal server error (DynamoDB query failed) |

Example error response:
```json
{
  "error": "Invalid date format. Use YYYY-MM-DD",
  "statusCode": 400
}
```

## Related Components

- **AIUsageMetric Entity**: `receipt_dynamo/entities/ai_usage_metric.py`
- **Cost Calculator**: `receipt_label/utils/cost_calculator.py`
- **Usage Tracker**: `receipt_label/utils/ai_usage_tracker.py`
- **Infrastructure**: `infra/routes/ai_usage/infra.py`

## Deployment

The Lambda function is deployed with:
- Runtime: Python 3.12
- Architecture: ARM64
- Memory: 1024 MB
- Timeout: 30 seconds
- Environment Variables:
  - `DYNAMODB_TABLE_NAME`: The DynamoDB table name

## Future Enhancements

1. **Webhooks**: Send notifications when costs exceed thresholds
2. **Export**: Add CSV/Excel export functionality
3. **Predictions**: ML-based cost predictions
4. **Budgets**: Per-service budget tracking
5. **Visualization**: Built-in charting capabilities