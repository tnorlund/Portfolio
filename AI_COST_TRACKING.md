# AI Cost Tracking

Comprehensive tracking system for AI service costs (OpenAI, Anthropic, Google Places).

## Quick Start
```bash
# View costs (last 30 days)
python scripts/ai_usage_report.py

# API access
curl "https://api.tnorlund.com/ai_usage?start_date=2024-12-18&aggregation=day"
```

## Components
- **AIUsageMetric Entity** (`receipt_dynamo/entities/ai_usage_metric.py`) - DynamoDB storage
- **Cost Calculator** (`receipt_label/utils/cost_calculator.py`) - Pricing models
- **Usage Tracker** (`receipt_label/utils/ai_usage_tracker.py`) - Automatic tracking
- **API Endpoint** (`infra/routes/ai_usage/`) - Query interface

## Automatic Tracking
```python
from receipt_label.utils import ClientManager

client_manager = ClientManager.from_env()
client_manager.set_tracking_context(job_id="job-123")

# All API calls automatically tracked
response = client_manager.openai.chat.completions.create(...)
```

## Cost Optimization
1. Use GPT-3.5 over GPT-4 (15x cheaper)
2. Use Claude Haiku over Opus (60x cheaper)
3. Cache embeddings and set max_tokens
4. Use batch APIs for 50% OpenAI discount

## Environment Variables
- `TRACK_AI_USAGE=true` - Enable tracking
- `TRACK_TO_FILE=false` - Local development logging
- `DYNAMODB_TABLE_NAME` - Storage table
