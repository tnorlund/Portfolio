# ChromaDB Compaction Migration Documentation

This directory contains comprehensive documentation for migrating ChromaDB compaction business logic from AWS Lambda handlers to the `receipt_chroma` package.

## Quick Start

**New to this migration?** Start here:

1. Read [MIGRATION_OVERVIEW.md](./MIGRATION_OVERVIEW.md) - Understand the "why" and "what"
2. Review [TWO_PACKAGE_RATIONALE.md](./TWO_PACKAGE_RATIONALE.md) - Understand why two packages
3. Review [MIGRATION_FILE_MAPPING.md](./MIGRATION_FILE_MAPPING.md) - See exactly what moves where
4. Follow [MIGRATION_IMPLEMENTATION.md](./MIGRATION_IMPLEMENTATION.md) - **⭐ Step-by-step implementation**
5. Review [MIGRATION_TESTING.md](./MIGRATION_TESTING.md) - Test strategy and requirements

> **Architecture**: We're creating **two packages** with different deployment needs:
> - `receipt_dynamo_stream` (NEW) - Lightweight stream processing (zip Lambda ✓)
> - `receipt_chroma` (ENHANCED) - ChromaDB operations (container Lambda ⚠️)
>
> Lambda handlers remain as orchestration code (not a package)

## Current Status (Dec 16, 2024)

- ✅ `receipt_dynamo_stream` package scaffolded and installable (stream parsing + change detection lifted out of Lambda)
- 🚧 Next: move enhanced compactor business logic from `infra/chromadb_compaction/lambdas/compaction/` into `receipt_chroma/compaction/` so Lambda keeps only AWS wiring
- 🔜 Add `receipt_dynamo_stream` as a `receipt_chroma` dependency for shared `StreamMessage` models and integration testing

**Need to rollback?** See [MIGRATION_ROLLBACK.md](./MIGRATION_ROLLBACK.md)

## Documentation Structure

### Overview & Planning

- **[MIGRATION_OVERVIEW.md](./MIGRATION_OVERVIEW.md)** - Executive summary, objectives, and strategy
  - Why we're doing this migration
  - Three-package architecture overview
  - Current vs future architecture
  - Success criteria and timeline
  - Risk assessment

- **[TWO_PACKAGE_RATIONALE.md](./TWO_PACKAGE_RATIONALE.md)** - Why two separate packages?
  - Problem with single-package approach
  - Benefits of separation
  - Cost analysis
  - Decision matrix

### Implementation Guides

- **[MIGRATION_IMPLEMENTATION.md](./MIGRATION_IMPLEMENTATION.md)** - ⭐ **Primary implementation guide**
  - Complete step-by-step instructions
  - receipt_dynamo_stream package creation
  - receipt_chroma enhancements
  - Lambda handler updates
  - Verification steps
  - Troubleshooting guide

- **[MIGRATION_FILE_MAPPING.md](./MIGRATION_FILE_MAPPING.md)** - Complete file mapping reference
  - Source to destination mapping
  - Detailed function analysis
  - Import dependency graphs
  - Line count and size analysis
  - What moves to each package

- **[QUICK_REFERENCE.md](./QUICK_REFERENCE.md)** - Quick command reference
  - Essential commands
  - Import patterns
  - Common issues & fixes
  - Verification checklist
  - Keep open during implementation

### Testing

- **[MIGRATION_TESTING.md](./MIGRATION_TESTING.md)** - Comprehensive testing strategy
  - Unit test requirements and examples
  - Integration test requirements and examples
  - End-to-end test scenarios
  - Coverage requirements
  - Test implementation checklist

### Operations

- **[MIGRATION_ROLLBACK.md](./MIGRATION_ROLLBACK.md)** - Rollback procedures and safety nets
  - When to rollback
  - Quick rollback (Lambda only)
  - Full rollback (Packages + Lambda)
  - Post-rollback actions
  - Prevention strategies

## Document Reading Order

### For Engineers Implementing the Migration

```
1. MIGRATION_OVERVIEW.md (15 min)
   ↓ Understand objectives and strategy
2. TWO_PACKAGE_RATIONALE.md (10 min)
   ↓ Understand why two packages
3. MIGRATION_FILE_MAPPING.md (20 min)
   ↓ See what moves where
4. MIGRATION_IMPLEMENTATION.md (45 min)
   ↓ Follow step-by-step
5. MIGRATION_TESTING.md (30 min)
   ↓ Understand testing requirements
6. MIGRATION_ROLLBACK.md (15 min)
   ↓ Know the safety net

Total Time: ~2.5 hours
```

### For Reviewers/Approvers

```
1. MIGRATION_OVERVIEW.md (15 min)
   ↓ High-level understanding
2. TWO_PACKAGE_RATIONALE.md (10 min)
   ↓ Architecture justification
3. MIGRATION_FILE_MAPPING.md (10 min - skim)
   ↓ Quick file movement overview

Total Time: ~35 minutes
```

### For QA/Test Engineers

```
1. MIGRATION_OVERVIEW.md (10 min - sections 1-2)
   ↓ Context only
2. MIGRATION_TESTING.md (45 min)
   ↓ Full testing strategy
3. MIGRATION_IMPLEMENTATION.md (10 min - Verification section)
   ↓ How to verify

Total Time: ~1 hour
```

## Key Concepts

### Two-Package Separation

We're splitting business logic into **two packages** with different characteristics:

1. **`receipt_dynamo_stream`** (NEW) - Lightweight stream processing
   - ✅ Zip Lambda compatible (no heavy dependencies)
   - DynamoDB stream parsing
   - Change detection
   - Stream message models

2. **`receipt_chroma`** - ChromaDB operations
   - ⚠️ Container Lambda required (heavy dependencies)
   - Compaction operations
   - EFS snapshot management
   - Delta merging

3. **Lambda Handlers** - AWS orchestration
   - Event routing
   - SQS publishing
   - Observability

**Why Two Separate Packages?**
- `receipt_chroma` has heavy dependencies (ChromaDB, numpy) requiring container Lambda
- Stream processor is lightweight and should use zip Lambda (faster, cheaper)
- Separating `receipt_dynamo_stream` keeps stream processing fast and independently deployable
- Lambda handlers stay as orchestration code, not a package

### Lift-and-Shift Strategy

This migration uses a **lift-and-shift** approach:
- Copy code as-is without refactoring
- Update only imports and minor dependencies
- Preserve all functionality
- Refactor later after safe migration

### Business Logic vs Orchestration

**Business Logic** (moving to packages):
- Stream processing → `receipt_dynamo_stream`
- ChromaDB operations → `receipt_chroma`
- Domain logic and algorithms
- Reusable across contexts

**Orchestration** (staying in Lambda):
- AWS event handling
- SQS publishing
- Observability and metrics
- Error handling and retries

### Package Structures

After migration:

```
receipt_dynamo_stream/    # NEW: Lightweight
├── parsing/
│   ├── parsers.py
│   └── compaction_run.py
├── change_detection/
│   └── detector.py
└── models.py

receipt_chroma/           # Enhanced
├── compaction/           # NEW: Compaction logic
│   ├── operations.py
│   ├── compaction_run.py
│   ├── efs_snapshot_manager.py
│   └── models.py
├── data/                 # EXISTING
├── embedding/            # EXISTING
├── s3/                   # EXISTING
└── lock_manager.py       # EXISTING
```

## Migration Checklist

Use this checklist to track progress:

### Planning Phase
- [ ] Read all documentation
- [ ] Understand current architecture
- [ ] Identify files to move
- [ ] Review import dependencies
- [ ] Plan testing approach

### Implementation Phase
- [x] Create `receipt_dynamo_stream` package structure and models
- [x] Copy stream parsing/change detection business logic
- [ ] Create `receipt_chroma/compaction` module and move compactor business logic out of Lambda
- [ ] Update imports in Lambda handlers to use packages (keep AWS wiring local)
- [ ] Update package exports

### Testing Phase
- [ ] Write unit tests
- [ ] Write integration tests
- [ ] Run all tests locally
- [ ] Verify import statements
- [ ] Test Lambda handlers locally

### Deployment Phase
- [ ] Deploy to dev environment
- [ ] Monitor dev for 24 hours
- [ ] Run integration tests in dev
- [ ] Deploy to staging (if applicable)
- [ ] Deploy to production with monitoring

### Post-Migration Phase
- [ ] Monitor production metrics
- [ ] Verify compaction operations
- [ ] Document any issues
- [ ] Update package documentation
- [ ] Create lessons learned doc

## Estimated Timeline

| Phase | Duration | Effort |
|-------|----------|--------|
| Planning & Review | 2.5 hours | Reading docs, understanding three-package architecture |
| Create receipt_dynamo_stream | 2-3 hours | New package creation, file moves, testing |
| Update receipt_chroma | 2-3 hours | Add compaction module, file moves, testing |
| Update Lambda handlers | 1-2 hours | Import updates, verification |
| Testing | 3-4 hours | Unit tests, integration tests, fixes |
| Dev Deployment | 24 hours | Deploy + monitoring |
| Production Deployment | 1 hour + monitoring | Deploy + verify |
| **Total** | **2-3 days** | ~13-18 hours active work + monitoring |

## Success Metrics

After migration, verify:

### Technical Metrics
- [ ] All business logic in `receipt_chroma` package
- [ ] All tests passing (unit, integration, e2e)
- [ ] Test coverage maintained or improved
- [ ] No AWS dependencies in business logic
- [ ] Clear import boundaries

### Operational Metrics
- [ ] Lambda execution time unchanged or improved
- [ ] Compaction success rate maintained
- [ ] No increase in error rates
- [ ] No customer-facing issues
- [ ] CloudWatch metrics normal

### Code Quality Metrics
- [ ] Code duplication reduced
- [ ] Package size appropriate
- [ ] Import graph clean
- [ ] Documentation complete

## Common Questions

### Q: Why are we doing this migration?

**A**: To separate business logic from AWS infrastructure, making the code more reusable, testable, and maintainable.

### Q: What's the risk level?

**A**: Medium. We're using lift-and-shift to minimize changes, and we have comprehensive testing and rollback plans.

### Q: How long will this take?

**A**: 2-3 days including implementation, testing, and monitoring.

### Q: What if something goes wrong?

**A**: See [MIGRATION_ROLLBACK.md](./MIGRATION_ROLLBACK.md) for detailed rollback procedures. We can rollback Lambda deployment in ~5 minutes.

### Q: Will there be downtime?

**A**: No. Lambda deployments are zero-downtime by design.

### Q: Do we need to update other services?

**A**: No. This is an internal refactor. External interfaces remain unchanged.

### Q: Can we deploy gradually?

**A**: Yes. Deploy to dev first, then staging, then production. Monitor at each stage.

### Q: What about existing tests?

**A**: Existing tests will be updated with new import paths. New tests will be added for moved modules.

## Related Documentation

### Package Documentation
- [receipt_chroma README](../../receipt_chroma/README.md)
- [receipt_dynamo README](../../receipt_dynamo/README.md)

### Lambda Documentation
- [Enhanced Compaction Handler](../../infra/chromadb_compaction/lambdas/README.md)
- [Stream Processor](../../infra/chromadb_compaction/lambdas/processor/README.md)

### Architecture Documentation
- [ChromaDB Architecture](../architecture/chromadb.md)
- [Compaction System Design](../architecture/compaction.md)
- [DynamoDB Streams](../architecture/dynamodb-streams.md)

## Contributing

### Updating Documentation

When updating these docs:

1. Keep consistent formatting
2. Update "Last Updated" date
3. Keep code examples accurate
4. Update checklists if needed
5. Cross-reference related docs

### Adding New Documents

If adding new documentation:

1. Add link to this README
2. Follow existing document structure
3. Include frontmatter (status, dates)
4. Add to reading order section

## Support

If you encounter issues during migration:

1. **Check Documentation**: Review relevant sections
2. **Troubleshooting Guide**: See MIGRATION_IMPLEMENTATION.md
3. **Rollback Plan**: See MIGRATION_ROLLBACK.md if needed
4. **Team Discussion**: Discuss in #engineering channel
5. **Create Issue**: Document persistent problems in GitHub

## Change Log

- **2024-12-15**: Initial documentation created
  - MIGRATION_OVERVIEW.md
  - MIGRATION_IMPLEMENTATION.md
  - MIGRATION_TESTING.md
  - MIGRATION_FILE_MAPPING.md
  - MIGRATION_ROLLBACK.md
  - README.md (this file)

## License

This documentation is part of the Portfolio project and follows the same license as the codebase.

---

**Last Updated**: December 16, 2024
**Maintained By**: Engineering Team
**Status**: Implementation - Phase 2 (receipt_chroma integration)
