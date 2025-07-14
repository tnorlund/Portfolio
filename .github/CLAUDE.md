# Claude AI Workflow Guidelines

This document provides guidelines and best practices for using Claude AI workflows in this repository.

## Overview

We use Claude AI for automated code reviews and interactive assistance to improve code quality and developer productivity. This system is fully integrated with our optimized CI pipeline, leveraging self-hosted runners and cost-effective triggering strategies.

## Integration with Optimized CI

Our Claude integration builds on the existing cost-optimized CI infrastructure:
- **Self-hosted runners**: Leverages your existing ARM64 macOS runners for ~50% cost reduction
- **Selective triggering**: Only activates when needed, preserving your $2-5/month CI target
- **Fast-checks integration**: Complements existing fast-checks → test-python workflow pattern
- **Performance focus**: Specifically trained on your CI optimization patterns and performance bottlenecks

## Claude Integration Workflow

### Consolidated Claude Workflow (`claude-review.yml`)
A single, unified workflow that provides automated PR reviews, interactive assistance, and comment management.

**Triggers:**
- **Automatic**: Non-draft PRs (all sizes)
- **Interactive**: `@claude` mentions in PR/issue comments
- **Manual**: `claude-review-requested` label to force reviews
- **Conditional**: Skips documentation-only changes and large PRs

**Cost Optimization Features:**
- **No size limits**: Reviews all PRs regardless of size
- **Self-hosted runners**: Uses your existing ARM64 macOS infrastructure
- **Smart filtering**: Ignores documentation-only changes
- **Conversation limits**: Max 3 turns to prevent runaway costs
- **Selective triggering**: Only on meaningful code changes

**Review Focus Areas:**
- Performance implications (especially test timeouts and CI optimization)
- Pattern detection logic (overlapping matches, race conditions)
- Package boundary adherence (receipt_dynamo vs receipt_label separation)
- Architecture alignment with your CI optimization patterns
- Security and maintainability

### Interactive Features
The unified workflow provides comprehensive interactive assistance:

**Usage Examples:**
- `@claude can you explain how this function works?`
- `@claude review this change for performance implications`
- `@claude help me understand the pattern detection logic`

**Capabilities:**
- Code explanation and analysis
- Architecture recommendations
- Performance optimization suggestions
- Bug identification and fixes
- Test strategy validation

## Cost Management

### Current Pricing (Claude 3 Opus)
- Input: $0.015 per 1K tokens
- Output: $0.075 per 1K tokens
- Average PR review: ~5K tokens ≈ $0.24

### Cost Optimization Strategies

1. **Size Limits**
   - Keep PRs under 500 lines for automatic reviews
   - Split large features into multiple PRs
   - Use `skip-claude-review` label for documentation-only changes

2. **Smart Triggering**
   - Reviews only trigger on "ready for review" status
   - Use labels to control when reviews happen
   - Batch related changes together

3. **File Filtering**
   - Non-code files are automatically excluded
   - Focus reviews on critical paths

### Usage Tracking
All Claude API usage is tracked in DynamoDB via `track-ai-usage.yml`. Monitor costs through the AWS console.

## Best Practices

### For Pull Requests

1. **Prepare Your PR**
   - Write clear PR descriptions
   - Keep changes focused and under 500 lines
   - Mark as draft until ready for review

2. **Request Reviews Wisely**
   - Use `/claude review` for complex logic changes
   - Skip for simple refactoring or documentation
   - Add context in your PR description

3. **Respond to Feedback**
   - Claude's suggestions are recommendations
   - Engage with specific questions using `@claude`
   - Mark resolved conversations

### For Interactive Use

1. **Be Specific**
   - Ask focused questions
   - Provide context about what you're trying to achieve
   - Reference specific files or functions

2. **Examples of Good Questions**
   ```
   @claude can you explain the error handling in src/api/handler.py?
   @claude what's the best way to add caching to this endpoint?
   @claude are there any security concerns with this authentication flow?
   ```

3. **Avoid**
   - Vague questions without context
   - Asking for complete implementations
   - Multiple unrelated questions in one comment

## Permissions and Security

- Claude workflows run with limited GitHub token permissions
- Cannot directly modify code or merge PRs
- All actions are logged and auditable
- Sensitive data should never be included in PR descriptions or comments

## Manual Controls

### Comment Commands
- `/claude review` - Trigger a code review
- `/claude cleanup` - Clean up old Claude comments
- `@claude` - Get interactive help

### Labels
- `claude-review-requested` - Force a review on any PR
- `skip-claude-review` - Prevent automatic reviews
- `claude-no-cleanup` - Preserve Claude comments

## Troubleshooting

### Review Not Triggering
1. Check PR is marked "ready for review"
2. Verify no `skip-claude-review` label
3. Ensure PR has code changes (not just docs)
4. Check workflow runs in Actions tab

### High Costs
1. Review PR sizes in recent history
2. Check for workflow loops or retries
3. Monitor usage in DynamoDB tracking
4. Consider implementing stricter size limits

### Poor Review Quality
1. Ensure PR has clear description
2. Add more context about changes
3. Use `@claude` for specific clarifications
4. Consider splitting complex PRs

## Configuration

### Environment Variables
- `CLAUDE_CODE_OAUTH_TOKEN` - Set in repository secrets (uses Claude subscription instead of API key)
- `GITHUB_TOKEN` - Automatically provided by GitHub Actions

### Customization
Workflow behavior can be customized by editing the workflow files in `.github/workflows/`. Always test changes in a separate branch first.

## Future Improvements

- [ ] Implement different models for different PR sizes
- [ ] Add support for incremental reviews on PR updates
- [ ] Create cost dashboards and alerts
- [ ] Integrate with project management tools
- [ ] Add support for different review depths

## Support

For issues with Claude workflows:
1. Check the [GitHub Actions logs](../../actions)
2. Review this documentation
3. Ask in the team's DevOps channel
4. Create an issue with the `claude-workflow` label
