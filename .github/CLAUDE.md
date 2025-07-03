# Claude AI Workflow Guidelines

This document provides guidelines and best practices for using Claude AI workflows in this repository.

## Overview

We use Claude AI for automated code reviews and interactive assistance to improve code quality and developer productivity. This document outlines how to effectively use these tools while managing costs.

## Available Claude Workflows

### 1. Automated Code Review (`claude-review.yml`)
Automatically reviews pull requests for code quality, bugs, and best practices.

**Triggers:**
- PR marked as "ready for review"
- PR labeled with `claude-review-requested`
- Manual: Comment `/claude review` on any PR

**Cost Optimization:**
- Automatically skips PRs over 1000 lines (unless explicitly requested)
- Ignores non-code files (.md, .json, .yaml, etc.)
- Add `skip-claude-review` label to bypass review

### 2. Interactive Assistant (`claude.yml`)
Provides on-demand AI assistance for questions and code help.

**Usage:**
- Mention `@claude` in any issue or PR comment
- Example: `@claude can you explain how this function works?`

### 3. Enhanced Review (`claude-review-enhanced.yml`)
Advanced review features with comment management and cleanup.

**Features:**
- Collapses outdated reviews automatically
- Provides review summaries
- Cleans up after PR merge

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
- `ANTHROPIC_API_KEY` - Set in repository secrets
- `CLAUDE_MODEL` - Currently using `claude-3-opus`
- `MAX_PR_SIZE` - Default 1000 lines

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
