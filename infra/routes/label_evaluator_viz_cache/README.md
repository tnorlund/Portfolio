# Label Evaluator Visualization Cache

This component generates cached visualization data for the portfolio website's Label Evaluator visualization.

## Architecture

```text
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│  LangSmith Export   │     │   S3 Batch Bucket   │     │    DynamoDB     │
│  (Parquet files)    │     │  (Step Fn results)  │     │                 │
├─────────────────────┤     ├─────────────────────┤     ├─────────────────┤
│ • image_id          │     │ • currency/         │     │ • Receipt CDN   │
│ • receipt_id        │     │   all_decisions     │     │ • Words + bbox  │
│ • execution_id      │     │ • metadata/         │     │ • Labels        │
│ • duration_seconds  │     │   all_decisions     │     │ • width/height  │
└─────────┬───────────┘     │ • financial/        │     └────────┬────────┘
          │                 │   all_decisions     │              │
          │                 └─────────┬───────────┘              │
          └───────────────────────────┼──────────────────────────┘
                                      ▼
                        ┌─────────────────────────┐
                        │   Cache Generator       │
                        │   (Lambda)              │
                        └─────────────┬───────────┘
                                      ▼
                        ┌─────────────────────────┐
                        │  viz-sample-data.json   │
                        │  (S3 Cache Bucket)      │
                        └─────────────────────────┘
                                      ▼
                        ┌─────────────────────────┐
                        │  Portfolio Website      │
                        │  (Label Evaluator Viz)  │
                        └─────────────────────────┘
```

## Data Sources

### LangSmith Parquet Export
- **Source**: LangSmith bulk export to S3
- **Format**: Parquet files
- **Contains**: Trace metadata including `image_id`, `receipt_id`, `execution_id`, and timing information
- **Path**: `s3://{langsmith_export_bucket}/traces/export_id={export_id}/`

### S3 Batch Bucket (Step Function Results)
- **Source**: Label Evaluator Step Function outputs
- **Format**: JSON files per receipt
- **Contains**: Detailed evaluation decisions from each subagent
- **Paths**:
  - `currency/{execution_id}/{image_id}_{receipt_id}.json`
  - `metadata/{execution_id}/{image_id}_{receipt_id}.json`
  - `financial/{execution_id}/{image_id}_{receipt_id}.json`
  - `results/{execution_id}/{image_id}_{receipt_id}.json` (geometric)

### DynamoDB
- **Source**: Main receipts table
- **Contains**:
  - Receipt metadata (CDN S3 keys, dimensions)
  - Word bounding boxes
  - Word labels

## Components

### API Lambda (`index.py`)
- **Endpoint**: `GET /label_evaluator/visualization`
- **Purpose**: Serves the cached `viz-sample-data.json` to the frontend
- **Memory**: 256 MB
- **Timeout**: 30 seconds

### Cache Generator Lambda (`cache_generator.py`)
- **Purpose**: Generates the visualization cache by combining all data sources
- **Memory**: 2048 MB (required for Parquet processing)
- **Timeout**: 300 seconds (5 minutes)
- **Layers**:
  - `receipt-dynamo` - DynamoDB client and entities
  - `receipt-langsmith` - Parquet reader utilities

## Output Format

The generated `viz-sample-data.json` contains:

```json
{
  "execution_id": "viz-full-run-1767764200",
  "receipts": [
    {
      "image_id": "abc123",
      "receipt_id": 1,
      "merchant_name": "Example Store",
      "issues_found": 3,
      "words": [
        {
          "text": "TOTAL",
          "label": "GRAND_TOTAL",
          "line_id": 10,
          "word_id": 0,
          "bbox": {"x": 100, "y": 500, "width": 80, "height": 20}
        }
      ],
      "geometric": { "issues_found": 1, "issues": [...], "duration_seconds": 2.5 },
      "currency": { "decisions": {...}, "all_decisions": [...], "duration_seconds": 10.2 },
      "metadata": { "decisions": {...}, "all_decisions": [...], "duration_seconds": 8.1 },
      "financial": { "decisions": {...}, "all_decisions": [...], "duration_seconds": 15.3 },
      "cdn_s3_key": "receipts/abc123_1.jpg",
      "width": 800,
      "height": 1200
    }
  ],
  "summary": {
    "total_receipts": 10,
    "receipts_with_issues": 7
  },
  "cached_at": "2026-01-07T06:00:00Z"
}
```

## Infrastructure

### S3 Buckets
- **Cache Bucket**: Stores `viz-sample-data.json`
- Read by API Lambda, written by Generator Lambda

### IAM Permissions
- **API Lambda**: Read from cache bucket
- **Generator Lambda**:
  - Read from LangSmith export bucket (Parquet files)
  - Read from batch bucket (Step Function results)
  - Read/write to cache bucket
  - Query DynamoDB (receipts, words, labels)

## Environment Variables

### API Lambda
- `S3_CACHE_BUCKET`: Cache bucket name

### Generator Lambda
- `S3_CACHE_BUCKET`: Cache bucket name
- `LANGSMITH_EXPORT_BUCKET`: LangSmith export bucket
- `LANGSMITH_EXPORT_PREFIX`: Path to Parquet files (e.g., `traces/export_id=xxx/`)
- `BATCH_BUCKET`: Step Function results bucket
- `DYNAMODB_TABLE_NAME`: Main receipts table name

## Usage

### Manually Trigger Cache Generation
```shell
aws lambda invoke \
  --function-name "label-evaluator-viz-cache-dev-generator-lambda-xxxxx" \
  --invocation-type Event \
  --payload '{}' \
  /tmp/output.json
```

### Check Cache Contents
```shell
aws s3 cp s3://{cache-bucket}/viz-sample-data.json - | jq '.summary'
```
