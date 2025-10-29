# CI/CD Pipeline

> **Quick Reference**: For a quick lookup of workflow names, triggers, and secrets, see [`.github/README.md`](../../.github/README.md)

## Overview

This project uses GitHub Actions for continuous integration and deployment, with cost-optimized self-hosted runners. This guide provides comprehensive documentation for understanding, configuring, and troubleshooting the CI/CD pipeline.

## Workflow Structure

### Pull Request Checks (`.github/workflows/pr-checks.yml`)
Runs on every pull request to ensure code quality.

- **Format Check**: Validates Python formatting (black, isort)
- **Quick Tests**: Runs unit tests for changed packages
- **Type Checking**: MyPy static type analysis
- **Security Scan**: Checks for vulnerable dependencies

**Typical Duration**: 2-3 minutes

### Main Branch Pipeline (`.github/workflows/main.yml`)
Runs on merges to main branch for comprehensive validation.

- **Full Test Suite**: All unit and integration tests
- **Coverage Report**: Code coverage analysis
- **Build Artifacts**: Creates deployable packages
- **Documentation**: Generates API documentation

**Typical Duration**: 15-20 minutes

### Other Workflows

- **`claude.yml`**: AI-assisted code review (manual trigger)
- **`swift-ci.yml`**: Swift OCR worker build and tests

## Self-Hosted Runners

### Setup
We use self-hosted Apple Silicon runners for cost optimization:

```bash
# Location
/Users/tnorlund/GitHub/actions-runner/

# Start runner
./run.sh

# Runner labels
[self-hosted, ARM64, python-tests]
```

### Benefits
- Reduces GitHub Actions costs by ~90%
- Faster execution on local hardware
- Cached dependencies between runs

## Cost Optimization

### Strategies
1. **Self-hosted runners** for Python tests
2. **Intelligent test splitting** for parallelization
3. **Dependency caching** to reduce install time
4. **Conditional workflows** to skip unchanged code

### Monthly Budget
- Target: < $5/month
- Self-hosted saves: ~$40/month
- GitHub-hosted usage: Security tasks only

## Local CI Simulation

### Before Pushing Code
```bash
# Format check
make format

# Run quick tests
./scripts/local_ci_check.sh receipt_dynamo

# Full test suite
./scripts/local_full_test.sh receipt_dynamo integration
```

### Skipping CI
```bash
# Skip CI for documentation changes
git commit -m "docs: update README [skip ci]"

# Skip specific workflows
git commit -m "fix: typo [skip tests]"
```

## Workflow Configuration

### Environment Variables
```yaml
env:
  PYTHON_VERSION: "3.12"
  NODE_VERSION: "18"
  AWS_REGION: "us-east-1"
```

### Secrets Management
Required secrets in GitHub repository settings:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `PULUMI_ACCESS_TOKEN`
- `OPENAI_API_KEY`

### Caching Strategy
```yaml
- uses: actions/cache@v3
  with:
    path: |
      ~/.cache/pip
      .venv
    key: ${{ runner.os }}-pip-${{ hashFiles('**/requirements*.txt') }}
```

## Monitoring

### Workflow Status
- Check: https://github.com/tnorlund/Portfolio/actions
- Email notifications for failures
- Slack integration (optional)

### Performance Metrics
- Average PR check time: 2-3 minutes
- Average full test time: 15-20 minutes
- Monthly action minutes: < 500

## Troubleshooting

### Common Issues

**Python version mismatch**
```bash
# Ensure Python 3.12 is used
python --version
```

**Self-hosted runner offline**
```bash
cd /Users/tnorlund/GitHub/actions-runner
./run.sh
```

**Cache miss**
```bash
# Clear and rebuild cache
rm -rf ~/.cache/pip
pip install -r requirements.txt
```

**Test failures in CI but not locally**
- Check for environment-specific configurations
- Verify all dependencies are in requirements.txt
- Review CI environment variables

## Best Practices

1. **Test locally first** to avoid CI failures
2. **Use draft PRs** during development
3. **Bundle commits** to reduce CI runs
4. **Monitor costs** monthly
5. **Keep workflows simple** and maintainable