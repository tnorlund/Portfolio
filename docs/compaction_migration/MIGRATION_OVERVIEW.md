# ChromaDB Compaction Logic Migration Overview

**Status**: Planning
**Created**: December 15, 2024
**Last Updated**: December 15, 2024

## Executive Summary

This document outlines the migration of ChromaDB compaction business logic from AWS Lambda handlers into two separate packages: `receipt_dynamo_stream` (NEW) and `receipt_chroma` (ENHANCED). The goal is to extract reusable business logic while keeping AWS orchestration in Lambda handlers, with proper separation between lightweight stream processing and heavy ChromaDB operations.

## Objectives

### Primary Goals

1. **Extract Business Logic**: Move pure ChromaDB operations out of Lambda handlers into `receipt_chroma` package
2. **Improve Reusability**: Make compaction operations available outside Lambda context
3. **Enhance Testability**: Enable unit testing without AWS mocking
4. **Maintain Functionality**: Keep all existing functionality working during migration
5. **Safe Migration**: Use lift-and-shift approach (no refactoring during migration)

### Non-Goals

- Refactoring or optimization of existing code (done after migration)
- Changing lambda handler orchestration logic
- Modifying AWS infrastructure or deployment processes
- Performance improvements (separate effort)

## Architecture Evolution

### Current State: Monolithic Lambda Modules

```
Lambda Handlers
├── All business logic embedded
├── All stream processing embedded
├── All compaction logic embedded
└── Heavy dependencies bundled
```

### Target State: Three-Package Architecture

```
┌─────────────────────────────────────────────────┐
│ receipt_dynamo_stream (NEW)                     │
│ • Lightweight stream processing                 │
│ • DynamoDB stream parsing                       │
│ • Change detection                              │
│ • Zip Lambda compatible ✓                       │
└─────────────────────────────────────────────────┘
                    ↓
         Stream messages via SQS
                    ↓
┌─────────────────────────────────────────────────┐
│ receipt_chroma                                   │
│ • ChromaDB operations                           │
│ • Compaction logic                              │
│ • Heavy dependencies                            │
│ • Container Lambda required ⚠                   │
└─────────────────────────────────────────────────┘
                    ↓
         Uses entities from
                    ↓
┌─────────────────────────────────────────────────┐
│ receipt_dynamo (EXISTING)                       │
│ • Entity models                                 │
│ • DynamoDB operations                           │
│ • Shared across both packages                   │
└─────────────────────────────────────────────────┘
```

## Current Architecture

### Lambda Functions

#### 1. Stream Processor Lambda (`stream_processor.py`)

**Purpose**: Lightweight DynamoDB stream event processor

**Responsibilities**:
- Receives DynamoDB stream events (INSERT, MODIFY, REMOVE)
- Parses stream records using `receipt_dynamo` entities
- Detects ChromaDB-relevant field changes
- Builds SQS messages for compaction
- Publishes messages to compaction queues

**Modules Used**:
```
processor/
├── parsers.py           - DynamoDB stream parsing
├── change_detector.py   - Field change detection
├── message_builder.py   - SQS message construction
└── sqs_publisher.py     - SQS publishing
```

#### 2. Enhanced Compaction Handler Lambda (`enhanced_compaction_handler.py`)

**Purpose**: Heavy-lifting compaction processor

**Responsibilities**:
- Receives SQS messages from stream processor
- Downloads ChromaDB snapshots (S3 or EFS+S3)
- Applies metadata updates to snapshots
- Applies label updates to snapshots
- Merges compaction delta files
- Uploads updated snapshots to S3
- Uses distributed locks for concurrency

**Modules Used**:
```
compaction/
├── operations.py            - Update/remove ChromaDB operations
├── compaction_run.py        - Delta merging logic
├── efs_snapshot_manager.py  - EFS caching layer
├── metadata_handler.py      - Metadata update orchestration
├── label_handler.py         - Label update orchestration
└── message_builder.py       - Message categorization
```

## Migration Strategy

### Approach: Lift-and-Shift

We will use a **lift-and-shift** migration strategy:

1. **Copy files as-is** to `receipt_chroma` package
2. **Update imports** to point to new locations
3. **Keep orchestration** in Lambda handlers
4. **Test incrementally** after each move
5. **Refactor later** once code is safely moved

### Why Lift-and-Shift?

✅ **Lower Risk**: Minimal code changes reduce chance of bugs
✅ **Easier to Debug**: Can compare old vs new directly
✅ **Incremental Testing**: Test each module independently
✅ **Easy Rollback**: Original files remain until tested
✅ **Clear Progress**: Can track completion file-by-file

## Package Responsibilities After Migration

### receipt_dynamo

**Owns**:
- Entity models (ReceiptMetadata, ReceiptWordLabel)
- DynamoDB operations (DynamoClient)
- Compaction tracking (CompactionRun, CompactionLock)
- Data access patterns

**Does NOT Own**:
- ChromaDB operations
- Stream event processing logic

### receipt_dynamo_stream (NEW)

**Owns**:
- DynamoDB stream parsing (NEW)
- Stream change detection (NEW)
- Stream message models (NEW)
- Field change analysis (NEW)
- Lightweight stream processing utilities

**Does NOT Own**:
- ChromaDB operations
- Heavy dependencies
- Compaction operations

**Deployment**: ✅ Zip-based Lambda compatible (lightweight dependencies)

### receipt_chroma

**Owns**:
- ChromaDB client operations
- Compaction business logic (NEW)
- EFS snapshot management (NEW)
- Delta merging (NEW)
- Lock management
- S3 snapshot operations
- Embedding operations

**Does NOT Own**:
- DynamoDB operations
- DynamoDB stream parsing
- AWS Lambda orchestration
- SQS message publishing

**Deployment**: ⚠️ Container-based Lambda required (heavy dependencies)

### Lambda Handlers (infra/chromadb_compaction/lambdas/)

**Owns**:
- AWS event handling (DynamoDB streams, SQS)
- Lambda orchestration and routing
- AWS observability (CloudWatch, EMF metrics)
- SQS message publishing
- Error handling and retries
- Partial batch failure handling

**Does NOT Own**:
- ChromaDB business logic (moved to receipt_chroma)

## Migration Phases

### Phase 1: Create Package Structures

**Create `receipt_dynamo_stream` package** (NEW):
- Package structure for stream processing
- Lightweight, zip-Lambda compatible
- Directory structure: `parsing/`, `change_detection/`, `models/`

**Create directories in `receipt_chroma`**:
- `compaction/` - ChromaDB compaction operations

### Phase 2: Move Stream Processing Logic

Copy files from Lambda to `receipt_dynamo_stream`:
- `processor/parsers.py` → `receipt_dynamo_stream/parsing/parsers.py`
- `processor/change_detector.py` → `receipt_dynamo_stream/change_detection/detector.py`
- `processor/models.py` (partial) → `receipt_dynamo_stream/models.py`
- `processor/compaction_run.py` → `receipt_dynamo_stream/parsing/compaction_run.py`

### Phase 3: Move Compaction Logic

Copy files from Lambda to `receipt_chroma`:
- `compaction/operations.py` → `receipt_chroma/compaction/operations.py`
- `compaction/compaction_run.py` → `receipt_chroma/compaction/compaction_run.py`
- `compaction/efs_snapshot_manager.py` → `receipt_chroma/compaction/efs_snapshot_manager.py`
- `compaction/models.py` (partial) → `receipt_chroma/compaction/models.py`

### Phase 4: Update Lambda Handlers

**Stream Processor Lambda** (zip-based):
- Import from `receipt_dynamo_stream` instead of local `processor/`
- Keep SQS publishing in Lambda
- Lightweight deployment

**Compaction Handler Lambda** (container-based):
- Import from `receipt_chroma.compaction` instead of local modules
- Import from `receipt_dynamo_stream` for message models
- Keep orchestration logic in place

### Phase 5: Testing & Validation

Test each package separately:
- Unit tests for `receipt_dynamo_stream` (no ChromaDB needed)
- Unit tests for `receipt_chroma.compaction` (with ChromaDB)
- Integration tests for Lambda handlers
- End-to-end tests in dev environment

### Phase 6: Cleanup & Documentation

After successful migration:
- Remove duplicate code from Lambda
- Update both package documentations
- Update Lambda deployment configs (zip vs container)

## Success Criteria

### Must Have

- [ ] All business logic moved to `receipt_chroma`
- [ ] All existing tests pass
- [ ] Lambda handlers work with new imports
- [ ] No functionality regression
- [ ] Package version updated

### Should Have

- [ ] New unit tests for moved modules
- [ ] Integration tests updated
- [ ] Documentation complete
- [ ] Code coverage maintained

### Nice to Have

- [ ] Performance benchmarks
- [ ] Migration runbook
- [ ] Rollback plan documented

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Import errors after move | High | Medium | Thorough testing, gradual rollout |
| Circular dependencies | High | Low | Careful dependency analysis before move |
| Lambda deployment issues | Medium | Low | Test in dev first, keep rollback ready |
| Test failures | Medium | Medium | Update tests incrementally with code |
| Performance regression | Low | Low | Benchmark before/after |

## Timeline

Estimated effort: **2-3 days**

- **Day 1**: Create structure, move files, update imports (4-6 hours)
- **Day 2**: Write unit tests, fix issues (4-6 hours)
- **Day 3**: Integration testing, documentation (3-4 hours)

## Related Documentation

- [Two-Package Rationale](./TWO_PACKAGE_RATIONALE.md) - Why two packages instead of one
- [Migration Implementation Guide](./MIGRATION_IMPLEMENTATION.md) - Step-by-step instructions
- [File Mapping](./MIGRATION_FILE_MAPPING.md) - Detailed file-by-file mapping
- [Testing Strategy](./MIGRATION_TESTING.md) - Unit and integration test plans
- [Quick Reference](./QUICK_REFERENCE.md) - Commands and patterns
- [Rollback Plan](./MIGRATION_ROLLBACK.md) - How to revert if needed

## References

- [receipt_chroma package](../../receipt_chroma/)
- [Lambda compaction handlers](../../infra/chromadb_compaction/lambdas/)
- [receipt_dynamo entities](../../receipt_dynamo/receipt_dynamo/entities/)

## Questions & Decisions

### Open Questions

- Should we keep `LambdaResponse` in both places or only in Lambda?
- Do we need to maintain backward compatibility during migration?
- Should we version the package (0.1.0 → 0.2.0) after migration?

### Decisions Made

- **Lift-and-shift over refactor**: Safer migration path
- **Keep orchestration in Lambda**: Clear separation of concerns
- **Move domain models**: They belong with business logic
- **Create new subpackages**: Better organization than flat structure

## Change Log

- **2024-12-15**: Initial document created

