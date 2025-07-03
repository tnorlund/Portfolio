# CI/CD Pipeline Optimization Plan

## Current Issues
1. **High Claude API costs** from PR reviews on every commit
2. **Black formatter failing** on main branch
3. **No pre-commit hooks** to catch issues before CI
4. **Limited parallelization** of checks
5. **Missing linting tools** (only Black is run, not mypy/pylint/isort)

## Proposed Solutions

### 1. Fix Black Formatting Issues Immediately
```bash
# Run Black to auto-format all Python files
cd receipt_dynamo && black .
cd ../receipt_label && black .
cd ../infra && black .
```

### 2. Add Pre-commit Hooks
Create `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 24.3.0
    hooks:
      - id: black
        language_version: python3.12
  - repo: https://github.com/pycqa/isort
    rev: 5.13.2
    hooks:
      - id: isort
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.9.0
    hooks:
      - id: mypy
        additional_dependencies: [types-all]
  - repo: https://github.com/pycqa/pylint
    rev: v3.1.0
    hooks:
      - id: pylint
```

### 3. Optimize Claude PR Reviews
Modify `.github/workflows/claude-code-review.yml`:
```yaml
name: Claude Code Review
on:
  pull_request:
    types: [ready_for_review]  # Only on ready PRs, not drafts
    paths:
      - '**.py'
      - '**.ts'
      - '**.tsx'
      # Ignore docs, configs, etc.
      - '!**.md'
      - '!**.json'
      - '!**.yaml'
      - '!**.yml'

jobs:
  review:
    if: |
      github.event.pull_request.draft == false &&
      !contains(github.event.pull_request.labels.*.name, 'skip-claude-review')
    runs-on: ubuntu-latest
    steps:
      # Add size check
      - name: Check PR size
        uses: actions/github-script@v7
        with:
          script: |
            const pr = context.payload.pull_request;
            if (pr.additions + pr.deletions > 1000) {
              core.setFailed('PR too large for automated review (>1000 lines)');
            }

      - uses: actions/checkout@v4
      - uses: anthropics/claude-code-action@beta
        with:
          # ... existing config
```

### 4. Create Staged CI Pipeline
Split workflow into stages that fail fast:

#### Stage 1: Format & Lint (Fast checks)
- Black formatting
- isort imports
- ESLint/TypeScript
- File size checks

#### Stage 2: Unit Tests (Medium speed)
- Python unit tests (parallel)
- TypeScript/Jest tests

#### Stage 3: Integration Tests (Slower)
- Python integration tests
- Documentation generation

#### Stage 4: Deploy (Only on main)
- Pulumi preview on PRs
- Pulumi up on main merge

### 5. Add Manual Claude Review Trigger
Allow developers to request Claude review only when needed:
```yaml
name: Manual Claude Review
on:
  workflow_dispatch:
  issue_comment:
    types: [created]

jobs:
  review:
    if: |
      github.event_name == 'workflow_dispatch' ||
      (github.event.issue.pull_request &&
       contains(github.event.comment.body, '/claude review'))
    # ... rest of workflow
```

### 6. Local Development Scripts
Create `scripts/pre-push.sh`:
```bash
#!/bin/bash
# Run before pushing to catch issues early

echo "Running pre-push checks..."

# Format check
black --check receipt_dynamo receipt_label infra || {
  echo "❌ Black formatting failed. Run: make format"
  exit 1
}

# Type check
mypy receipt_dynamo receipt_label || {
  echo "❌ Type checking failed"
  exit 1
}

# Fast tests only
pytest -m "not integration and not end_to_end" --fail-fast || {
  echo "❌ Tests failed"
  exit 1
}

echo "✅ All checks passed!"
```

### 7. Makefile for Common Tasks
Create `Makefile`:
```makefile
.PHONY: format lint test test-fast

format:
	black receipt_dynamo receipt_label infra
	isort receipt_dynamo receipt_label infra

lint: format
	mypy receipt_dynamo receipt_label
	pylint receipt_dynamo receipt_label

test-fast:
	pytest -m "not integration and not end_to_end" --fail-fast

test:
	pytest -m "not end_to_end"

pre-push: format lint test-fast

install-hooks:
	pre-commit install
	cp scripts/pre-push.sh .git/hooks/pre-push
	chmod +x .git/hooks/pre-push
```

### 8. PR Template with Checklist
Create `.github/pull_request_template.md`:
```markdown
## Description
<!-- Describe your changes -->

## Checklist
- [ ] Ran `make format` locally
- [ ] Ran `make test-fast` locally
- [ ] PR is <500 lines (or added `skip-claude-review` label)
- [ ] Only request Claude review if needed with `/claude review`

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update
```

## Implementation Order

1. **Immediate**: Fix Black formatting on main
2. **Day 1**: Add pre-commit hooks and Makefile
3. **Day 2**: Update Claude review workflow to be more selective
4. **Day 3**: Implement staged CI pipeline
5. **Week 2**: Add manual review triggers and monitoring

## Expected Cost Savings

- **80% reduction** in Claude API calls by:
  - Only reviewing non-draft PRs
  - Skipping large PRs (>1000 lines)
  - Adding skip label option
  - Manual trigger option

- **50% faster CI** by:
  - Failing fast on format/lint
  - Parallel test execution
  - Caching dependencies

- **90% fewer CI failures** by:
  - Pre-commit hooks
  - Local pre-push checks
  - Developer tooling (Makefile)
