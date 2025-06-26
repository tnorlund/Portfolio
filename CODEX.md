# AI Assistant Guide for Portfolio Project

This document provides context for AI assistants (OpenAI Codex, GitHub Copilot, etc.) working on this codebase.

## Boto3 Type Annotations

This project uses boto3 type stubs for AWS SDK type safety. Key implementation details:

### Pattern to Follow
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_s3 import S3Client

# In function/method:
client: DynamoDBClient = boto3.client("dynamodb")
```

### Why This Pattern?
- **Development**: Full type hints and autocomplete in IDEs
- **Runtime**: No import overhead or dependency requirements
- **Testing**: Works in CI without dev dependencies

### Current AWS Services with Typing
- DynamoDB: `mypy_boto3_dynamodb.DynamoDBClient`
- S3: `mypy_boto3_s3.S3Client`

### Adding New Services
1. Update `receipt_dynamo/pyproject.toml`: Add service to `boto3-stubs[dynamodb,s3,NEW_SERVICE]`
2. Import with TYPE_CHECKING guard
3. Annotate client creation: `client: NewServiceClient = boto3.client("new-service")`

## Project Structure
- `receipt_dynamo/`: DynamoDB interface package
- `receipt_label/`: Label processing package
- `portfolio/`: TypeScript frontend
- `infra/`: Pulumi infrastructure as code

## Testing
- Unit tests: `pytest tests/unit`
- Integration tests: `pytest tests/integration` (uses moto for AWS mocking)
- End-to-end tests: `pytest -m end_to_end` (requires real AWS credentials)

## Code Style
- Python: black + isort formatting
- Type checking: mypy with strict settings
- Pre-commit hooks enforce style (can bypass with `--no-verify` if needed)
