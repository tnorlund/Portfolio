# AI Usage Tracking Integration Tests

This directory contains comprehensive integration tests for the AI Usage Tracking system implemented in Workstream 4 (WS4).

## Overview

The integration tests validate the complete flow of AI usage tracking across all components:

- **ClientManager** → **AIUsageTracker** → **CostCalculator** → **AIUsageMetric** → **DynamoDB**
- **API Request** → **Lambda Handler** → **DynamoDB Query** → **Response Aggregation**
- **Batch Processing** → **Report Generation** → **Cost Analysis**

## Test Files

### 1. `test_client_manager_integration.py`
Tests the complete ClientManager integration with AI usage tracking:

- **ClientManager initialization** with tracking enabled/disabled
- **OpenAI client wrapping** with automatic usage tracking
- **Complete tracking flow**: API call → track → calculate cost → store metric
- **OpenAI completion and embedding** API tracking
- **Concurrent API usage** tracking with proper isolation
- **Error handling** and metric storage during failures
- **Batch operations** with correct pricing
- **Context propagation** (job_id, batch_id) across calls
- **File logging** when enabled
- **DynamoDB querying** by service and date range

**Key Features Tested:**
- Lazy initialization of clients
- Automatic cost calculation
- DynamoDB serialization/deserialization
- Context tracking across requests
- Concurrent request handling
- Error recovery

### 2. `test_ai_usage_integration.py`
Tests complete system integration across multiple services:

- **Multi-service workflows** (OpenAI + Anthropic + Google Places)
- **Batch processing** with report generation
- **Error recovery** and retry logic
- **Concurrent batch processing** with proper isolation
- **Cost threshold monitoring** and alerting
- **Data consistency** across component failures
- **Asynchronous batch processing** for high throughput

**Advanced Scenarios:**
- Job tracking across multiple AI services
- Batch cost reporting and analysis
- Intermittent failure handling
- Cost threshold alerts
- Data consistency during failures
- High-throughput async processing

### 3. `test_ai_usage_performance_integration.py`
Performance and stress testing for the AI usage tracking system:

- **High throughput** tracking (1000+ requests/second)
- **Concurrent load handling** with multiple threads
- **Memory efficiency** under sustained load
- **Burst traffic** handling
- **Query performance** with large datasets
- **Graceful degradation** under stress

**Performance Metrics:**
- Throughput: >100 req/s under normal load
- Latency: <10ms average DynamoDB write latency
- Memory: <100MB increase under 10,000 operations
- Burst handling: >50 req/s during traffic spikes
- Query performance: <500ms for month-long queries

### 4. `infra/routes/ai_usage/handler/test_handler.py`
Integration tests for the AI Usage API Lambda handler:

- **Lambda handler** with various query parameters
- **Service filtering** (openai, anthropic, google_places)
- **Operation filtering** (completion, embedding, etc.)
- **Multiple aggregation levels** (day, service, model, operation, hour)
- **DynamoDB pagination** handling
- **Error handling** and response formatting
- **Date range queries** with realistic data
- **Concurrent request** processing
- **Large dataset** aggregation performance
- **Decimal serialization** for JSON responses

**API Features Tested:**
- Query parameter parsing
- Multi-service aggregation
- Pagination handling
- Performance under load
- Error responses
- JSON serialization

## Test Utilities

### `utils/ai_usage_helpers.py`
Shared utilities for creating test data and assertions:

- `create_mock_usage_metric()`: Factory for AIUsageMetric test objects
- `create_mock_openai_response()`: Mock OpenAI API responses
- `create_mock_anthropic_response()`: Mock Anthropic API responses  
- `create_mock_google_places_response()`: Mock Google Places responses
- `assert_usage_metric_equal()`: Compare metrics in tests
- `create_test_tracking_context()`: Create test tracking contexts

## Running Tests

### Prerequisites
```bash
# Install dependencies
pip install pytest pytest-asyncio pytest-mock moto boto3 openai

# Set up environment variables
export DYNAMO_TABLE_NAME="test-table"
export OPENAI_API_KEY="test-key"
export PINECONE_API_KEY="test-key"
export PINECONE_INDEX_NAME="test-index"
export PINECONE_HOST="test.pinecone.io"
```

### Run Integration Tests
```bash
# Run all integration tests
pytest receipt_label/tests/ -m integration

# Run specific test categories
pytest receipt_label/tests/test_client_manager_integration.py -v
pytest receipt_label/tests/test_ai_usage_integration.py -v
pytest receipt_label/tests/test_ai_usage_performance_integration.py -m performance -v
pytest infra/routes/ai_usage/handler/test_handler.py -v

# Run with coverage
pytest receipt_label/tests/ -m integration --cov=receipt_label.utils --cov-report=html
```

### Performance Tests
```bash
# Run performance tests specifically
pytest receipt_label/tests/test_ai_usage_performance_integration.py -m performance -v --tb=short

# Run stress tests
pytest receipt_label/tests/test_ai_usage_performance_integration.py -k "stress" -v
```

## Test Patterns

### 1. Mock Usage
All external dependencies are mocked:
- **DynamoDB**: Using `moto` with realistic table structure
- **OpenAI API**: Mock responses with proper token/cost data
- **Anthropic API**: Mock responses for Claude models
- **Google Places API**: Mock place lookup responses

### 2. Integration Philosophy
Tests focus on **component interactions** rather than individual units:
- Test data flow between components
- Verify state consistency across operations
- Validate error propagation and recovery
- Measure performance under realistic loads

### 3. Test Data
Realistic test data that matches production patterns:
- Actual AI model names and pricing
- Realistic token counts and costs
- Proper timestamp and context data
- Real-world usage patterns

## Coverage Goals

The integration tests aim for:
- **95%+ integration coverage** across all AI usage tracking components
- **All critical paths** tested with realistic scenarios
- **Error conditions** and edge cases covered
- **Performance baselines** established and verified

## Continuous Integration

These tests are designed to run in CI/CD pipelines:
- **Fast execution**: Most tests complete in <5 seconds
- **Isolated**: No external service dependencies
- **Deterministic**: Consistent results across environments
- **Comprehensive**: Cover both happy path and failure scenarios

## Key Integration Flows Validated

1. **Complete Tracking Flow**:
   ```
   API Call → AIUsageTracker → CostCalculator → AIUsageMetric → DynamoDB
   ```

2. **Query and Aggregation Flow**:
   ```
   API Request → Lambda Handler → DynamoDB Query → Aggregation → JSON Response
   ```

3. **Batch Processing Flow**:
   ```
   Batch Jobs → Concurrent Tracking → Cost Analysis → Report Generation
   ```

4. **Error Recovery Flow**:
   ```
   API Failures → Error Tracking → Retry Logic → Partial Recovery
   ```

The integration tests ensure that the AI Usage Tracking system works correctly as a complete system, not just as individual components.