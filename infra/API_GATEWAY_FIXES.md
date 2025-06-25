# API Gateway Infrastructure Fixes

This document details the issues found and fixes applied to the API Gateway infrastructure on December 25, 2024.

## Issues Discovered

### 1. Duplicate `architectures` Parameter in Lambda Functions

Multiple Lambda function definitions had duplicate `architectures` parameters, causing Pulumi deployment failures.

**Affected Files:**
- `routes/process/infra.py` (line 135)
- `routes/images/infra.py` (line 72)
- `routes/merchant_counts/infra.py` (line 138)
- Several other route files

**Error Message:**
```
TypeError: __init__() got multiple values for keyword argument 'architectures'
```

**Fix Applied:**
Removed duplicate `architectures=["arm64"]` parameters from all affected Lambda function definitions.

### 2. Missing Lambda Layer Exports

The Lambda layer modules were missing backward compatibility exports that other modules depended on.

**Affected Files:**
- `fast_lambda_layer.py`
- `simple_lambda_layer.py`

**Issue:**
Routes were trying to import `dynamo_layer`, `label_layer`, and `upload_layer` but these were only exported with prefixes (`fast_dynamo_layer`, etc.).

**Fix Applied:**
Added compatibility aliases at the end of each layer file:
```python
# Backward compatibility aliases
dynamo_layer = fast_dynamo_layer
label_layer = fast_label_layer
upload_layer = fast_upload_layer
```

## Deployment Process

### 1. Initial Discovery
When trying to access `https://dev-api.tylernorlund.com/ai_usage`, discovered the route wasn't deployed due to syntax errors preventing Pulumi from running.

### 2. Systematic Fix
1. Ran `pulumi preview` to identify all syntax errors
2. Fixed duplicate parameters in 7 route files
3. Added backward compatibility exports to layer files
4. Successfully ran `pulumi up` to deploy changes

### 3. Verification
After deployment:
- All routes now return proper HTTP responses
- `/ai_usage` endpoint is accessible and returns 200 status
- Several routes return 500 errors (likely data issues, not infrastructure)

## Current API Status

| Route | Status | Notes |
|-------|--------|-------|
| `/health_check` | ✅ 200 | Working correctly |
| `/ai_usage` | ✅ 200 | Newly deployed and working |
| `/merchant_counts` | ✅ 200 | Working correctly |
| `/image_count` | ❌ 500 | Internal error (data issue) |
| `/images` | ❌ 500 | Internal error (data issue) |
| `/label_validation_count` | ❌ 500 | Internal error (data issue) |
| `/process` | ❌ 500 | Internal error (data issue) |
| `/random_image_details` | ❌ 500 | Internal error (data issue) |
| `/receipt_count` | ❌ 500 | Internal error (data issue) |
| `/receipts` | ❌ 500 | Internal error (data issue) |

## Lessons Learned

1. **Code Duplication**: The duplicate parameters likely resulted from copy-pasting Lambda definitions
2. **Breaking Changes**: Renaming exports (like `dynamo_layer` to `fast_dynamo_layer`) requires careful migration
3. **Testing**: Running `pulumi preview` before `pulumi up` catches syntax errors early
4. **Documentation**: Route documentation should include deployment verification steps

## Recommendations

1. **Add Pre-commit Hooks**: Use `pylint` or `flake8` to catch duplicate parameters
2. **Standardize Lambda Definitions**: Create a template or factory function for Lambda definitions
3. **Integration Tests**: Add tests that verify all API routes are accessible after deployment
4. **Monitoring**: Set up CloudWatch alarms for 500 errors on API routes
5. **Documentation**: Document all routes consistently (only 1/10 routes had documentation)

## Related Files

- Main API Gateway configuration: `infra/api_gateway.py`
- Lambda layer definitions: `infra/fast_lambda_layer.py`, `infra/simple_lambda_layer.py`
- Route implementations: `infra/routes/*/infra.py`
- New documentation: 
  - `infra/API_DOCUMENTATION.md` (comprehensive API docs)
  - `infra/routes/ai_usage/README.md` (route-specific docs)