# GitHub Workflows Migration Guide

This guide documents the workflow changes made in the CI/CD cleanup and provides migration instructions for any external dependencies.

## Summary of Changes

### Removed Workflows
1. **`claude-code-review.yml`** → Replaced by `claude-review.yml`
2. **`claude-manual-trigger.yml`** → Functionality merged into `claude-review.yml`
3. **`main.yml`** (old version) → Replaced by optimized version

### Renamed Workflows
1. **`main-optimized.yml`** → **`main.yml`**
2. **`claude-code-review-optimized.yml`** → **`claude-review.yml`**

### Updated Workflow Names
- "Pulumi (Optimized)" → "Main CI/CD Pipeline"
- "Claude Code Review (Optimized)" → "Claude Review"
- "Claude Code Review (Enhanced)" → "Claude Review Enhanced"

## Migration Steps

### 1. Update Branch Protection Rules

The required status checks have changed. Update your branch protection settings:

**Old Status Checks** (remove these):
- `PR Status`
- `CI Status`
- `Lint Python`
- `Run unit Tests`
- `Run integration Tests`
- `Build and Deploy`

**New Status Checks** (add these):
- `pr-status` (from pr-checks.yml)
- `ci-status` (from ci-improved.yml)
- `ci-success` (from main.yml)

### 2. Update External References

If you have any external systems referencing the old workflow names, update them:

| Old Reference | New Reference |
|--------------|---------------|
| `claude-code-review.yml` | `claude-review.yml` |
| `claude-manual-trigger.yml` | `claude-review.yml` (use `/claude review` comment) |
| `main-optimized.yml` | `main.yml` |
| Workflow: "Claude Code Review (Optimized)" | Workflow: "Claude Review" |
| Workflow: "Pulumi (Optimized)" | Workflow: "Main CI/CD Pipeline" |

### 3. Update API Calls

If using GitHub API to trigger workflows:

```bash
# Old
gh workflow run claude-code-review.yml

# New
gh workflow run claude-review.yml
```

### 4. Update Documentation

Search your documentation for references to old workflow files and update accordingly.

### 5. Update Monitoring/Dashboards

If you have any dashboards or monitoring that tracks workflow runs by name, update the workflow names.

## Breaking Changes

1. **Manual Claude Review Trigger**: The standalone `claude-manual-trigger.yml` no longer exists. Use `/claude review` comment instead.

2. **Workflow Run Events**: Any automation listening for `workflow_run` events from the old workflows will need updating.

3. **Status Check Names**: Branch protection rules must be updated to use new status check names.

## Verification Checklist

- [ ] Branch protection rules updated
- [ ] External webhook configurations updated
- [ ] Documentation references updated
- [ ] API scripts updated
- [ ] Monitoring/dashboards updated
- [ ] Team notified of changes

## Rollback Plan

If issues arise, the deleted workflows are preserved in git history and can be restored:

```bash
# Restore old workflows
git checkout HEAD~1 -- .github/workflows/claude-code-review.yml
git checkout HEAD~1 -- .github/workflows/claude-manual-trigger.yml
git checkout HEAD~1 -- .github/workflows/main.yml

# Restore old names
git mv .github/workflows/main.yml .github/workflows/main-optimized.yml
git mv .github/workflows/claude-review.yml .github/workflows/claude-code-review-optimized.yml
```

## Support

For questions or issues related to this migration:
1. Check the workflow logs in GitHub Actions
2. Review the `.github/README.md` documentation
3. Contact the DevOps team
