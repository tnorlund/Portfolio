# AI Usage Tracking Testing Project

## Project Overview
This document tracks the progress of implementing comprehensive tests for the AI Usage Tracking system. The project uses a parallel development approach with git worktrees to enable multiple developers to work simultaneously.

## Project Status

### Overall Progress: 🟡 In Progress (10% Complete)

| Workstream | Branch | Status | Progress | Owner |
|------------|--------|--------|----------|--------|
| WS1: Core Utils | `test/ai-usage-core-utils` | 🟡 Started | 5/20 tests | TBD |
| WS2: Entity & Data | `test/ai-usage-entity` | 🟡 Started | 2/25 tests | TBD |
| WS3: Tracker Components | `test/ai-usage-tracker` | ⚪ Not Started | 0/25 tests | TBD |
| WS4: Integration | `test/ai-usage-integration` | ⚪ Not Started | 0/18 tests | TBD |
| WS5: End-to-End | `test/ai-usage-e2e` | ⚪ Not Started | 0/8 tests | TBD |

## Components Being Tested

### 1. Cost Calculator (`receipt_label/utils/cost_calculator.py`)
- **Purpose**: Calculate costs for AI API usage
- **Test Coverage**: 🟡 In Progress
- **Location**: `receipt_label/tests/test_cost_calculator.py`

### 2. AI Usage Metric Entity (`receipt_dynamo/entities/ai_usage_metric.py`)
- **Purpose**: DynamoDB entity for storing usage metrics
- **Test Coverage**: 🟡 In Progress
- **Location**: `receipt_dynamo/tests/test_ai_usage_metric.py`

### 3. AI Usage Tracker (`receipt_label/utils/ai_usage_tracker.py`)
- **Purpose**: Track and aggregate AI API usage
- **Test Coverage**: ⚪ Not Started
- **Location**: `receipt_label/tests/test_ai_usage_tracker.py`

### 4. Client Manager (`receipt_label/utils/client_manager.py`)
- **Purpose**: Manage AI clients with automatic usage tracking
- **Test Coverage**: ⚪ Not Started
- **Location**: `receipt_label/tests/test_client_manager_integration.py`

### 5. API Endpoint (`infra/routes/ai_usage/`)
- **Purpose**: REST API for querying usage metrics
- **Test Coverage**: ⚪ Not Started
- **Location**: `infra/routes/ai_usage/handler/test_handler.py`

## Git Worktree Structure

```
/Users/tnorlund/GitHub/
├── example/                                    # Main repository
├── example-service-api-exploration/            # Base feature branch
├── example-test-core-utils/                    # WS1: Core utilities testing
├── example-test-entity/                        # WS2: Entity & data layer testing
├── example-test-tracker/                       # WS3: Tracker components testing
├── example-test-integration/                   # WS4: Integration testing
└── example-test-e2e/                          # WS5: End-to-end testing
```

## Detailed Progress Tracking

### Workstream 1: Core Utils Testing
**Branch**: `test/ai-usage-core-utils`  
**Dependencies**: None  
**Can Start**: ✅ Immediately

#### Tasks:
- [x] Create test file structure
- [x] Test OpenAI GPT-3.5 cost calculation
- [x] Test OpenAI GPT-4 cost calculation
- [x] Test OpenAI embedding cost calculation
- [x] Test batch API discount (50% off)
- [ ] Test all OpenAI models (GPT-4o, GPT-4o-mini, etc.)
- [ ] Test Anthropic Claude Opus costs
- [ ] Test Anthropic Claude Sonnet costs
- [ ] Test Anthropic Claude Haiku costs
- [ ] Test Google Places API costs
- [ ] Test error handling for unknown services
- [ ] Test error handling for unknown models
- [ ] Test edge cases (zero tokens, negative tokens)
- [ ] Test pricing data retrieval methods
- [ ] Test model listing functionality
- [ ] Add fixture for pricing test data
- [ ] Add performance tests for bulk calculations
- [ ] Document any pricing changes needed
- [ ] Add tests for future model additions
- [ ] Create integration test with real calculations

### Workstream 2: Entity & Data Layer Testing
**Branch**: `test/ai-usage-entity`  
**Dependencies**: None  
**Can Start**: ✅ Immediately

#### Tasks:
- [x] Create test file structure
- [x] Test entity creation and validation
- [ ] Test DynamoDB save operation
- [ ] Test retrieve by ID
- [ ] Test query by date range
- [ ] Test query by service
- [ ] Test query by model
- [ ] Test GSI1 queries (service + date)
- [ ] Test GSI2 queries (date + cost)
- [ ] Test GSI3 queries (job_id)
- [ ] Test aggregate costs by date
- [ ] Test aggregate costs by service
- [ ] Test aggregate costs by model
- [ ] Test aggregate by operation type
- [ ] Test metadata serialization
- [ ] Test batch operations
- [ ] Test pagination
- [ ] Test error handling
- [ ] Test data migration scenarios
- [ ] Test index efficiency
- [ ] Performance tests for large datasets
- [ ] Test TTL functionality
- [ ] Test backup/restore scenarios
- [ ] Document query patterns
- [ ] Create data access patterns guide

### Workstream 3: Tracker Components Testing
**Branch**: `test/ai-usage-tracker`  
**Dependencies**: Workstream 1  
**Can Start**: 🔴 After WS1 completes

#### Tasks:
- [ ] Create test file structure
- [ ] Test tracker initialization
- [ ] Test OpenAI client wrapping
- [ ] Test Anthropic client wrapping
- [ ] Test context management (job_id, batch_id)
- [ ] Test usage aggregation
- [ ] Test file-based backend
- [ ] Test DynamoDB backend
- [ ] Test backend switching
- [ ] Test concurrent tracking
- [ ] Test error recovery
- [ ] Test metric batching
- [ ] Test memory efficiency
- [ ] Test decorator functionality
- [ ] Test async tracking
- [ ] Test tracking suspension/resume
- [ ] Test export functionality
- [ ] Test import functionality
- [ ] Test cleanup operations
- [ ] Test rate limiting
- [ ] Performance benchmarks
- [ ] Test integration with ClientManager
- [ ] Test custom metadata
- [ ] Document tracking patterns
- [ ] Create usage examples

### Workstream 4: Integration Testing
**Branch**: `test/ai-usage-integration`  
**Dependencies**: Workstreams 1, 2, 3  
**Can Start**: 🔴 After dependencies complete

#### Tasks:
- [ ] Create test file structure
- [ ] Test full tracking flow (call → track → save)
- [ ] Test ClientManager with tracking enabled
- [ ] Test ClientManager with tracking disabled
- [ ] Test API endpoint handler
- [ ] Test query parameter handling
- [ ] Test date range queries via API
- [ ] Test service filtering via API
- [ ] Test aggregation via API
- [ ] Test error responses
- [ ] Test authentication/authorization
- [ ] Test rate limiting
- [ ] Test caching behavior
- [ ] Test concurrent API calls
- [ ] Test data consistency
- [ ] Test transaction handling
- [ ] Test monitoring integration
- [ ] Test alerting thresholds

### Workstream 5: End-to-End Testing
**Branch**: `test/ai-usage-e2e`  
**Dependencies**: Workstream 4  
**Can Start**: 🔴 After WS4 completes

#### Tasks:
- [ ] Create test file structure
- [ ] Test with real OpenAI API (minimal)
- [ ] Test with real DynamoDB
- [ ] Test full pipeline (API call → track → store → query)
- [ ] Test report generation
- [ ] Test cost alerts
- [ ] Test multi-day aggregation
- [ ] Test system performance
- [ ] Document test costs

## Key Files and Locations

### Shared Resources (Base Branch)
- `docs/testing-strategy-ai-usage.md` - Testing strategy
- `docs/ai-testing-workflow.md` - Workflow guide
- `receipt_label/tests/utils/ai_usage_helpers.py` - Shared test utilities
- `spec/ai-usage-testing-project.md` - This tracking document

### Test Files by Workstream
- **WS1**: `receipt_label/tests/test_cost_calculator.py`
- **WS2**: `receipt_dynamo/tests/test_ai_usage_metric.py`
- **WS3**: `receipt_label/tests/test_ai_usage_tracker.py`
- **WS4**: `receipt_label/tests/test_client_manager_integration.py`
- **WS5**: `receipt_label/tests/end_to_end/test_ai_usage_e2e.py`

## Success Metrics

1. **Code Coverage**: Achieve 90%+ coverage for all AI usage tracking components
2. **Test Performance**: Full test suite (excluding e2e) runs in <30 seconds
3. **Documentation**: Complete test documentation and usage examples
4. **Reliability**: Zero flaky tests
5. **Maintainability**: Clear test structure following project patterns

## Timeline

| Week | Milestone | Workstreams |
|------|-----------|-------------|
| Week 1 | Unit tests complete | WS1 + WS2 complete |
| Week 2 | Tracker tests complete | WS3 complete |
| Week 3 | Integration tests complete | WS4 complete |
| Week 4 | E2E tests & merge to main | WS5 complete, final merge |

## Open Questions

1. Should we add performance benchmarks for cost calculations?
2. Do we need to test against multiple AWS regions for DynamoDB?
3. Should we create mock data generators for large-scale testing?
4. How should we handle testing of future API pricing changes?
5. Do we need to test the GitHub Actions integration?

## Notes

- All workstreams use pytest with markers: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.end_to_end`
- Mocking strategy: Use `moto` for AWS services, `pytest-mock` for API clients
- Each workstream should rebase from base branch daily to avoid conflicts
- Consider adding property-based tests for cost calculations

## Next Steps

1. Assign owners to each workstream
2. Set up virtual environments in each worktree
3. Complete WS1 and WS2 (independent workstreams)
4. Begin WS3 once WS1 is complete
5. Regular sync meetings to track progress

---

Last Updated: 2025-06-25
Project Lead: TBD
Review Schedule: Daily standup recommended