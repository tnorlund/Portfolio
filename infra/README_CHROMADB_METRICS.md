# ChromaDB Compaction Metrics Collection

Quick script to gather comprehensive metrics for all ChromaDB compaction infrastructure components.

## Usage

```bash
# Get metrics for the last 24 hours (default)
./get_chromadb_metrics.sh

# Get metrics for the last 6 hours
./get_chromadb_metrics.sh 6

# Get metrics for the last 48 hours and save to file
./get_chromadb_metrics.sh 48 metrics_report.txt

# Save to file to avoid terminal pipe errors
./get_chromadb_metrics.sh 24 chromadb_metrics_$(date +%Y%m%d_%H%M%S).txt
```

## What It Collects

### Lambda Functions
- **Stream Processor**: Invocations, duration, errors
- **Enhanced Compaction**: Invocations, duration

### SQS Queues  
- **Lines Queue**: Messages sent/received
- **Words Queue**: Messages sent/received
- **Dead Letter Queues**: Error message counts

### S3 Storage
- **ChromaDB Bucket**: Current contents, structure, CloudWatch metrics

### Custom Metrics
- Checks for any custom ChromaDB namespace metrics

## Output Format

The script provides structured output with:
- Timestamped data points
- Tabular format for easy reading
- Zero-value filtering (only shows periods with activity)
- Error handling for missing metrics

## Resource Names

The script automatically uses these resource identifiers:
- Stream Processor: `chromadb-dev-lambdas-stream-processor-e79a370`
- Enhanced Compaction: `chromadb-dev-lambdas-enhanced-compaction-79f6426`  
- Lines Queue: `chromadb-dev-queues-lines-queue-b3d38e1`
- Words Queue: `chromadb-dev-queues-words-queue-6e2171c`
- S3 Bucket: `chromadb-dev-shared-buckets-vectors-c239843`

## Prerequisites

- AWS CLI configured with appropriate permissions
- CloudWatch metrics read access
- S3 list permissions for the ChromaDB bucket
- macOS compatible (uses `date -v` syntax)

## Example Output

```
=== ChromaDB Compaction Metrics Report ===
Time Range: 2025-08-24T23:49:38 to 2025-08-25T05:49:38 (6 hours)

=== LAMBDA FUNCTIONS ===
Stream Processor Invocations:
+-----------------------------+--------+
|  2025-08-25T03:49:00+00:00  |  25.0  |
|  2025-08-25T02:49:00+00:00  |  169.0 |
+-----------------------------+--------+
```