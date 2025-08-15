# ChromaDB Branch Merge Strategy

## Overview
The `chroma_db` branch has diverged significantly from `main` with 131 commits and changes across 744 files (86,715 insertions, 6,672 deletions). This document outlines a systematic approach to merge these changes back to main in a controlled, reviewable manner.

## Current State Analysis

### Branch Divergence
- **Commits ahead of main**: 131
- **Files changed**: 744
- **Lines added**: 86,715
- **Lines removed**: 6,672
- **Last merge from main**: Multiple months ago

### Key Areas of Change
1. **ChromaDB Integration**: New vector database functionality
2. **Infrastructure Refactoring**: ComponentResource pattern, modular architecture
3. **Code Quality**: Mypy/pylint fixes, type annotations
4. **Build Optimizations**: Docker improvements, parallel builds
5. **Frontend Updates**: New TypeScript interfaces and documentation

## Recommended Merge Strategy: Phased Pull Requests

### Phase 1: Foundation & Cleanup (Low Risk, High Value)

#### PR 1: Code Quality Improvements
**Commits to include**:
- `ff46b0fa` - Fix mypy and pylint issues in embedding_step_functions
- `d65d041b` - Fix mypy and pylint issues in embedding infrastructure

**Changes**:
- Add comprehensive type annotations to Lambda handlers
- Fix line length issues (79-character limit)
- Convert f-string logging to lazy % formatting
- Consolidate mypy/pylint config into `infra/pyproject.toml`

**Risk Level**: ✅ Low - Non-functional changes
**Testing Required**: Run mypy and pylint validation

```bash
git checkout main
git pull origin main
git checkout -b feature/code-quality-improvements
git cherry-pick ff46b0fa d65d041b
```

#### PR 2: Infrastructure Cleanup
**Commits to include**:
- `6e0b3511` - Remove unnecessary requirements.txt files
- `3978c246` - Clean up deprecated embedding infrastructure files
- `e80aa2e1` - Clean up infrastructure naming for consistency
- `a3d32b0b` - Remove outdated architecture documentation

**Changes**:
- Remove ~6,820 lines of deprecated code
- Standardize naming conventions
- Remove redundant configuration files

**Risk Level**: ✅ Low - Removing unused code
**Testing Required**: `pulumi preview` to ensure no active resources affected

```bash
git checkout main
git checkout -b feature/infrastructure-cleanup
git cherry-pick 6e0b3511 3978c246 e80aa2e1 a3d32b0b
```

### Phase 2: Core Infrastructure Refactoring (Medium Risk)

#### PR 3: ComponentResource Architecture
**Commits to include**:
- `6f793c12` - Refactor embedding infrastructure with ComponentResource pattern
- `78132344` - Refactor lambda aliasing and update ECR/image naming

**Changes**:
- Modular component architecture
- Improved code organization
- Backward compatibility aliases

**Risk Level**: ⚠️ Medium - Architectural changes
**Testing Required**: 
- Full `pulumi up` in test environment
- Verify all Lambda functions deploy correctly
- Check Step Functions execute properly

```bash
git checkout main
git checkout -b feature/component-architecture
git cherry-pick 6f793c12 78132344
```

#### PR 4: Resource Naming and Tagging
**Commits to include**:
- `899bc835` - Rename Lambda functions to consistent naming
- `367dd1e8` - Rename Step Functions to submit/ingest convention
- `d02aae14` - Add missing environment tags to AWS resources
- `4b056640` - Remove explicit resource names from Pulumi definitions

**Changes**:
- Standardized naming: `embedding-{entity}-{action}-lambda`
- Step Functions: `embedding-{entity}-{submit|ingest}-sf`
- Consistent tagging strategy

**Risk Level**: ⚠️ Medium - Resource renaming
**Testing Required**: 
- Verify Pulumi aliases work correctly
- Ensure no service disruption during deployment

### Phase 3: ChromaDB Implementation (Higher Risk)

#### PR 5: ChromaDB Core Infrastructure
**Commits to include**:
- `6ece9732` - Implement database-aware snapshot architecture
- `468a460b` - Implement database separation for ChromaDB embeddings
- ChromaDB S3 buckets and SQS queues setup commits

**Changes**:
- ChromaDB vector database integration
- S3-based persistence layer
- SQS queue for async processing
- Database separation (words vs lines)

**Risk Level**: 🔴 High - New feature
**Testing Required**:
- End-to-end embedding pipeline tests
- S3 snapshot/delta verification
- Performance testing with production-like data

#### PR 6: ChromaDB Bug Fixes and Optimizations
**Commits to include**:
- `5de6ebe2` - Fix ChromaDB metadata error (lists to strings)
- `48f9e4fb` - Fix word embedding pipeline query
- `798b0e8e` - Fix ChromaDB collection name validation
- `be8ffbc6` - Fix ChromaDB delta upload to S3

**Changes**:
- Critical bug fixes for ChromaDB operations
- Metadata handling improvements
- Collection name validation

**Risk Level**: ⚠️ Medium - Bug fixes
**Testing Required**: 
- Regression testing for each fix
- Verify metadata handling with various data types

### Phase 4: Build and Deployment Optimizations

#### PR 7: Docker and Build Improvements
**Commits to include**:
- `c3175ce9` - Optimize base image builds for parallel execution
- `ce177d28` - Fix Lambda image URI to use digest
- Docker-related optimization commits

**Changes**:
- Parallel Docker builds
- Improved caching strategy
- ECR repository optimizations

**Risk Level**: ⚠️ Medium - Build pipeline changes
**Testing Required**:
- CI/CD pipeline validation
- Deployment time comparison
- Image size verification

## Implementation Timeline

### Week 1
- [ ] PR 1: Code Quality Improvements
- [ ] PR 2: Infrastructure Cleanup
- [ ] Test and merge both PRs

### Week 2
- [ ] PR 3: ComponentResource Architecture
- [ ] PR 4: Resource Naming and Tagging
- [ ] Extensive testing in dev environment

### Week 3-4
- [ ] PR 5: ChromaDB Core Infrastructure
- [ ] PR 6: ChromaDB Bug Fixes
- [ ] Integration testing

### Week 5
- [ ] PR 7: Build Optimizations
- [ ] Final testing and documentation updates

## Cherry-Pick Commands Reference

```bash
# Setup
git fetch origin
git checkout main
git pull origin main

# Create working branch
git checkout -b feature/[feature-name]

# Cherry-pick specific commits
git cherry-pick [commit-hash]

# If conflicts arise
git status
# Resolve conflicts in editor
git add [resolved-files]
git cherry-pick --continue

# Push and create PR
git push origin feature/[feature-name]
```

## Testing Checklist for Each PR

### Pre-merge Requirements
- [ ] All tests pass (`pytest`)
- [ ] Mypy validation passes (`mypy .`)
- [ ] Pylint score > 8.0 (`pylint .`)
- [ ] `pulumi preview` shows expected changes
- [ ] No breaking changes to existing APIs
- [ ] Documentation updated if needed

### Deployment Validation
- [ ] Deploy to test environment first
- [ ] Run smoke tests
- [ ] Monitor CloudWatch logs for errors
- [ ] Verify Lambda execution times
- [ ] Check for any cost implications

## Rollback Plan

If issues arise after merging:

1. **Immediate Rollback**:
   ```bash
   git revert [merge-commit]
   git push origin main
   ```

2. **Pulumi Rollback**:
   ```bash
   pulumi stack select [stack-name]
   pulumi refresh
   pulumi up --target-dependents
   ```

3. **Hotfix Process**:
   - Create hotfix branch from main
   - Apply minimal fix
   - Fast-track through review
   - Deploy immediately

## Success Metrics

- **Code Quality**: Mypy passes, Pylint > 9.0
- **Deployment Time**: < 10 minutes for full stack
- **Test Coverage**: > 80% for new code
- **Zero Downtime**: No service interruptions during deployment
- **Performance**: No degradation in Lambda execution times

## Notes and Considerations

1. **Backward Compatibility**: All changes maintain aliases for existing resources
2. **Cost Impact**: ChromaDB infrastructure adds ~$5-10/month in AWS costs
3. **Documentation**: Update README files with new architecture
4. **Team Communication**: Notify team before major deployments
5. **Monitoring**: Set up CloudWatch alarms for new resources

## Contact

For questions about this merge strategy:
- Review the PR descriptions for detailed change logs
- Check CloudWatch logs for deployment issues
- Consult the architecture diagrams in `/docs`

---

*Last Updated: [Current Date]*
*Branch Status: 131 commits ahead of main*
*Estimated Completion: 5 weeks*