# Lambda Memory Analysis Queries

Use these CloudWatch Logs Insights queries to analyze memory usage:

## 1. Memory Usage Analysis
```
fields @timestamp, @message
| filter @message like /REPORT RequestId/
| parse @message "Max Memory Used: * MB" as memoryUsed
| parse @message "Memory Size: * MB" as memorySize  
| parse @message "Duration: * ms" as duration
| filter memoryUsed > 0
| stats avg(memoryUsed), max(memoryUsed), min(memoryUsed), count() by bin(5m)
| sort @timestamp desc
```

## 2. High Memory Usage Detection
```
fields @timestamp, @message
| filter @message like /REPORT RequestId/
| parse @message "Max Memory Used: * MB" as memoryUsed
| parse @message "Memory Size: * MB" as memorySize
| filter memoryUsed > 0
| sort memoryUsed desc
| limit 20
```

## 3. Memory Usage Percentage
```
fields @timestamp, @message
| filter @message like /REPORT RequestId/
| parse @message "Max Memory Used: * MB" as memoryUsed
| parse @message "Memory Size: * MB" as memorySize
| filter memoryUsed > 0 and memorySize > 0
| sort @timestamp desc
| limit 50
```

## 4. Memory vs Duration Correlation
```
fields @timestamp, @message
| filter @message like /REPORT RequestId/
| parse @message "Max Memory Used: * MB" as memoryUsed
| parse @message "Duration: * ms" as duration
| filter memoryUsed > 0 and duration > 0
| stats avg(memoryUsed), avg(duration) by bin(10m)
| sort @timestamp desc
```

## How to Use:
1. Go to CloudWatch → Logs → Log Insights
2. Select log group: `/aws/lambda/embedding-word-poll-lambda-dev-a18caf9`
3. Paste one of the queries above
4. Set time range (last 1 hour, 6 hours, etc.)
5. Click "Run query"