# receipt_dynamo_stream

Lightweight DynamoDB stream parsing and change detection used by the receipt
compaction pipeline. This package keeps stream processing deployable as a zip
Lambda while sharing business logic with other services.

## Features

- Parse DynamoDB stream records into typed receipt entities
- Detect ChromaDB-relevant field changes on metadata and word labels
- Extract CompactionRun signals for embedding completion

## Development

```bash
pip install -e ".[test]"
pytest
```
