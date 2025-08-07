# Testing Guide

## Overview

This project uses comprehensive testing strategies across both frontend and backend components.

## Python Testing (Backend)

### Test Structure
```
receipt_*/
├── tests/
│   ├── unit/           # Fast, isolated unit tests
│   ├── integration/    # Tests with real dependencies
│   └── end_to_end/     # Full system tests (uses AWS)
```

### Running Tests

#### Quick Testing
```bash
# Run unit tests for a specific package
pytest receipt_dynamo/tests/unit -v

# Run with coverage
pytest receipt_label/tests --cov=receipt_label

# Run specific test file
pytest tests/unit/test_specific.py
```

#### Parallel Testing
```bash
# Use all CPU cores
pytest -n auto

# Run integration tests in parallel groups
./scripts/test_runner.sh receipt_dynamo
```

#### Test Markers
```bash
# Skip slow tests
pytest -m "not slow"

# Run only integration tests
pytest -m "integration"

# Skip end-to-end tests (they use real AWS)
pytest -m "not end_to_end"
```

### Writing Tests

#### Unit Test Example
```python
def test_receipt_creation():
    receipt = Receipt(merchant="Store", total=10.99)
    assert receipt.merchant == "Store"
    assert receipt.total == 10.99
```

#### Integration Test Example
```python
@pytest.mark.integration
def test_dynamo_write(dynamo_table):
    item = {"pk": "TEST#1", "sk": "RECEIPT#123"}
    dynamo_table.put_item(Item=item)
    response = dynamo_table.get_item(Key={"pk": "TEST#1", "sk": "RECEIPT#123"})
    assert response["Item"] == item
```

## JavaScript Testing (Frontend)

### Test Structure
```
portfolio/
├── __tests__/
│   ├── unit/           # Component unit tests
│   ├── integration/    # Page integration tests
│   └── e2e/           # End-to-end tests
```

### Running Tests

```bash
cd portfolio

# Run all tests
npm test

# Run with coverage
npm run test:coverage

# Run in watch mode
npm run test:watch

# Run E2E tests
npm run test:e2e
```

### Writing Tests

#### Component Test Example
```jsx
import { render, screen } from '@testing-library/react';
import Button from '@/components/Button';

test('renders button with text', () => {
  render(<Button>Click me</Button>);
  expect(screen.getByText('Click me')).toBeInTheDocument();
});
```

#### E2E Test Example
```javascript
test('homepage loads correctly', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle(/Portfolio/);
  await expect(page.locator('h1')).toContainText('Tyler Norlund');
});
```

## CI/CD Testing

Tests run automatically on:
- Pull request creation
- Commits to main branch
- Nightly scheduled runs

### GitHub Actions Workflow
- **Quick Tests**: Format checks and unit tests (2-3 minutes)
- **Full Test Suite**: All tests in parallel groups (15-20 minutes)
- **E2E Tests**: Production smoke tests (5 minutes)

## Best Practices

### Performance
- Use `pytest-xdist` for parallel execution
- Skip expensive tests during development
- Mock external services in unit tests
- Use fixtures for test data

### Reliability
- Isolate tests from each other
- Clean up test data after runs
- Use deterministic test data
- Avoid time-dependent assertions

### Coverage Goals
- Unit tests: 80% coverage minimum
- Integration tests: Critical paths covered
- E2E tests: Happy path scenarios

## Debugging Tests

### Python
```bash
# Run with verbose output
pytest -vv

# Show print statements
pytest -s

# Debug with pdb
pytest --pdb

# Run specific test by name
pytest -k "test_receipt_creation"
```

### JavaScript
```bash
# Debug in Chrome
npm run test:debug

# Run specific test file
npm test Button.test.tsx

# Update snapshots
npm test -- -u
```

## Test Configuration

### Python (`pytest.ini`)
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -ra -q --strict-markers
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    end_to_end: marks tests that use real AWS services
```

### JavaScript (`jest.config.js`)
```javascript
module.exports = {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/$1',
  },
  collectCoverageFrom: [
    'components/**/*.{js,jsx,ts,tsx}',
    'pages/**/*.{js,jsx,ts,tsx}',
  ],
};
```