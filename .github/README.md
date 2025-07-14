# GitHub Configuration and Workflows

This directory contains GitHub-specific configuration files and workflows that automate various aspects of our development process.

## Directory Structure

```
.github/
├── workflows/          # GitHub Actions workflow definitions
├── branch-protection.json  # Branch protection rules configuration
├── pull_request_template.md  # PR template for consistent contributions
├── CLAUDE.md          # AI workflow guidelines and best practices
└── README.md          # This file
```

## Workflows Overview

### Core CI/CD Workflows

#### 🚀 `main.yml` - Main CI/CD Pipeline
- **Purpose**: Complete CI/CD pipeline for production deployments
- **Triggers**: Push to main branch, Pull requests
- **Features**:
  - Python and TypeScript linting
  - Parallel unit and integration tests
  - Documentation generation
  - Pulumi infrastructure deployment (main branch only)
  - E2E tests after deployment
  - Coverage reporting
  - Concurrency control with auto-cancellation

#### ✅ `pr-checks.yml` - Quick PR Validation
- **Purpose**: Fast validation checks for pull requests
- **Triggers**: Pull request events
- **Features**:
  - Quick format checks with auto-fix
  - Fast subset of tests
  - Status comments on PR
  - Optimized for speed

#### 🔧 `ci-improved.yml` - Full CI with Auto-formatting
- **Purpose**: Comprehensive CI with automatic code formatting
- **Triggers**: Push to main, Pull requests
- **Features**:
  - Auto-formatting for Python code
  - Full test suite
  - Documentation generation
  - Branch protection status checks

#### 🚢 `deploy.yml` - Deployment Workflow
- **Purpose**: Manual or automated deployment workflow
- **Triggers**: Manual dispatch, Push to specific branches
- **Features**:
  - Configurable deployment targets
  - Environment-specific configurations

### Claude AI Workflows

#### 🤖 `claude.yml` - Interactive Claude Assistant
- **Purpose**: Interactive AI assistance via mentions
- **Triggers**:
  - Issue comments containing `@claude`
  - PR review comments containing `@claude`
  - New issues with `@claude` mention
- **Features**:
  - Context-aware responses
  - Code analysis and suggestions
  - Interactive Q&A

#### 🔍 `claude-review.yml` - Automated PR Code Review
- **Purpose**: Automated code review for pull requests
- **Triggers**:
  - PR ready for review
  - PR labeled with `claude-review-requested`
  - Manual trigger via `/claude review` comment
- **Features**:
  - Reviews all PRs regardless of size
  - File type filtering (focuses on code files)
  - Permission checks
  - Cost-optimized

#### 🔍+ `claude-review-enhanced.yml` - Advanced PR Review
- **Purpose**: Enhanced PR review with comment management
- **Triggers**: PR ready for review, labeled, or synchronized
- **Features**:
  - Manages and collapses outdated Claude comments
  - Review metadata and summaries
  - GraphQL optimization
  - Cleanup after merge

#### 🧹 `claude-cleanup.yml` - Comment Management
- **Purpose**: Clean up old Claude comments
- **Triggers**:
  - Weekly schedule
  - Manual dispatch
  - `/claude cleanup` comment
- **Features**:
  - Collapses outdated reviews
  - Deletes comments from merged PRs
  - Keeps latest review visible

#### 📊 `track-ai-usage.yml` - AI Usage Tracking
- **Purpose**: Track Claude API usage and costs
- **Triggers**: Completion of Claude review workflows
- **Features**:
  - Token usage estimation
  - Cost calculation
  - DynamoDB storage
  - PR-level tracking

## Configuration Files

### `branch-protection.json`
Defines branch protection rules for the repository:
- Required status checks
- Review requirements
- Merge restrictions
- Force push protection

### `pull_request_template.md`
Standard template for pull requests including:
- Description section
- Type of change checklist
- Testing confirmation
- Size guidelines for cost optimization
- Review requirements

## Best Practices

1. **Workflow Naming**: Use clear, descriptive names that indicate purpose
2. **Concurrency**: Use concurrency groups to prevent duplicate runs
3. **Permissions**: Follow principle of least privilege
4. **Cost Optimization**:
   - Use path filters to avoid unnecessary runs
   - Implement size checks for AI reviews
   - Cache dependencies effectively
5. **Status Checks**: Ensure critical workflows provide status checks for branch protection

## Manual Triggers

Several workflows support manual triggers:
- `/claude review` - Trigger Claude code review on a PR
- `/claude cleanup` - Clean up Claude comments
- `@claude [question]` - Get interactive AI assistance

## Environment Variables and Secrets

Required secrets for workflows:
- `ANTHROPIC_API_KEY` - Claude API access
- `AWS_ACCESS_KEY_ID` - AWS credentials
- `AWS_SECRET_ACCESS_KEY` - AWS credentials
- `PULUMI_ACCESS_TOKEN` - Pulumi deployment token
- `DYNAMODB_TABLE_NAME` - For usage tracking

## Maintenance

- Review and update workflows quarterly
- Monitor workflow run times and optimize as needed
- Track AI usage costs via the usage tracking workflow
- Clean up experimental or unused workflows promptly

## Support

For issues or questions about workflows:
1. Check workflow logs in the Actions tab
2. Review this documentation
3. Consult the team's DevOps channel
4. Submit issues to the repository
