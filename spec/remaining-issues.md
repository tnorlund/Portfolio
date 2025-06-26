# Remaining Issues

## Overview

This document tracks the remaining minor issues identified during the BugBot remediation effort. Most critical issues have been resolved, but one minor issue remains.

## Outstanding Issues

### 1. Environment Variable Naming Conflicts (MEDIUM PRIORITY)

**Status**: ❌ UNRESOLVED
**Impact**: LOW - Functional but inconsistent
**Effort**: 1-2 hours

**Problem**: Inconsistent environment variable naming throughout the codebase:
- `ClientManager.from_env()` uses `"DYNAMO_TABLE_NAME"` (line 37)
- `AIUsageTracker` uses `"DYNAMODB_TABLE_NAME"` (line 97)
- Multiple configuration sources with different names

**Files Affected**:
- `receipt_label/receipt_label/utils/client_manager.py`
- `receipt_label/receipt_label/utils/ai_usage_tracker.py`
- Any environment configuration files

**Solution**:
1. **Standardize on `DYNAMODB_TABLE_NAME`** (more descriptive and AWS-consistent)
2. Update `ClientManager.from_env()` to use `DYNAMODB_TABLE_NAME`
3. Maintain backward compatibility by checking both variables with deprecation warning
4. Update documentation and examples

**Implementation Plan**:
```python
# In ClientManager.from_env()
dynamo_table = (
    os.environ.get("DYNAMODB_TABLE_NAME") or
    os.environ.get("DYNAMO_TABLE_NAME")  # Deprecated fallback
)
if "DYNAMO_TABLE_NAME" in os.environ and "DYNAMODB_TABLE_NAME" not in os.environ:
    warnings.warn(
        "DYNAMO_TABLE_NAME is deprecated. Use DYNAMODB_TABLE_NAME instead.",
        DeprecationWarning,
        stacklevel=2
    )
```

**Testing Required**:
- Verify both variable names work during transition period
- Test deprecation warning is triggered correctly
- Update integration tests to use new variable name

## Resolved Issues ✅

### Major Accomplishments
1. **Architectural Violations** - All resilience patterns moved to correct package ✅
2. **Thread Safety Issues** - Context managers replace manual lock management ✅
3. **DynamoDB Key Mismatches** - Consistent camelCase naming ✅
4. **Resilient Client Bypass** - Robust test environment detection ✅
5. **Test Environment Pollution** - Specific pattern matching prevents false positives ✅
6. **Dead Code** - All query methods are functional ✅
7. **Client Detection Logic** - Probe-based detection without private attributes ✅

## Summary

**BugBot Remediation: 7/8 Issues Resolved (87.5% Complete)**

The system is now in excellent shape with proper:
- Package boundaries and architectural compliance
- Thread-safe concurrency patterns
- Robust environment detection
- Clean abstraction without encapsulation violations

The remaining environment variable naming issue is minor and can be addressed during routine maintenance.

**Next Steps**: Address environment variable naming in next maintenance cycle or when convenient.
