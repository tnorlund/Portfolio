# AI Cost Tracking System

This system tracks usage and costs for AI services (OpenAI, Anthropic, Google Places) across the Portfolio project.

## Overview

The cost tracking system automatically records:
- API calls to OpenAI (GPT-3.5, GPT-4, embeddings)
- Claude usage in GitHub Actions for PR reviews
- Google Places API calls for merchant validation
- Token usage, costs, latency, and context for each call

## Components

### 1. AIUsageMetric Entity
Located in `receipt_dynamo/receipt_dynamo/entities/ai_usage_metric.py`

Stores metrics in DynamoDB with:
- Service, model, and operation type
- Token counts (input/output/total)
- Cost in USD
- Timestamps and context (job ID, batch ID, PR number)
- Efficient querying via GSI indexes

### 2. Cost Calculator
Located in `receipt_label/receipt_label/utils/cost_calculator.py`

Maintains current pricing for:
- OpenAI models (GPT-3.5, GPT-4, embeddings)
- Anthropic Claude models (Opus, Sonnet, Haiku)
- Google Places API operations

### 3. Usage Tracker
Located in `receipt_label/receipt_label/utils/ai_usage_tracker.py`

Provides:
- Decorators for tracking function calls
- Automatic client wrapping
- Context management (job/batch tracking)
- Local file logging option for development

### 4. API Endpoint
Located in `infra/routes/ai_usage/`

Query usage metrics via:
```
GET /ai_usage?start_date=2024-01-01&service=openai&aggregation=day,service
```

## Usage

### Automatic Tracking with ClientManager

```python
from receipt_label.utils import ClientConfig, ClientManager

# Usage tracking is enabled by default
config = ClientConfig.from_env()
client_manager = ClientManager(config)

# Set context for tracking
client_manager.set_tracking_context(
    job_id="job-123",
    batch_id="batch-456"
)

# All OpenAI calls are automatically tracked
response = client_manager.openai.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello"}]
)
```

### Manual Tracking with Decorators

```python
from receipt_label.utils import AIUsageTracker

tracker = AIUsageTracker(dynamo_client)

@tracker.track_openai_completion
def call_gpt(prompt: str):
    return openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )

@tracker.track_google_places("place_details")
def get_place_details(place_id: str):
    return places_client.get_place(place_id)
```

### GitHub Actions Tracking

Claude usage in PR reviews is automatically tracked via the `track-ai-usage.yml` workflow.

## Viewing Reports

### Command Line Report
```bash
# View last 30 days of usage
python scripts/ai_usage_report.py

# Filter by service
python scripts/ai_usage_report.py --service openai --days 7

# Use local DynamoDB (for development)
python scripts/ai_usage_report.py --local
```

### API Queries
```bash
# Get daily costs for last week
curl "https://api.tnorlund.com/ai_usage?start_date=2024-12-18&aggregation=day"

# Get OpenAI usage by model
curl "https://api.tnorlund.com/ai_usage?service=openai&aggregation=model"
```

## Environment Variables

- `TRACK_AI_USAGE`: Enable/disable tracking (default: "true")
- `TRACK_TO_FILE`: Log to file for local development (default: "false")
- `USER_ID`: User identifier for tracking
- `DYNAMODB_TABLE_NAME`: DynamoDB table for storing metrics

## Cost Optimization Tips

1. **Use GPT-3.5 instead of GPT-4** when possible (15x cheaper)
2. **Use batch APIs** for OpenAI (50% discount)
3. **Cache embeddings** to avoid re-computing
4. **Set max_tokens** to limit response length
5. **Use Claude Haiku** instead of Opus for simpler tasks (60x cheaper)

## Updating Prices

Edit `receipt_label/receipt_label/utils/cost_calculator.py` when providers update their pricing.

## Database Schema

DynamoDB indexes for efficient querying:
- **GSI1**: Query by service and date range
- **GSI2**: Query total costs by date
- **GSI3**: Query by job or batch ID

## Future Enhancements

1. **Budget Alerts**: CloudWatch alarms for cost thresholds
2. **Usage Quotas**: Enforce limits per user/service
3. **Cost Attribution**: Track costs by feature/department
4. **Optimization Suggestions**: ML-based recommendations
5. **Dashboard**: Web UI for real-time monitoring