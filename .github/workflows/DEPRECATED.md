# Deprecated Workflows

The following workflows have been removed or renamed. This file serves as a reference for the migration.

## Removed Workflows

### `claude-code-review.yml`
- **Removed on**: [Date of this PR]
- **Replaced by**: `claude-review.yml`
- **Reason**: Duplicate of optimized version

### `claude-manual-trigger.yml`
- **Removed on**: [Date of this PR]
- **Replaced by**: `/claude review` comment trigger in `claude-review.yml`
- **Reason**: Functionality consolidated into main review workflow

### `main.yml` (old version)
- **Removed on**: [Date of this PR]
- **Replaced by**: Optimized version (renamed from `main-optimized.yml`)
- **Reason**: Performance improvements and better structure

## Renamed Workflows

### `main-optimized.yml` → `main.yml`
- **Renamed on**: [Date of this PR]
- **Reason**: Became the primary CI/CD pipeline

### `claude-code-review-optimized.yml` → `claude-review.yml`
- **Renamed on**: [Date of this PR]
- **Reason**: Simplified naming convention

## If you're looking for these workflows:
- Check the new workflow names above
- Review `.github/README.md` for current workflow documentation
- See `.github/MIGRATION_GUIDE.md` for migration instructions
