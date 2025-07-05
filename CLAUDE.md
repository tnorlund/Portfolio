# Claude Assistant Guide - Receipt Dynamo

## Current State (Jan 2025)

### Active Work
- **Issue #167**: Phase 2 Entity Migration (Batches 3-4 remaining)
- **Issue #171**: Addressing diminishing returns in refactoring
- **Pylint Score**: 7.67/10 (target: 10/10)

### Recent Completions
- ✅ Batch 1: Core entities (25.5% reduction)
- ✅ Batch 2: Receipt entities (16.4% reduction)

## Repository Structure

```
receipt_dynamo/
├── data/                    # DynamoDB entity classes
│   ├── base_operations.py   # Base classes for refactoring
│   ├── _*.py               # Entity implementations
│   └── resilient_*.py      # Resilience patterns
├── entities/               # Data models
└── tests/
    ├── unit/              # Fast unit tests
    └── integration/       # DynamoDB integration tests
```

## Code Patterns

### Entity Refactoring Pattern
```python
class _EntityName(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    @handle_dynamodb_errors("operation_name")
    def operation(self, param):
        self._validate_entity(param, EntityClass, "param")
        # operation logic
```

### Known Issues
1. **Type Conflicts**: 26 MyPy errors with `List[Dict[Any, Any]]` vs `List[WriteRequestTypeDef]`
2. **Complex Queries**: GSI queries resist abstraction (see `_receipt_metadata.py`)
3. **Diminishing Returns**: Recent batches showing <20% code reduction

## Common Commands

```bash
# Run specific test
pytest tests/integration/test__receipt_word.py::test_name -xvs

# Check pylint score
cd receipt_dynamo && pylint receipt_dynamo

# Run integration tests for a group
python -m pytest tests/integration -k "group-3"

# Fix formatting
black . && isort .

# Type check
mypy receipt_dynamo
```

## Decision Guidelines

### When to Refactor
- Code reduction potential > 10%
- Clear duplication patterns
- Maintains type safety

### When to Stop
- Code reduction < 10%
- Increases complexity
- Breaks type safety

## Current Priorities

1. **Fix MyPy Errors** - Type safety affecting code quality
2. **Evaluate Batch 3** - May have < 10% reduction potential
3. **Query Builder Pattern** - For complex GSI operations
4. **Document Patterns** - Ensure consistency

## Testing Strategy

- Always run tests before pushing
- Integration tests grouped for parallel execution
- Use `--no-verify` only for pre-existing MyPy errors
- CI runs full test matrix

## Contact & Context

- Original refactoring spec: `/spec/phase2-entity-refactoring.md`
- Retrospective: Issue #171
- PR Template: Follow format from PR #168/170