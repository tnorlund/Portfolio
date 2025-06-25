# Notification System Setup - Run and Done

The notification system is now fully integrated into the Pulumi infrastructure and ready to deploy.

## Configuration

### 1. Update Email Address

Edit the appropriate Pulumi stack configuration file:

**For Development:**
```bash
# Edit infra/Pulumi.dev.yaml
# Replace "admin@example.com" with your actual email address
```

**For Production:**
```bash
# Edit infra/Pulumi.prod.yaml
# Replace "admin@example.com" with your actual email address
```

### 2. Multiple Email Recipients (Optional)

To add multiple recipients, update the config file:

```yaml
portfolio:notification_emails:
  - "admin@yourcompany.com"
  - "ops@yourcompany.com"
  - "oncall@yourcompany.com"
```

## Deployment

### One-Time Setup

1. **Deploy the infrastructure:**
   ```bash
   cd infra
   pulumi up
   ```

2. **Confirm email subscriptions:**
   - Check your email for AWS SNS subscription confirmations
   - Click the confirmation links (one for each topic)

That's it! The system is now active.

## What Gets Monitored

### Automatic Monitoring (No Code Changes Required)

The CloudWatch Events rule automatically captures:
- ✅ ALL Step Function failures (FAILED, TIMED_OUT, ABORTED states)
- ✅ From ALL step functions in your AWS account/region
- ✅ Without modifying any existing step functions

### Enhanced Monitoring (Optional)

For step functions with enhanced error handling:
- Detailed error context in notifications
- Retry attempt information
- Individual task failure details
- CloudWatch logs integration

## Email Notifications

You'll receive emails for:

1. **Step Function Failures**
   - Subject: "Step Function Error: [Function Name]"
   - Body: Execution details, error message, and CloudWatch logs link

2. **Critical Infrastructure Errors**
   - Lambda DLQ messages
   - CloudWatch alarm breaches
   - Other critical system failures

## Testing the System

To test notifications are working:

```bash
# Trigger a test step function failure
aws stepfunctions start-execution \
  --state-machine-arn <your-step-function-arn> \
  --input '{"test": "failure"}'
```

## Troubleshooting

### Not Receiving Emails?

1. **Check subscription status:**
   ```bash
   aws sns list-subscriptions-by-topic \
     --topic-arn $(pulumi stack output step_function_failure_topic_arn)
   ```

2. **Verify email is confirmed:**
   - Status should be "Confirmed" not "PendingConfirmation"

3. **Check CloudWatch Events:**
   ```bash
   aws events list-rules --name-prefix receipt-processing
   ```

### Email in Spam?

Add these AWS SNS email addresses to your safe sender list:
- no-reply@sns.amazonaws.com
- no-reply@email.amazonaws.com

## Cost Considerations

- SNS: First 1,000 email notifications free/month, then $2.00 per 100,000
- CloudWatch Events: $1.00 per million events
- Typical usage: < $1/month for most applications

## Security

- Email addresses are stored in Pulumi config (not in code)
- SNS topics are encrypted at rest
- IAM roles follow least-privilege principle
- No sensitive data in notifications (only IDs and error types)

## Next Steps

After deployment, consider:
1. Setting up a dedicated ops email list
2. Creating CloudWatch dashboards
3. Adding Slack/PagerDuty integration
4. Implementing error aggregation/deduplication