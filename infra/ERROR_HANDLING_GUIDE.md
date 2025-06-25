# Step Function Error Handling Implementation Guide

This guide shows how to add comprehensive error handling and notifications to all step functions in the infrastructure.

## Overview

We've implemented:
1. **Centralized notification system** (`notifications.py`)
2. **Error handling utilities** (`step_function_utils.py`)
3. **Enhanced receipt processor** (`step_function_enhanced.py`) as an example

## Key Features Added

### 1. SNS Topics for Notifications
- `step-function-failures` - For all step function execution failures
- `critical-errors` - For Lambda DLQ messages and critical infrastructure issues

### 2. CloudWatch Events Integration
- Automatically captures ALL step function failures across the infrastructure
- Sends notifications without modifying existing step functions

### 3. Enhanced Error Handling Pattern
- Retry policies with exponential backoff
- Catch blocks for graceful error handling
- Timeout configurations at state and execution level
- Detailed error notifications with context

**Note:** Step Function logging configuration is not available in the current pulumi-aws version but doesn't affect core functionality.

## How to Enable Notifications

### Step 1: Configure Email Notifications
```bash
# Set email addresses for notifications
pulumi config set --secret notification_emails '["admin@example.com", "ops@example.com"]'
```

### Step 2: Deploy the Infrastructure
```bash
pulumi up
```

### Step 3: Confirm Email Subscriptions
Check your email for SNS subscription confirmations and click the links.

## Adding Error Handling to Existing Step Functions

### Option 1: Minimal Changes (Recommended)
The CloudWatch Events rule automatically captures failures from ALL step functions without code changes.

### Option 2: Enhanced Error Handling
To add comprehensive error handling to a step function:

```python
from notifications import NotificationSystem
from step_function_utils import wrap_step_function_definition

# In your step function module:
def create_my_step_function(notification_system: NotificationSystem):
    # ... existing code ...
    
    # Wrap your definition with error handling
    enhanced_definition = wrap_step_function_definition(
        original_definition,
        notification_system.step_function_topic_arn
    )
    
    # Update IAM role to include SNS permissions
    # ... add SNS publish permissions ...
    
    # Create the step function with logging
    my_step_function = aws.sfn.StateMachine(
        "my-step-function",
        role_arn=role.arn,
        definition=enhanced_definition,
        logging_configuration=aws.sfn.StateMachineLoggingConfigurationArgs(
            destinations=[...],
            level="ALL"
        ),
        tracing_configuration=aws.sfn.StateMachineTracingConfigurationArgs(
            enabled=True
        )
    )
    
    # Add CloudWatch alarm
    alarm = notification_system.create_step_function_alarm(
        "my-step-function",
        my_step_function.arn
    )
```

## Step Functions Requiring Updates

Based on the codebase analysis, these step functions need error handling:

1. **validate_merchant_step_functions** (`infra/validate_merchant_step_functions/`)
   - Complex 3-phase validation system
   - Critical for data quality
   - Priority: HIGH

2. **word_label_step_functions** (`infra/word_label_step_functions/`)
   - Submit and poll workflows
   - Has CloudWatch alarms but no actions configured
   - Priority: HIGH

3. **embedding_step_functions** (`infra/embedding_step_functions/`)
   - Line embedding processing
   - Long-running batch operations
   - Priority: MEDIUM

4. **validation_pipeline** (`infra/validation_pipeline/`)
   - Completion batch processing
   - Priority: MEDIUM

5. **validation_by_merchant** (`infra/validation_by_merchant/`)
   - Merchant-based validation
   - Priority: MEDIUM

## Example: Adding Error Handling to Merchant Validation

```python
# In validate_merchant_step_functions.py

# Add to the state machine definition:
"ValidateSingleReceiptV2": {
    "Type": "Task",
    "Resource": validate_receipt_lambda.arn,
    "Retry": [
        {
            "ErrorEquals": ["States.TaskFailed", "States.Timeout"],
            "IntervalSeconds": 2,
            "MaxAttempts": 3,
            "BackoffRate": 2.0
        }
    ],
    "Catch": [
        {
            "ErrorEquals": ["States.ALL"],
            "Next": "NotifyValidationError",
            "ResultPath": "$.error"
        }
    ],
    "Next": "NextState"
}
```

## Monitoring and Alerts

### CloudWatch Dashboards
Consider creating dashboards for:
- Step function execution metrics
- Lambda error rates
- DLQ message counts
- API call failures (OpenAI, Google Places, Pinecone)

### Metric Alarms
Add alarms for:
- High error rates
- Execution timeouts
- Throttling events
- Cost anomalies

## Best Practices

1. **Always add timeouts** to prevent stuck executions
2. **Use retry policies** for transient failures
3. **Implement circuit breakers** for external APIs
4. **Add correlation IDs** for request tracing
5. **Log all errors** with context for debugging
6. **Test error scenarios** in development

## Next Steps

1. Deploy the notification system
2. Monitor for failures and adjust thresholds
3. Gradually enhance individual step functions
4. Add integration tests for error scenarios
5. Create runbooks for common failure patterns