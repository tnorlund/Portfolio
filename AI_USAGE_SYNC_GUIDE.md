# AI Usage Sync - Cloud-Based Cost Tracking

This guide explains how to set up automated syncing of AI usage data from service providers, so you don't need to run anything on your laptop.

## Current Reality

### ❌ What Providers DON'T Offer
- **OpenAI**: No public usage API (must use dashboard or billing emails)
- **Anthropic**: No public usage API (console access only)
- **Google Cloud**: Has APIs but requires complex setup

### ✅ What We CAN Do

## 1. OpenAI - Email-Based Tracking

OpenAI sends daily usage emails. We can process these automatically:

```python
# Lambda function to process OpenAI usage emails
# Forward emails to usage-tracking@yourdomain.com -> SES -> Lambda

def process_openai_usage_email(email_content):
    # Parse email for usage data
    usage_pattern = r"Total usage.*?\$(\d+\.\d+)"
    match = re.search(usage_pattern, email_content)
    if match:
        daily_cost = float(match.group(1))
        # Store in DynamoDB
```

### Setup:
1. Set up AWS SES to receive emails at `usage@yourdomain.com`
2. Create SES rule to trigger Lambda on OpenAI emails
3. Parse usage data and store in DynamoDB

## 2. Anthropic - Web Scraping (with Puppeteer)

Since Anthropic has no API, use headless Chrome in Lambda:

```python
# Lambda with Puppeteer layer
def scrape_anthropic_usage():
    # Log into Anthropic console
    # Navigate to usage page
    # Extract usage data
    # Store in DynamoDB
```

### Setup:
1. Store Anthropic credentials in AWS Secrets Manager
2. Use Lambda with Puppeteer layer (or container)
3. Run daily to scrape console

## 3. Google Cloud - Native Integration

Google Cloud has proper APIs:

```python
from google.cloud import bigquery

def sync_google_cloud_costs():
    # Query BigQuery billing export
    query = """
    SELECT 
        service.description,
        SUM(cost) as total_cost,
        COUNT(*) as request_count
    FROM `project.dataset.gcp_billing_export`
    WHERE service.description LIKE '%Places API%'
        AND DATE(usage_start_time) = CURRENT_DATE()
    GROUP BY service.description
    """
    # Store results in DynamoDB
```

### Setup:
1. Enable billing export to BigQuery
2. Set up service account with BigQuery access
3. Query billing data hourly

## 4. Alternative: Cost Tracking Services

### Use Third-Party Services
- **CloudHealth**: Aggregates cloud costs
- **CloudCheckr**: Multi-cloud cost management
- **Vantage**: Specifically good for AI/ML costs

These services can:
- Pull data from multiple providers
- Provide APIs you can query
- Send webhooks on cost changes

## 5. Recommended Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   OpenAI        │     │   Anthropic     │     │  Google Cloud   │
│ (Email Report)  │     │ (Web Scraping)  │     │ (Billing API)   │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                         │
         ▼                       ▼                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AWS Lambda Functions                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐        │
│  │Email Parser │  │Web Scraper   │  │BigQuery Client │        │
│  └─────────────┘  └──────────────┘  └────────────────┘        │
└─────────────────────────────┬────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │    DynamoDB     │
                    │ (Unified Costs) │
                    └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   API Gateway   │
                    │   /ai_usage     │
                    └─────────────────┘
```

## 6. Hybrid Approach (Recommended)

Since providers don't offer good APIs, use a hybrid approach:

1. **Real-time tracking** (current implementation):
   - Track costs as calls happen
   - Immediate visibility
   - Good for development

2. **Daily reconciliation** (new):
   - Pull actual costs from providers
   - Reconcile with tracked data
   - Catch any missed calls

3. **Monthly validation**:
   - Compare with actual bills
   - Adjust tracking accuracy

## Implementation Steps

### Step 1: Set Up Email Processing (OpenAI)
```bash
# Configure SES domain
aws ses verify-domain-identity --domain yourdomain.com

# Create receipt rule
aws ses put-receipt-rule --rule-set-name default-rule-set \
  --rule Name=openai-usage,Enabled=true,Actions=[{LambdaAction:{FunctionArn:arn:aws:lambda:...}}]
```

### Step 2: Create Secrets for Web Scraping
```bash
# Store Anthropic credentials
aws secretsmanager create-secret \
  --name anthropic-console-credentials \
  --secret-string '{"username":"your-email","password":"your-password"}'
```

### Step 3: Enable Google Cloud Billing Export
1. Go to Google Cloud Console
2. Navigate to Billing > Billing export
3. Set up BigQuery export
4. Note dataset location

### Step 4: Deploy Sync Functions
```bash
cd infra
pulumi up  # Deploys the ai_usage_sync Lambda
```

## Cost Tracking Without Laptop

Once set up, the system runs entirely in the cloud:

1. **Hourly**: Lambda pulls/processes usage data
2. **Real-time**: Your app tracks usage as it happens
3. **Daily**: Reconciliation ensures accuracy
4. **API**: Query costs anytime via `/ai_usage`

No laptop scripts needed - everything runs in AWS!

## Limitations

- **OpenAI**: 24-hour delay (email-based)
- **Anthropic**: Fragile (web scraping)
- **Google**: Requires GCP setup

For immediate needs, the real-time tracking (current implementation) gives good estimates. The sync functions provide reconciliation with actual costs.