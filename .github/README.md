# GitHub Workflows

Quick reference for CI/CD workflows. For detailed documentation, see [CI/CD Pipeline Guide](../docs/development/ci-cd.md).

## Quick Reference

### Active Workflows

| Workflow | Trigger | Purpose | Duration |
|----------|---------|---------|----------|
| `pr-checks.yml` | Pull requests | Format check, quick tests, linting | ~2-3 min |
| `main.yml` | Push/merge to main | Full test suite + deployment | ~15-20 min |
| `claude.yml` | Manual | AI-assisted code review | Varies |
| `swift-ci.yml` | Manual | Swift OCR worker build | Varies |

### Runner

- **Type**: Self-hosted macOS ARM64 runner
- **Location**: `/Users/tnorlund/GitHub/actions-runner/`
- **Labels**: `[self-hosted, macOS, ARM64]`
- **Cost**: $0/month (vs ~$40/month for GitHub-hosted)

### Required Secrets

Configure these in Repository Settings → Secrets and variables → Actions:

- `AWS_ACCESS_KEY_ID` - AWS deployment
- `AWS_SECRET_ACCESS_KEY` - AWS deployment  
- `PULUMI_ACCESS_TOKEN` - Infrastructure deployment
- `OPENAI_API_KEY` - AI features (if applicable)

### Common Commands

VERIFIED WORKAROUND:
```bash
# Skip CI for documentation-only changes
git commit -m "docs: update README [skip ci]"

# Skip tests for non-code changes
git commit -m "chore: update dependencies [skip tests]"
```

### Troubleshooting

**Runner offline?**
```bash
cd /Users/tnorlund/GitHub/actions-runner && ./run.sh
```

**Workflow failing?**
1. Check workflow logs in GitHub Actions tab
2. Run checks locally: `make format && pytest`
3. See [detailed troubleshooting](../docs/development/ci-cd.md#troubleshooting)

---

📖 **For setup, configuration, and detailed documentation**, see [CI/CD Pipeline Guide](../docs/development/ci-cd.md)
