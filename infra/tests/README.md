# End-to-End Testing Strategy for Embedding Pipeline

## Overview
This testing strategy combines multiple approaches to ensure comprehensive coverage of the embedding pipeline Lambda functions and Step Functions.

## Testing Layers

### 1. Unit Tests (Fastest)
- Test business logic in isolation
- Mock AWS services and external APIs
- Run locally without infrastructure

### 2. Integration Tests (LocalStack)
- Test Lambda functions with emulated AWS services
- Validate DynamoDB interactions, S3 operations
- Test Step Functions execution paths

### 3. Infrastructure Tests (Pulumi)
- Validate Pulumi program configuration
- Test resource creation and properties
- Ensure proper IAM permissions

### 4. End-to-End Tests (Ephemeral Environments)
- Deploy to real AWS environments
- Test complete workflows
- Validate ChromaDB operations

## Tools and Frameworks

### Core Tools
- **pytest**: Python testing framework
- **LocalStack**: Local AWS service emulation
- **Pulumi Automation API**: Programmatic infrastructure testing
- **moto**: AWS service mocking for unit tests
- **Step Functions Local**: State machine testing

### Testing Libraries
```bash
# requirements-test.txt
pytest>=7.4.0
pytest-asyncio>=0.21.0
pytest-cov>=4.1.0
localstack>=3.0.0
boto3-stubs[essential]>=1.34.0
moto[all]>=4.2.0
pulumi>=3.100.0
docker>=6.1.0
```

## Test Structure

```
tests/
├── unit/                    # Pure unit tests
│   ├── test_list_pending.py
│   ├── test_find_unembedded.py
│   ├── test_submit_openai.py
│   └── test_line_polling.py
├── integration/             # LocalStack integration tests
│   ├── conftest.py         # pytest fixtures for LocalStack
│   ├── test_lambda_handlers.py
│   └── test_step_functions.py
├── infrastructure/          # Pulumi infrastructure tests
│   ├── test_lambda_configs.py
│   └── test_step_function_definitions.py
└── e2e/                    # End-to-end tests
    ├── test_embedding_pipeline.py
    └── test_compaction_workflow.py
```

## Testing Approaches by Component

### Lambda Functions

#### Zip-based Lambdas (Simple Functions)
- **list-pending**: Mock DynamoDB queries
- **find-unembedded**: Mock DynamoDB + S3 operations
- **submit-openai**: Mock OpenAI API calls

#### Container-based Lambdas (ChromaDB Functions)
- **line-polling**: Use test ChromaDB instance
- **compaction**: Test delta merging with sample data

### Step Functions
1. Test state transitions with mocked Lambda responses
2. Validate error handling paths
3. Test Map state parallelization
4. Verify Choice state logic

## LocalStack Configuration

### docker-compose.test.yml
```yaml
version: '3.8'
services:
  localstack:
    image: localstack/localstack:3.0
    ports:
      - "4566:4566"
    environment:
      - SERVICES=lambda,s3,dynamodb,sqs,stepfunctions,iam,ecr
      - DEBUG=1
      - LAMBDA_EXECUTOR=docker-reuse
      - DOCKER_HOST=unix:///var/run/docker.sock
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - ./localstack-init:/etc/localstack/init
```

## CI/CD Integration

### GitHub Actions Workflow
```yaml
name: Test Embedding Pipeline
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      
      - name: Start LocalStack
        run: docker-compose -f docker-compose.test.yml up -d
      
      - name: Run Unit Tests
        run: pytest tests/unit -v --cov
      
      - name: Run Integration Tests
        run: pytest tests/integration -v
      
      - name: Run Infrastructure Tests
        env:
          PULUMI_ACCESS_TOKEN: ${{ secrets.PULUMI_ACCESS_TOKEN }}
        run: |
          pulumi login
          pytest tests/infrastructure -v
```

## Performance Benchmarks

### Expected Test Execution Times
- Unit tests: < 10 seconds
- Integration tests (LocalStack): 1-2 minutes
- Infrastructure tests: 2-3 minutes
- End-to-end tests: 5-10 minutes

### Coverage Goals
- Unit test coverage: > 80%
- Integration test coverage: > 60%
- Critical path coverage: 100%

## Mock Data Strategy

### Test Fixtures
```python
# tests/fixtures/data.py
SAMPLE_RECEIPT_LINE = {
    "receipt_id": "test-receipt-001",
    "line_number": 1,
    "text": "Sample product description",
    "embedding_status": "PENDING"
}

SAMPLE_BATCH_RESPONSE = {
    "id": "batch_test_123",
    "status": "completed",
    "output_file_id": "file-test-456"
}
```

## Monitoring Test Health

### Key Metrics
1. Test execution time trends
2. Flaky test detection
3. Coverage regression alerts
4. LocalStack resource usage

### Test Reports
- Generate HTML coverage reports
- Track test execution history
- Monitor test performance over time