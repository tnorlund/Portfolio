# 🤖 Dual AI Review System Guide

## Overview

This repository uses both **Cursor bot** and **Claude Code** for comprehensive PR reviews, providing both automated bug detection and architectural analysis.

## 🔄 Review Workflow

### 1. **PR Creation**
- Open PR with descriptive title and description
- Use the PR template checklist
- Mark as draft until ready for review

### 2. **Fast Validation (Automatic - 30 seconds)**
- ⚡ **Syntax checks**: Python compilation, basic linting
- ⚡ **Format validation**: Black, isort compatibility
- ⚡ **Change detection**: Skip AI reviews if no code changes
- **Gate**: Blocks AI reviews if basic validation fails

### 3. **Cursor Bot Review (If validation passes)**
- Triggers only after fast validation succeeds
- Focuses on: bugs, security, syntax, best practices
- Comments directly on problematic lines
- **Timeline**: Usually completes within 1-2 minutes

### 4. **Claude Code Review (If validation passes)**
- Triggers 30 seconds after Cursor (allows time to complete)
- Focuses on: architecture, performance, testing, documentation
- Posts comprehensive summary comment
- **Timeline**: Usually completes within 2-3 minutes

### 5. **Developer Response**
```bash
# Fix any fast validation issues first
git commit -m "fix: syntax and formatting issues"

# Address Cursor findings (critical bugs)
git commit -m "fix: address cursor bot findings"

# Then address Claude architectural recommendations
git commit -m "refactor: improve architecture per claude review"

# Push updates
git push
```

### 6. **Human Review**
- Reviews focus on business logic and requirements
- Both AI reviews provide context for human reviewers
- Use AI findings to guide manual review priorities

## 🎯 Review Focus Areas

### **Cursor Bot Strengths**
- ✅ **Syntax Errors**: Missing semicolons, typos, invalid syntax
- ✅ **Logic Bugs**: Operator precedence, null pointer issues
- ✅ **Security**: SQL injection, XSS, credential exposure
- ✅ **Performance**: N+1 queries, memory leaks
- ✅ **Best Practices**: Code smells, anti-patterns

### **Claude Code Strengths**
- ✅ **Architecture**: Design patterns, modularity, coupling
- ✅ **Performance**: System-level optimizations, caching
- ✅ **Testing**: Coverage, strategy, test quality
- ✅ **Documentation**: Completeness, clarity, maintainability
- ✅ **Context**: Understanding business requirements

## 📋 Using AI Reviews Effectively

### **For Developers**

**Before Opening PR:**
- [ ] Run tests locally
- [ ] Self-review code changes
- [ ] Write clear PR description
- [ ] Check that changes are focused

**After AI Reviews:**
1. **Address Cursor findings first** (usually blocking issues)
2. **Consider Claude recommendations** (architectural improvements)
3. **Update documentation** if architectural changes made
4. **Re-run tests** after changes

**Example Response Pattern:**
```markdown
## Addressing AI Review Findings

### Cursor Bot Issues ✅
- [x] Fixed operator precedence in condition (line 45)
- [x] Added null check for user input (line 67)
- [x] Removed unused import (line 12)

### Claude Code Recommendations 📋
- [x] Extracted common logic into utility function
- [x] Added documentation for complex algorithm
- [ ] Consider adding integration test (will do in follow-up PR)
```

### **For Reviewers**

**Priority Order:**
1. **Security Issues** (Cursor) - Block merge until fixed
2. **Logic Bugs** (Cursor) - Must be addressed
3. **Architecture Concerns** (Claude) - Discuss and decide
4. **Performance Suggestions** (Both) - Evaluate cost/benefit
5. **Style Issues** (Cursor) - Fix if easy, otherwise defer

**Review Checklist:**
- [ ] All Cursor security/bug findings addressed
- [ ] Claude architecture concerns discussed
- [ ] Test coverage adequate per Claude analysis
- [ ] Performance implications understood
- [ ] Documentation updated if needed

## 🛠️ Manual Controls

### **Triggering Reviews**
```bash
# Trigger Claude review manually
gh pr comment <pr_number> --body "/claude-review"

# Re-trigger after major changes
gh pr comment <pr_number> --body "/claude-review --force"
```

### **Skipping Reviews**
```bash
# Skip both AI reviews (emergency hotfix)
gh pr edit <pr_number> --add-label "skip-ai-review"

# Skip only Claude (keep Cursor for bugs)
gh pr edit <pr_number> --add-label "skip-claude-review"
```

### **Review Labels**
- `ai-review-complete` - Both reviews finished
- `cursor-findings-addressed` - Cursor issues resolved
- `claude-recommendations-reviewed` - Claude suggestions considered
- `skip-ai-review` - Skip all AI reviews
- `skip-claude-review` - Skip only Claude review

## 📊 Review Quality Tips

### **Writing Better PRs for AI Review**

**Good PR Descriptions:**
```markdown
## Summary
Refactor user authentication to use JWT tokens instead of session cookies.

## Context
Current session-based auth doesn't work well with our new microservice architecture.

## Changes
- Replaced session middleware with JWT validation
- Added token refresh endpoint
- Updated all auth-protected routes

## Testing
- Added unit tests for JWT validation
- Updated integration tests
- Manual testing in dev environment
```

**Poor PR Descriptions:**
```markdown
## Summary
Fix auth stuff

## Changes
- Updated some files
```

### **Getting Better AI Feedback**

**For Complex Changes:**
- Break large PRs into smaller, focused changes
- Explain the reasoning behind architectural decisions
- Call out areas where you want specific feedback

**For Bug Fixes:**
- Explain what was broken and how the fix works
- Include reproduction steps if applicable
- Mention any related issues or PRs

## 🔧 Troubleshooting

### **AI Review Not Triggering**
1. Check PR is not marked as draft
2. Verify no `skip-ai-review` label
3. Ensure PR has actual code changes
4. Check GitHub Actions status

### **Conflicting AI Recommendations**
1. **Security/Bugs**: Always follow Cursor (safety first)
2. **Architecture**: Consider Claude's broader context
3. **Style**: Follow project conventions
4. **Performance**: Measure and decide based on data

### **AI Review Taking Too Long**
- Cursor: Should complete in 1-2 minutes
- Claude: Should complete in 2-3 minutes
- If stuck, check GitHub Actions logs
- Can manually re-trigger with comments

## 📈 Success Metrics

Track these metrics to measure AI review effectiveness:

- **Bug Detection Rate**: Issues caught before human review
- **Review Time Reduction**: Faster human reviews due to AI prep
- **Code Quality**: Reduced post-merge bugs
- **Developer Satisfaction**: Helpful vs. noisy feedback ratio

## 🚀 Best Practices Summary

1. **Sequential Review**: Let Cursor finish, then Claude, then human
2. **Address Bugs First**: Security and logic issues are highest priority
3. **Consider Architecture**: Claude's suggestions improve long-term maintainability
4. **Update Documentation**: Keep docs in sync with architectural changes
5. **Measure Impact**: Track how AI reviews improve code quality

This dual AI approach ensures both immediate bug prevention and long-term architectural health of the codebase.