# GitHub Repository Configuration

This directory contains GitHub-specific configuration files for the repository.

## 📋 Pull Request Template

When opening a pull request, a template is automatically provided that includes:

- Type of change (bug fix, feature, documentation, etc.)
- Testing checklist
- Documentation checklist
- AI review status tracking
- Deployment notes section

See [`.github/pull_request_template.md`](pull_request_template.md) for the full template.

**Usage**: Just create a PR normally — the template will automatically populate.

## 🔄 GitHub Actions Workflows

Automated CI/CD workflows are configured in [`workflows/`](workflows/):

- `main.yml` - Main CI/CD pipeline
- `pr-checks.yml` - Pull request validation
- `claude.yml` - AI-assisted code review
- `claude-code-review.yml` - Additional code review automation
- `swift-ci.yml` - Swift OCR worker builds

For detailed workflow documentation, see:
- **[CI/CD Pipeline Guide](../docs/development/ci-cd.md)** - Comprehensive setup and troubleshooting
- **[Root README CI/CD section](../README.md#-cicd)** - Quick overview

## 🔧 Adding New Workflows

When adding new workflows:

1. Place workflow files in `workflows/` with `.yml` extension
2. Follow existing naming conventions
3. Document any required secrets in the workflow file comments
4. Update this README if the workflow needs special mention

## 📚 Related Documentation

- [Development Setup](../docs/development/setup.md)
- [Testing Guide](../docs/development/testing.md)
- [Contributing Guidelines](../CONTRIBUTING.md) (if present)
