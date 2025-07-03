# AI Usage Testing - Workflow Guide

## Quick Reference

### Worktree Locations
```bash
/Users/tnorlund/GitHub/example-service-api-exploration  # Base branch
/Users/tnorlund/GitHub/example-test-core-utils          # Workstream 1
/Users/tnorlund/GitHub/example-test-entity              # Workstream 2
/Users/tnorlund/GitHub/example-test-tracker             # Workstream 3
/Users/tnorlund/GitHub/example-test-integration         # Workstream 4
/Users/tnorlund/GitHub/example-test-e2e                 # Workstream 5
```

### Daily Workflow

#### 1. Start Work on a Workstream
```bash
cd /Users/tnorlund/GitHub/example-test-core-utils
python -m venv .venv
source .venv/bin/activate
pip install -e ../example/receipt_label
pip install -e ../example/receipt_dynamo
```

#### 2. Sync with Base Branch Daily
```bash
# In your workstream directory
git fetch origin
git rebase origin/service-api-exploration
```

#### 3. Run Tests
```bash
# Unit tests only
pytest -m unit

# Your workstream tests
pytest receipt_label/tests/test_cost_calculator.py -v
```

#### 4. Push Changes
```bash
git add .
git commit -m "test: add cost calculator unit tests"
git push -u origin test/ai-usage-core-utils
```

## Parallel Development Guide

### Independent Workstreams (Start Now)

#### Person A: Workstream 1 (Core Utils)
```bash
cd /Users/tnorlund/GitHub/example-test-core-utils
# Create: receipt_label/tests/test_cost_calculator.py
# No dependencies - can start immediately
```

#### Person B: Workstream 2 (Entity)
```bash
cd /Users/tnorlund/GitHub/example-test-entity
# Create: receipt_dynamo/tests/test_ai_usage_metric.py
# No dependencies - can start immediately
```

### Dependent Workstreams (Start After Dependencies)

#### Workstream 3 (Tracker) - Depends on WS1
```bash
cd /Users/tnorlund/GitHub/example-test-tracker
# Wait for: test_cost_calculator.py from WS1
# Create: receipt_label/tests/test_ai_usage_tracker.py
```

#### Workstream 4 (Integration) - Depends on WS1, WS2, WS3
```bash
cd /Users/tnorlund/GitHub/example-test-integration
# Wait for: All unit tests complete
# Create: receipt_label/tests/test_client_manager_integration.py
```

## Merging Process

### 1. Create PR from Workstream
```bash
# From workstream directory
gh pr create --base service-api-exploration \
  --title "test: add core utils tests for AI usage tracking" \
  --body "Implements unit tests for CostCalculator (WS1)"
```

### 2. Merge to Base Branch
```bash
# After PR approval
cd /Users/tnorlund/GitHub/example-service-api-exploration
git pull origin service-api-exploration
```

### 3. Update Other Workstreams
```bash
# In each active workstream
git fetch origin
git rebase origin/service-api-exploration
```

## Test Organization

### File Naming Convention
- Unit tests: `test_<module_name>.py`
- Integration tests: `test_<feature>_integration.py`
- E2E tests: `end_to_end/test_<feature>_e2e.py`

### Test Structure Example
```python
import pytest
from receipt_label.tests.utils import create_mock_usage_metric

@pytest.mark.unit
class TestCostCalculator:
    def test_openai_gpt35_cost_calculation(self):
        # Test implementation
        pass
```

## Status Tracking

Use this checklist to track progress:

- [ ] **WS1**: Core Utils Tests
  - [ ] test_cost_calculator.py
  - [ ] Pricing data fixtures

- [ ] **WS2**: Entity Tests
  - [ ] test_ai_usage_metric.py
  - [ ] test_ai_usage_queries.py

- [ ] **WS3**: Tracker Tests
  - [ ] test_ai_usage_tracker.py
  - [ ] test_openai_wrapper.py

- [ ] **WS4**: Integration Tests
  - [ ] test_client_manager_integration.py
  - [ ] test_handler.py (Lambda)

- [ ] **WS5**: E2E Tests
  - [ ] test_ai_usage_e2e.py
  - [ ] Documentation for running
