# Stream Processor Tests

Comprehensive test suite for the DynamoDB stream processor Lambda function.

## Structure

```
tests/
├── fixtures/
│   ├── stream_events.py           # Sample DynamoDB stream events
│   └── expected_messages.py       # Expected SQS message schemas
├── unit/
│   ├── test_parsing.py            # Entity parsing tests
│   ├── test_change_detection.py   # Change detection logic
│   └── test_routing_logic.py      # Collection targeting
├── integration/
│   └── test_stream_processor_integration.py  # Full Lambda tests with moto
├── contract/
│   └── test_message_schemas.py    # Downstream contract tests
├── conftest.py                    # Shared fixtures
└── README.md                      # This file
```

## Test Categories

### Unit Tests (`unit/`)

Fast tests with no external dependencies. Test individual functions and logic:

- **test_parsing.py**: DynamoDB stream record parsing, entity detection
- **test_change_detection.py**: ChromaDB-relevant field change detection
- **test_routing_logic.py**: Collection targeting (lines vs words)

### Integration Tests (`integration/`)

Tests with mocked AWS services (using moto):

- **test_stream_processor_integration.py**: Full Lambda handler with SQS routing
  - Tests RECEIPT_METADATA routing to both queues
  - Tests RECEIPT_WORD_LABEL routing to words only
  - Tests COMPACTION_RUN fast-path routing
  - Tests batch processing (>10 messages)
  - Tests message format and attributes

### Contract Tests (`contract/`)

Validate message schemas for downstream compatibility:

- **test_message_schemas.py**: Ensures messages match what compaction handler expects
  - Required fields validation
  - JSON serializability
  - Collection targeting rules
  - Timestamp format compliance

### Fixtures (`fixtures/`)

Reusable test data:

- **stream_events.py**: Sample DynamoDB stream events for all entity types
  - `TARGET_METADATA_UPDATE_EVENT`: Metadata change event
  - `WORD_LABEL_UPDATE_EVENT`: Word label update event
  - `WORD_LABEL_REMOVE_EVENT`: Word label deletion event
  - `COMPACTION_RUN_INSERT_EVENT`: Compaction run creation event
  - Factory functions for custom variations

- **expected_messages.py**: Expected message schemas for contract testing
  - Schema definitions
  - Required field lists
  - Collection targeting rules

## Running Tests

### Run all tests:
```bash
pytest infra/chromadb_compaction/tests/
```

### Run specific categories:
```bash
# Unit tests (fast)
pytest infra/chromadb_compaction/tests/unit/ -v

# Integration tests (with moto)
pytest infra/chromadb_compaction/tests/integration/ -v

# Contract tests
pytest infra/chromadb_compaction/tests/contract/ -v
```

### Run specific test files:
```bash
pytest infra/chromadb_compaction/tests/unit/test_parsing.py -v
pytest infra/chromadb_compaction/tests/integration/test_stream_processor_integration.py::TestMetadataRoutingSQS -v
```

### With coverage:
```bash
pytest infra/chromadb_compaction/tests/ --cov=infra.chromadb_compaction.lambdas.stream_processor --cov-report=html
```

## Key Testing Principles

1. **No Mocking of receipt_dynamo**: Entity parsers are already tested, use them directly
2. **Mock AWS Only**: Use moto for SQS/DynamoDB, keep business logic real
3. **Test Message Format**: Ensure compatibility with downstream consumers
4. **Collection Targeting**: Verify correct routing (lines vs words)
5. **Batch Handling**: Test SQS batch limits (>10 messages)

## Adding New Tests

### For new entity types:

1. Add fixtures to `fixtures/stream_events.py`
2. Add routing tests to `unit/test_routing_logic.py`
3. Add integration tests to `integration/test_stream_processor_integration.py`
4. Add schema validation to `contract/test_message_schemas.py`

### For new change detection:

1. Add test cases to `unit/test_change_detection.py`
2. Verify field is in `get_chromadb_relevant_changes()` logic

### For new message attributes:

1. Update `fixtures/expected_messages.py` schema
2. Add validation to `contract/test_message_schemas.py`
3. Test in integration tests with moto

## Fixtures Available

From `conftest.py`:

- `target_metadata_event`: Sample metadata update event
- `target_event_factory`: Factory for metadata event variations
- `word_label_update_event`: Sample word label update
- `word_label_remove_event`: Sample word label deletion
- `word_label_event_factory`: Factory for word label variations
- `compaction_run_insert_event`: Sample compaction run creation
- `compaction_run_event_factory`: Factory for compaction run variations
- `mock_sqs_queues`: Mocked SQS queues (lines and words)

## Dependencies

- **pytest**: Test framework
- **moto**: AWS service mocking (SQS, DynamoDB)
- **receipt_dynamo**: Entity classes and parsers
- **boto3**: AWS SDK (mocked by moto in tests)

## Coverage Goals

- Unit tests: 100% coverage of business logic
- Integration tests: All routing paths covered
- Contract tests: All message schemas validated
- Edge cases: Error handling, malformed input, missing queues

