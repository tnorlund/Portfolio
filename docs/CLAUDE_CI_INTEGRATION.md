# Claude CI Integration Guide

This document explains how Claude Code review is integrated with our optimized CI pipeline and provides setup instructions.

## Overview

Claude Code review has been strategically integrated to complement our cost-optimized CI workflow, providing intelligent code analysis while maintaining our ~$2-5/month CI cost target.

## Architecture Integration

### Current CI Pipeline
```
fast-checks (format, lint, change detection) 
    ↓
test-python (receipt_dynamo, receipt_label) 
    ↓
ci-success (summary)
```

### With Claude Integration
```
fast-checks (format, lint, change detection) 
    ↓
test-python (receipt_dynamo, receipt_label) ┬─ claude-review (parallel)
    ↓                                        │
ci-success (summary + claude notification)  ↓
                                           claude-response
```

## Cost Optimization Strategy

### Infrastructure Leverage
- **Self-hosted runners**: Uses existing ARM64 macOS runners (no additional runner costs)
- **Parallel execution**: Claude reviews run alongside tests, not blocking the pipeline
- **Shared concurrency**: Benefits from existing concurrency controls

### Smart Triggering
- **Automatic**: Non-draft PRs under 1000 lines
- **Manual**: `@claude` mentions for interactive help
- **Skip conditions**: Large PRs, documentation-only changes, `skip-claude-review` label

### API Cost Controls
- **Conversation limits**: Max 3 turns per session
- **Timeout protection**: 15-minute maximum runtime
- **Size filtering**: Large PRs automatically deferred

## Setup Instructions

### 1. GitHub Secrets
Add to repository secrets:
```
CLAUDE_CODE_OAUTH_TOKEN=your-oauth-token
```
Note: Uses Claude Code subscription (no additional API costs)

### 2. Runner Labels (Already Configured)
The integration uses your existing runner:
```yaml
runs-on: [self-hosted, macOS, ARM64]
```

### 3. Workflow Files
- ✅ `.github/workflows/claude-review.yml` - Single unified Claude workflow
- ✅ `.github/workflows/main.yml` - Updated with Claude awareness
- ✅ `.github/pull_request_template.md` - Enhanced with Claude section
- ✅ `.github/CLAUDE.md` - Configuration and usage guide

**Note**: Previous implementation had 3 conflicting workflows. Simplified to single workflow per Claude's recommendation.

## Usage Patterns

### Automatic Reviews
Claude automatically reviews:
- Non-draft PRs with code changes
- PRs under 1000 lines
- Changes to Python/TypeScript files

### Interactive Help
Use `@claude` in comments for:
- Specific questions about code logic
- Architecture recommendations
- Performance optimization suggestions
- Test strategy validation

### Manual Control
- Add `skip-claude-review` label to skip reviews
- Add `claude-review-requested` label to force reviews
- Use draft PRs to prevent automatic reviews during development

## Review Focus Areas

Based on our recent CI optimization work, Claude focuses on:

1. **Performance Issues**
   - Test timeout regressions (like the 600s → 30s optimization)
   - CI execution bottlenecks
   - Pattern detection performance

2. **Architecture Patterns**
   - Package boundary adherence (receipt_dynamo vs receipt_label)
   - Dependency separation
   - Code organization

3. **Recent Bug Patterns**
   - Overlapping matches in pattern detection
   - Multi-word pattern handling
   - Race conditions in parallel processing

4. **CI Optimization Alignment**
   - Changes that might affect test parallelization
   - Timeout configurations
   - Coverage collection strategies

## Monitoring and Metrics

### Cost Tracking
Claude API usage is logged in workflow runs:
- Timestamp and trigger type
- PR size and review scope
- Conversation length

### Integration Health
The `ci-success` job reports Claude availability:
- Workflow status visibility
- Configuration validation
- Usage guidance

### Performance Impact
Expected metrics:
- **No CI slowdown**: Reviews run in parallel
- **Minimal cost increase**: ~$0.50-1.00/month for typical usage
- **Quality improvement**: Catch issues before human review

## Comparison with Previous Setup

### Before
- Manual code reviews only
- Performance issues discovered late
- Inconsistent architecture feedback
- No automated pattern detection guidance

### After (Integrated)
- Automatic intelligent reviews
- Performance-focused analysis
- Consistent architecture validation
- Pattern detection expertise
- Cost-controlled deployment

## Testing Checklist

- [ ] Workflow triggers correctly on PR creation
- [ ] Size limits prevent large PR reviews
- [ ] Interactive `@claude` responses work
- [ ] Self-hosted runner executes successfully
- [ ] Cost controls (timeouts, conversation limits) active
- [ ] Integration with existing CI pipeline clean

## Future Enhancements

Potential improvements based on usage patterns:
- Custom review templates for different change types
- Integration with DynamoDB cost tracking
- Conditional review depth based on change scope
- Team-specific configuration profiles

## Troubleshooting

### Claude Not Responding
1. Check `ANTHROPIC_API_KEY` secret exists
2. Verify workflow triggers in Actions tab
3. Check PR is not draft and under size limit
4. Ensure `skip-claude-review` label not present

### Cost Concerns
1. Monitor usage in workflow logs
2. Adjust size limits if needed
3. Use `skip-claude-review` for routine changes
4. Check conversation turn limits

### Integration Issues
1. Verify self-hosted runner is active
2. Check workflow concurrency settings
3. Ensure no conflicts with existing CI

This integration provides intelligent code review capabilities while preserving the cost-effective, performance-optimized CI pipeline we've established.