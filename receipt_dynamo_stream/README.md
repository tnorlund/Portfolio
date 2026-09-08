# receipt_dynamo_stream

Lightweight DynamoDB stream parsing and change detection used by the receipt
update pipeline (summary / line-item queues and inline vector-attribute
freshening). This package keeps stream processing deployable as a zip Lambda
while sharing business logic with other services.

## Features

- Parse DynamoDB stream records into typed receipt entities
- Detect update-relevant field changes on places, word labels, sections
  and summaries
- Freshen denormalized attributes on DynamoDB embedding items inline

## Development

```bash
pip install -e ".[test]"
pytest
```
