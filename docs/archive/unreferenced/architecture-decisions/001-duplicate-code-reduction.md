# ADR-001: Duplicate Code Reduction via Base Classes

## Status
Accepted and Implemented (Phase 1)

## Context
The receipt_dynamo package had 922 instances of duplicate/similar code (R0801), primarily in:
- Error handling patterns (every CRUD operation)
- Batch operations with retry logic
- Transaction handling
- Validation logic
- Query patterns

This duplication led to:
- Maintenance burden
- Inconsistent error handling
- Bugs fixed in one place but not others
- Difficult to add new features

## Decision
Implement a base class hierarchy with mixins to centralize common functionality:

1. **DynamoDBBaseOperations**: Core error handling and validation
2. **Mixins**: Specific operation patterns (CRUD, Batch, Transactional)
3. **Decorator**: Consistent error handling wrapper
4. **Phased rollout**: Start with proof of concept, then migrate all entities

## Consequences

### Positive
- **80.6% code reduction** achieved in proof of concept
- Centralized error handling and validation
- Consistent behavior across all operations
- Easier to maintain and debug
- Single place to fix bugs or add features

### Negative
- Learning curve for team
- Initial refactoring effort
- Need to maintain backward compatibility during migration

## Implementation Status
- ✅ Phase 1: Base infrastructure (COMPLETE)
- ⏳ Phase 2: Entity migration (PENDING)
- ⏳ Phase 3: Optimization (PENDING)

## Lessons Learned
- Start with comprehensive tests before refactoring
- Validate with proof of concept early
- Type hints are crucial for mixin design
- Bug bot caught critical validation bypass issue

## References
- Original strategy: `DUPLICATE_CODE_STRATEGY.md`
- PR #165: Phase 1 implementation
- Proof of concept: `_receipt_refactored.py`
