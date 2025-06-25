# Cloud-Based AI Cost Tracking (No Laptop Required)

## The Problem
- OpenAI, Anthropic, and Google don't provide real-time usage APIs
- You want costs tracked automatically in the cloud
- No scripts running on your laptop

## The Solution

### 1. Real-Time Tracking (Already Implemented)
Your Lambda functions automatically track costs as they make API calls:

```python
# This happens automatically when using ClientManager
response = client_manager.openai.chat.completions.create(...)
# Cost is tracked immediately in DynamoDB
```

**Pros:**
- Immediate cost visibility
- No external dependencies
- Works for all your Lambda functions

**Cons:**
- Misses costs from local development
- Estimates based on token counts

### 2. AWS Cost Explorer Integration (Recommended)

For AWS-based AI services, use Cost Explorer API:

```bash
# Deploy the cost explorer sync function
cd infra
pulumi up  # Deploys cost_explorer_sync Lambda
```

This Lambda runs hourly and tracks:
- AWS Bedrock costs
- API Gateway usage (proxy for external APIs)
- Lambda invocation patterns

### 3. Email Forwarding for OpenAI

OpenAI sends daily usage emails. Forward them to AWS:

1. **Set up email forwarding:**
   ```
   OpenAI -> usage@yourdomain.com -> AWS SES -> Lambda
   ```

2. **Lambda parses the email:**
   ```python
   def process_openai_email(event):
       # Extract: "Yesterday's usage: $12.34"
       cost = extract_cost_from_email(event['body'])
       store_in_dynamodb(cost)
   ```

### 4. Budget Alerts (Immediate Solution)

Set up CloudWatch alarms for spending thresholds:

```python
# In your infra code
cost_alarm = aws.cloudwatch.MetricAlarm(
    "ai-daily-cost-alarm",
    metric_name="AISpend",
    namespace="Portfolio/AI",
    statistic="Sum",
    period=86400,  # Daily
    threshold=50.0,  # $50/day
    comparison_operator="GreaterThanThreshold",
    alarm_actions=[sns_topic.arn]
)
```

## Practical Implementation Plan

### Phase 1: Enhance Current System (1 day)
✅ Already tracks OpenAI/Anthropic calls from Lambda
- Add CloudWatch metrics for every AI call
- Create cost dashboard in CloudWatch

### Phase 2: AWS Cost Integration (1 day)
- Deploy Cost Explorer sync Lambda
- Runs hourly to pull AWS costs
- Correlates with your tracked metrics

### Phase 3: Email Integration (2 days)
- Set up SES email receiving
- Parse OpenAI/Anthropic billing emails
- Store in DynamoDB for reconciliation

### Phase 4: Third-Party Service (Optional)
Consider using:
- **Vantage.sh**: Tracks AI costs across providers
- **CloudCheckr**: Multi-cloud cost management
- **Datadog**: Has AI/ML cost tracking

## What You Get Without Any Laptop Scripts

1. **Real-time tracking** via your Lambda functions
2. **Hourly AWS cost updates** via Cost Explorer
3. **Daily email reconciliation** for accurate costs
4. **Instant alerts** when spending exceeds thresholds
5. **API endpoint** to query costs anytime: `/ai_usage`

## Quick Start

```bash
# 1. Deploy the cost tracking infrastructure
cd infra
pulumi up

# 2. Set up environment variables
export TRACK_AI_USAGE=true

# 3. View costs via API (no laptop needed!)
curl https://api.tylernorlund.com/ai_usage

# 4. Or use the web dashboard (future enhancement)
# https://tylernorlund.com/dashboard/ai-costs
```

## Cost Breakdown You'll See

```json
{
  "summary": {
    "total_cost_usd": 127.43,
    "by_service": {
      "openai": 89.12,
      "anthropic": 31.54,
      "google_places": 6.77
    }
  },
  "daily_costs": {
    "2024-12-23": 18.34,
    "2024-12-24": 22.11,
    "2024-12-25": 15.87
  },
  "top_operations": {
    "gpt-3.5-turbo-completion": 45.23,
    "text-embedding-3-small": 28.41,
    "claude-pr-review": 31.54
  }
}
```

## No Laptop Required! 🎉

Everything runs in AWS:
- Lambda functions track usage
- EventBridge triggers hourly syncs
- SES processes billing emails
- API Gateway serves the data
- CloudWatch alerts on overspending

Your laptop can be off, and you'll still have complete cost visibility!