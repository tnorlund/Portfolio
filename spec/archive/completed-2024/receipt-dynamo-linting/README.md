# Receipt Dynamo Linting Strategy

## Overview

This document outlines a comprehensive strategy to lint and format the receipt_dynamo package. The package contains 102 source files and 89 test files (191 total Python files), making it a substantial codebase that requires a systematic approach.

## Current State Analysis

### Package Structure
- **Source files**: 102 files in `receipt_dynamo/`
- **Test files**: 89 files in `tests/`
- **Total**: 191 Python files

### Current Issues Found
1. **Black formatting**: 1 file needs reformatting (`ai_usage_metric.py`)
2. **Pylint errors**: 1 critical error in `_job.py` (method name issue)
3. **Mypy**: Type checking analysis pending

### Package Organization
```
receipt_dynamo/
├── receipt_dynamo/
│   ├── data/          # 50+ data access layer files
│   ├── entities/      # 47+ entity/model files
│   └── services/      # 4 service layer files
└── tests/
    ├── unit/          # ~40 unit test files
    ├── integration/   # ~40 integration test files
    └── end_to_end/    # ~5 e2e test files
```

## Parallel Strategy

### Phase 1: Analysis & Planning (Parallel Tasks)
**Estimated Time**: 30 minutes

| Task | Description | Output |
|------|-------------|---------|
| `lint-analysis` | Run comprehensive analysis of all linting tools | `analysis-report.md` |
| `file-categorization` | Categorize files by complexity and issues | `file-categories.json` |
| `dependency-mapping` | Map file dependencies for safe parallel processing | `dependency-map.json` |

### Phase 2: Quick Wins (Parallel Tasks)
**Estimated Time**: 15 minutes

| Task | Description | Files |
|------|-------------|-------|
| `format-entities` | Run black on all entity files | `entities/*.py` |
| `format-services` | Run black on service files | `services/*.py` |
| `format-tests-unit` | Run black on unit tests | `tests/unit/*.py` |
| `format-tests-integration` | Run black on integration tests | `tests/integration/*.py` |

### Phase 3: Complex Fixes (Sequential with Parallel Sub-tasks)
**Estimated Time**: 45 minutes

| Task | Description | Dependencies |
|------|-------------|--------------|
| `fix-data-layer` | Fix data access layer issues | Must be sequential due to inheritance |
| `fix-type-annotations` | Add/fix type hints | After data layer |
| `fix-pylint-errors` | Resolve pylint issues | After formatting |

### Phase 4: Validation (Parallel Tasks)
**Estimated Time**: 20 minutes

| Task | Description | Scope |
|------|-------------|-------|
| `validate-formatting` | Verify black compliance | All files |
| `validate-types` | Run mypy validation | Source files only |
| `validate-lint` | Run pylint validation | All files |
| `run-tests` | Ensure no regressions | Test suite |

## Implementation Plan

### Tools Configuration
- **Black**: Line length 79 (existing config)
- **Pylint**: Existing config in pyproject.toml
- **Mypy**: Existing config with boto3 stubs
- **Isort**: Black profile

### Parallel Execution Strategy

1. **File Groups for Parallel Processing**:
   ```json
   {
     "group_1": "entities/*.py",
     "group_2": "data/_*.py (excluding inheritance chains)",
     "group_3": "services/*.py",
     "group_4": "tests/unit/*.py",
     "group_5": "tests/integration/*.py"
   }
   ```

2. **Dependency-Safe Ordering**:
   - Entities first (no dependencies)
   - Services second (depend on entities)
   - Data layer carefully ordered by inheritance
   - Tests last (depend on all above)

### Scripts for Parallel Execution

Each task will have its own script in `spec/receipt-dynamo-linting/scripts/`:
- `analyze.py` - Analysis tasks
- `format_group.py` - Formatting by file group
- `fix_issues.py` - Issue resolution
- `validate.py` - Final validation

## Success Criteria

1. **Zero formatting violations**: All files pass `black --check`
2. **Zero critical pylint errors**: No E-level or F-level issues
3. **Type safety**: Clean mypy run with boto3 stubs
4. **Test compatibility**: All tests still pass
5. **Documentation**: Updated linting guidelines

## Monitoring & Progress Tracking

- Each parallel task generates a progress report
- Central coordinator tracks completion
- Failed tasks are retried with more aggressive fixing
- Final report summarizes all changes made

## Estimated Timeline

- **Total time**: ~2 hours
- **Parallel efficiency**: ~65% time savings vs sequential
- **Final result**: Production-ready, fully linted codebase

Next: Execute Phase 1 analysis tasks
