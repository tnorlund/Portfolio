# Reformat receipt_upload Package to Match PR #135 Standards

## Background
The receipt_upload package was successfully reformatted in PR #125, achieving a pylint score of 9.91/10. However, based on learnings from PR #135's over-engineering of receipt_dynamo, we should apply a simpler, more efficient approach to any remaining formatting needs.

## Current State
From PR #125, the receipt_upload package has:
- **Pylint score**: 9.91/10 (up from 9.03/10)
- **Code organization**: Split 1374-line geometry.py into 5 focused modules
- **Import sorting**: Fixed with isort
- **Code formatting**: Applied black formatting
- **Complexity reduction**: Refactored nested DBSCAN algorithm

## Remaining Issues
Per PR #125, the following architectural issues remain:
- Line length violations in geometry/ modules (61 instances) - complex mathematical expressions
- Functions with too many statements (would benefit from further decomposition)
- Functions with too many positional arguments (could use dataclasses/named tuples)

## Proposed Approach

### 1. Quick Assessment (15 minutes)
```bash
cd receipt_upload
pylint receipt_upload --score=y
black --check receipt_upload/
isort --check-only receipt_upload/
mypy receipt_upload/
```

### 2. Simple Fixes (if needed)
```bash
# Auto-format
black receipt_upload/
isort receipt_upload/

# Run tests to ensure no breakage
pytest tests/
```

### 3. Document Results
- Before/after pylint scores
- Number of files changed
- Any functional improvements made

## Success Criteria
- Maintain or improve 9.91/10 pylint score
- All tests pass
- Single PR with all changes
- Complete in <2 hours

## What NOT to Do
Based on PR #135 lessons:
- ❌ No multi-phase strategies
- ❌ No parallel execution plans
- ❌ No custom tooling or scripts
- ❌ No extensive documentation
- ❌ No multiple PRs

## References
- PR #125: Successful receipt_upload reformatting
- PR #135: Over-engineered receipt_dynamo approach (avoid this pattern)
- Analysis: `/spec/PR135-receipt_dynamo-analysis.md`
- Lessons learned: `/spec/issue-linting-overengineering.md`

## Acceptance Criteria
- [ ] Run linting assessment
- [ ] Apply minimal necessary fixes
- [ ] Ensure all tests pass
- [ ] Single PR with clear metrics
- [ ] Time spent <2 hours

## Labels
- `enhancement`
- `code-quality`
- `receipt-upload`
- `good-first-issue`
