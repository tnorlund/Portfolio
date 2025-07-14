# GitHub Actions Workflows

This directory contains all GitHub Actions workflow definitions for the repository. Below is a detailed breakdown of each workflow, its purpose, and usage.

## Workflow Files

### Core CI/CD Workflows

#### 🚀 [`main.yml`](./main.yml)
**Purpose**: Primary CI/CD pipeline for production deployments
**Triggers**:
- Push to `main` branch
- Pull requests (for testing only, no deployment)

**Jobs**:
1. `fast-checks` - Format and lint verification
2. `test-python` - Python test matrix (unit/integration)
3. `test-typescript` - TypeScript tests
4. `build-and-deploy` - Pulumi deployment (main branch only)
5. `e2e-tests` - End-to-end tests (after deployment)
6. `ci-success` - Summary job for branch protection

**Key Features**:
- Parallel test execution with matrix strategy
- Conditional deployment (only on main)
- Comprehensive test coverage reporting
- Automatic cancellation of in-progress runs

---

#### ✅ [`pr-checks.yml`](./pr-checks.yml)
**Purpose**: Quick validation for pull requests
**Triggers**: All pull request events

**Jobs**:
1. `format-check` - Python formatting with auto-fix
2. `quick-tests` - Subset of tests per package
3. `pr-status` - Summary status for branch protection

**Optimizations**:
- Runs only essential tests
- Auto-formats code and commits changes
- Provides quick feedback

---

#### 🔧 [`ci-improved.yml`](./ci-improved.yml)
**Purpose**: Full CI pipeline with comprehensive checks
**Triggers**:
- Push to main
- Pull requests

**Jobs**:
1. `format-python` - Auto-formatting (PR only)
2. `lint-python` - Pylint and mypy checks
3. `lint-typescript` - TypeScript linting
4. `test-python` - Full test suite with matrix
5. `test-typescript` - TypeScript tests
6. `docs` - Documentation generation
7. `deploy` - Deployment (main only)
8. `ci-status` - Summary for branch protection

---

#### 🚢 [`deploy.yml`](./deploy.yml)
**Purpose**: Standalone deployment workflow
**Triggers**: Manual dispatch

**Configuration Options**:
- Target environment selection
- Pulumi stack configuration
- Manual approval gates

### Claude AI Workflows


#### 🔍 [`claude-review.yml`](./claude-review.yml)
**Purpose**: Consolidated Claude code review, interactive assistance, and comment management
**Triggers**:
- PR marked "ready for review" (automatic)
- PR labeled with `claude-review-requested`
- Comments containing `@claude`

**Unified Features**:
- Reviews all PRs regardless of size
- Uses Claude subscription (not API key)
- Manages and collapses outdated comments
- Cleanup on PR merge
- Self-hosted runner optimization
- Interactive Q&A support
- Comprehensive review guidelines
- Cost optimization controls

---

#### 🧹 [`claude-cleanup.yml`](./claude-cleanup.yml)
**Purpose**: Manage Claude comment lifecycle
**Triggers**:
- Weekly schedule (Sundays at midnight)
- Manual: workflow dispatch
- Comment: `/claude cleanup`

**Actions**:
- Collapses outdated reviews
- Deletes comments from merged PRs
- Preserves latest review per day

---

#### 📊 [`track-ai-usage.yml`](./track-ai-usage.yml)
**Purpose**: Track Claude API usage and costs
**Triggers**: After Claude review workflows complete

**Tracking**:
- Token usage estimation
- Cost calculation ($0.015/1K input, $0.075/1K output)
- Stores in DynamoDB
- Links usage to specific PRs

## Workflow Patterns and Best Practices

### 1. Status Checks
Each major workflow provides a summary job for branch protection:
- `main.yml` → `ci-success`
- `pr-checks.yml` → `pr-status`
- `ci-improved.yml` → `ci-status`

### 2. Concurrency Control
```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

### 3. Conditional Execution
- Deployment only on main branch
- Auto-formatting only on PRs
- Different test sets for PRs vs main

### 4. Matrix Strategy
Used for parallel execution:
```yaml
matrix:
  package: [receipt_upload, receipt_label, ...]
  test-type: [unit, integration]
```

### 5. Secret Management
Required secrets:
- `CLAUDE_CODE_OAUTH_TOKEN` - Claude subscription access (preferred over API key)
- `AWS_ACCESS_KEY_ID` - AWS credentials
- `AWS_SECRET_ACCESS_KEY` - AWS credentials
- `PULUMI_ACCESS_TOKEN` - Infrastructure deployment
- `DYNAMODB_TABLE_NAME` - Usage tracking

## Manual Triggers and Commands

### PR Comments
- `/claude review` - Trigger Claude code review
- `/claude cleanup` - Clean up Claude comments
- `@claude [question]` - Interactive assistance

### Workflow Dispatch
Several workflows support manual triggering via GitHub UI:
- `deploy.yml` - Manual deployment
- `claude-cleanup.yml` - Manual cleanup
- `track-ai-usage.yml` - Manual usage tracking

### Labels
Control workflow behavior with PR labels:
- `claude-review-requested` - Force Claude review
- `skip-claude-review` - Skip automatic review
- `claude-no-cleanup` - Preserve comments

## Cost Optimization

### Workflow Efficiency
1. Use path filters to avoid unnecessary runs
2. Implement size checks for AI reviews
3. Cache dependencies effectively
4. Use matrix strategies for parallelization

### Claude AI Costs
- Average PR review: ~5K tokens ≈ $0.24
- Reviews all PRs regardless of size
- Track usage via `track-ai-usage.yml`

## Debugging Workflows

### Common Issues
1. **Status checks not appearing**: Check job names match branch protection settings
2. **Workflows not triggering**: Verify triggers and path filters
3. **Permission errors**: Check token permissions in workflow
4. **Timeout errors**: Review job time limits and optimize

### Useful Commands
```bash
# View workflow runs
gh run list

# View specific run details
gh run view <run-id>

# Download workflow logs
gh run download <run-id>

# Re-run failed workflow
gh run rerun <run-id>
```

## Maintenance

### Regular Tasks
1. Review workflow run times monthly
2. Update deprecated actions quarterly
3. Audit secret usage and permissions
4. Clean up experimental workflows
5. Monitor AI usage costs

### Version Pinning
Always pin action versions for stability:
```yaml
uses: actions/checkout@v4  # Good
uses: actions/checkout@main  # Avoid
```

## Contributing

When adding or modifying workflows:
1. Test in a feature branch first
2. Document the workflow purpose and triggers
3. Add appropriate concurrency controls
4. Include status check jobs for branch protection
5. Follow existing naming conventions
6. Update this README

## Additional Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Workflow Syntax Reference](https://docs.github.com/en/actions/reference/workflow-syntax-for-github-actions)
- [Claude API Documentation](https://docs.anthropic.com/claude/reference/getting-started-with-the-api)
- [Pulumi GitHub Actions](https://www.pulumi.com/docs/guides/continuous-delivery/github-actions/)
