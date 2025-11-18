# Create Labels Step Function - AWS CLI Commands

## Step Function ARN

```
arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf
```

---

## Forwards: Create Labels (AWS CLI)

### Basic Execution

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --input '{}' \
  --region us-east-1
```

### With Limit (Process Only N Receipts)

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --input '{"limit": 10}' \
  --region us-east-1
```

### With Reverse Order

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --input '{"reverse_order": true}' \
  --region us-east-1
```

### With Limit and Reverse Order

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --input '{"limit": 10, "reverse_order": true}' \
  --region us-east-1
```

### With Custom Execution Name

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --name "create-labels-$(date +%s)" \
  --input '{}' \
  --region us-east-1
```

### Check Execution Status

```bash
# Replace <execution-arn> with the ARN from the start-execution response
aws stepfunctions describe-execution \
  --execution-arn <execution-arn> \
  --region us-east-1
```

### Get Execution History

```bash
aws stepfunctions get-execution-history \
  --execution-arn <execution-arn> \
  --region us-east-1 \
  --max-results 20 \
  --reverse-order
```

### List Recent Executions

```bash
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --region us-east-1 \
  --max-results 10
```

---

## Complete Workflow Example

### Step 1: Create Labels (Forwards)

```bash
# Start execution
EXECUTION_ARN=$(aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --input '{}' \
  --region us-east-1 \
  --query 'executionArn' \
  --output text)

echo "Execution ARN: $EXECUTION_ARN"

# Wait and check status
aws stepfunctions describe-execution \
  --execution-arn $EXECUTION_ARN \
  --region us-east-1 \
  --query '{Status:status,StartDate:startDate,StopDate:stopDate}'
```

### Step 2: Monitor Progress

```bash
# Check status periodically
watch -n 30 "aws stepfunctions describe-execution \
  --execution-arn $EXECUTION_ARN \
  --region us-east-1 \
  --query '{Status:status,StartDate:startDate}' \
  --output json"
```

---

## Quick Reference

### Forwards (Create Labels)

```bash
# Start (normal order)
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --input '{}' \
  --region us-east-1

# Start (reverse order)
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --input '{"reverse_order": true}' \
  --region us-east-1

# Check status
aws stepfunctions describe-execution \
  --execution-arn <execution-arn> \
  --region us-east-1
```

---

## Troubleshooting

### Check if Step Function is Running

```bash
aws stepfunctions list-executions \
  --state-machine-arn arn:aws:states:us-east-1:681647709217:stateMachine:create-labels-dev-sf \
  --region us-east-1 \
  --status-filter RUNNING
```

### Stop a Running Execution

```bash
aws stepfunctions stop-execution \
  --execution-arn <execution-arn> \
  --region us-east-1 \
  --error "UserRequested" \
  --cause "Stopped by user"
```

### View Execution Output

```bash
aws stepfunctions describe-execution \
  --execution-arn <execution-arn> \
  --region us-east-1 \
  --query 'output' \
  --output text | jq .
```

### View Execution Errors

```bash
aws stepfunctions describe-execution \
  --execution-arn <execution-arn> \
  --region us-east-1 \
  --query '{Status:status,Error:error,Cause:cause}'
```

