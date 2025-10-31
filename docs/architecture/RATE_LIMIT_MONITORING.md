# Rate Limit Monitoring and Detection

## Overview

At scale, understanding rate limiting is critical for maintaining reliability. This document explains how to detect and monitor rate limiting in the LangGraph receipt analysis workflow.

---

## How We Detect Rate Limiting

### 1. Automatic Detection

The retry logic automatically detects rate limit errors:

```python
# receipt_label/langchain/utils/retry.py

def is_retriable_error(exception: Exception) -> bool:
    error_str = str(exception).lower()
    
    # Rate limiting
    if "rate limit" in error_str or "429" in error_str:
        # Track rate limit event
        detector.record_rate_limit(...)
        return True
    
    # Network/timeout errors
    if any(term in error_str for term in ["timeout", "connection"]):
        return True
    
    # Server errors (5xx)
    if any(code in error_str for code in ["500", "502", "503", "504"]):
        return True
```

### 2. What Triggers Retries

- **HTTP 429**: Rate limit errors
- **"rate limit"** in error message
- **Network errors**: Connection failures, timeouts
- **5xx errors**: Server errors (500, 502, 503, 504)

### 3. Exponential Backoff

When rate limited, the system automatically retries with exponential backoff:

```python
# Default configuration
max_retries = 3           # Try 3 times
initial_delay = 2.0       # Wait 2 seconds before first retry
backoff_factor = 2.0      # Double the delay each time
max_delay = 60.0          # Never wait more than 60 seconds

# Sequence:
# Attempt 1: Immediate
# Attempt 2: Wait 2 seconds (initial_delay)
# Attempt 3: Wait 4 seconds (initial_delay * backoff_factor^1)
# Attempt 4: Wait 8 seconds (initial_delay * backoff_factor^2)
```

---

## Monitoring at Scale

### CloudWatch Metrics

Add these custom metrics to your CloudWatch dashboard:

```python
import boto3

cloudwatch = boto3.client('cloudwatch')

# Track rate limit events
cloudwatch.put_metric_data(
    Namespace='ReceiptAnalysis',
    MetricData=[
        {
            'MetricName': 'RateLimitErrors',
            'Value': 1,
            'Unit': 'Count',
            'Dimensions': [
                {'Name': 'Phase', 'Value': 'phase1'},
            ]
        }
    ]
)
```

### Log Analysis

Monitor logs for these patterns:

```bash
# Count rate limit errors
aws logs filter-log-events \
    --log-group-name /aws/lambda/receipt-analysis \
    --filter-pattern "rate limit" \
    --query 'events[*].message' \
    | wc -l

# Get detailed rate limit events
aws logs filter-log-events \
    --log-group-name /aws/lambda/receipt-analysis \
    --filter-pattern "⚠️ Rate limit" \
    --start-time $(date -u -d '1 hour ago' +%s)000
```

### Key Metrics to Track

1. **Rate Limit Occurrences**
   ```
   Rate: < 1 per minute = Normal
   Rate: 1-5 per minute = Warning
   Rate: > 5 per minute = Critical
   ```

2. **Retry Success Rate**
   ```
   Success rate = (Retry attempts that succeeded) / (Total retry attempts)
   Target: > 80%
   ```

3. **Average Retry Count**
   ```
   If avg_retry_count > 2: System is under stress
   ```

4. **Error Distribution**
   ```
   - Phase 1 (120b model) errors vs Phase 2 (20b model) errors
   - Network errors vs rate limit errors vs other errors
   ```

---

## Detecting Rate Limiting Patterns

### 1. Check Recent Rate Limit Events

```python
from receipt_label.langchain.utils.rate_limit_detector import get_rate_limit_detector

detector = get_rate_limit_detector()

# Get summary
print(detector.get_detection_summary())
# Output: "phase1: 8.5 req/s, 3 errors/min | phase2: 12.2 req/s, 7 errors/min"

# Get detailed events
events = detector.get_rate_limit_events_summary(last_n=20)
for event in events:
    print(f"{event['timestamp']}: {event['phase']} - {event['receipt_id']}")
```

### 2. Check Rate Statistics

```python
# Get current rate statistics
phase1_stats = detector.get_rate_stats("phase1")
phase2_stats = detector.get_rate_stats("phase2")

print(f"Phase 1: {phase1_stats}")
print(f"Phase 2: {phase2_stats}")

# Example output:
# {
#     "requests_per_second": 8.5,
#     "errors_per_minute": 3,
#     "is_rate_limited": False,
#     "total_errors": 15
# }
```

### 3. Determine if Rate Limited

```python
# Check if system should apply backoff
should_backoff = detector.should_apply_backoff("phase1")

if should_backoff:
    print("⚠️ Rate limited - apply backoff")
    # Increase delay between requests
    # Reduce parallel processing
```

---

## Signs of Rate Limiting

### Warning Signs

1. **Increase in retry attempts**
   - First retry needed: Normal
   - Second retry needed: Warning
   - Third retry needed: Critical

2. **HTTP 429 responses**
   ```bash
   grep "429" /var/log/application.log
   ```

3. **Error patterns**
   ```
   Phase 1 errors clustering together
   → Likely rate limiting (120b model is expensive)
   
   Phase 2 errors clustering together
   → Likely rate limiting (many parallel requests)
   ```

4. **Latency spikes**
   ```
   If processing time suddenly increases:
   → Likely retry logic kicking in
   → Check for rate limiting
   ```

### Diagnostic Steps

```python
# 1. Check error logs for 429 or "rate limit"
# 2. Check retry statistics
detector = get_rate_limit_detector()
stats = detector.get_rate_stats("phase1")

if stats["errors_per_minute"] > 5:
    # Rate limiting detected
    print("⚠️ Rate limit detected!")
    print(f"Errors per minute: {stats['errors_per_minute']}")
    print(f"Requests per second: {stats['requests_per_second']}")
```

---

## Handling Rate Limits at Scale

### 1. Increase Retry Delays

```python
# Increase initial delay for phase 1 (expensive model)
response = await retry_with_backoff(
    invoke_llm,
    max_retries=3,
    initial_delay=5.0,  # Start with 5 seconds
)
```

### 2. Add Request Throttling

```python
import asyncio

# Limit concurrent requests
max_concurrent = 5
semaphore = asyncio.Semaphore(max_concurrent)

async def throttled_request(request_func):
    async with semaphore:
        return await request_func()
```

### 3. Circuit Breaker Pattern

```python
from circuitbreaker import circuit

@circuit(failure_threshold=10, recovery_timeout=60)
async def phase1_with_circuit_breaker(state, api_key):
    # Automatically breaks if too many failures
    return await phase1_currency_analysis(state, api_key)
```

### 4. Queue-Based Processing

If rate limiting is severe, switch to a queue-based system:

```python
# Put requests in SQS queue
queue = boto3.resource('sqs').Queue('receipt-analysis-queue')

# Process with controlled rate
# Prevents overwhelming the API
```

---

## CloudWatch Dashboard Queries

### Metric Queries

```sql
-- Rate limit errors
SELECT COUNT() AS rate_limit_errors
FROM Logs
WHERE message LIKE '%rate limit%' OR status_code = 429
GROUP BY timestamp BIN(1m)

-- Request rate by phase
SELECT COUNT() / 60 AS requests_per_second
FROM Logs
WHERE phase = 'phase1'
GROUP BY timestamp BIN(1m)

-- Retry statistics
SELECT COUNT() AS total_retries, phase
FROM Logs
WHERE message LIKE '%Retrying in%'
GROUP BY phase, timestamp BIN(1m)
```

### Alarms

```bash
# Create CloudWatch alarm for rate limit errors
aws cloudwatch put-metric-alarm \
    --alarm-name ReceiptAnalysis-RateLimitErrors \
    --alarm-description "Alert when rate limit errors exceed threshold" \
    --metric-name RateLimitErrors \
    --namespace ReceiptAnalysis \
    --statistic Sum \
    --period 300 \
    --threshold 10 \
    --comparison-operator GreaterThanThreshold \
    --evaluation-periods 2
```

---

## Summary

### What We Have

✅ **Automatic retry logic** with exponential backoff  
✅ **Rate limit detection** in error messages  
✅ **Timeout configuration** (120 seconds)  
✅ **Logging** of all retry attempts  
✅ **Error tracking** by phase (phase1/phase2)  

### What to Monitor

📊 **Rate limit error frequency** (target: < 1 per minute)  
📊 **Retry success rate** (target: > 80%)  
📊 **Processing latency** (target: < 20 seconds)  
📊 **Error distribution** (by phase, by error type)  

### When to Act

🔴 **Critical**: Rate limit errors > 5 per minute  
🟡 **Warning**: Rate limit errors > 1 per minute  
🟢 **Normal**: Rate limit errors < 1 per minute  

---

## Next Steps

1. ✅ Implement rate limit detection in retry logic
2. ⏭️ Add CloudWatch metrics to track rate limits
3. ⏭️ Create CloudWatch dashboards for monitoring
4. ⏭️ Set up alarms for automatic notifications

