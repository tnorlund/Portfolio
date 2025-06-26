# MCP Development Server

This repository includes a simple Model Context Protocol server for running
common development tasks.

Start the server from the repository root:

```bash
python mcp_server.py
```

The server exposes nine tools:

### Development Tools

- `run_tests` &ndash; run pytest for a specific package (`receipt_label`,
  `receipt_dynamo`, etc.) using all available CPU cores.
- `run_lint` &ndash; format code with `isort` and `black` and then run `pylint`
  and `mypy` for the package.

### Pulumi Infrastructure Tools

- `pulumi_preview` &ndash; run `pulumi preview` in the infra directory to see
  planned infrastructure changes. Optionally specify a stack and additional args.
- `pulumi_up` &ndash; run `pulumi up` in the infra directory to apply
  infrastructure changes. Optionally specify a stack and additional args.
- `pulumi_stack_list` &ndash; list all available Pulumi stacks.
- `pulumi_stack_output` &ndash; get outputs from a Pulumi stack. Optionally
  specify the stack name and a specific output name.
- `pulumi_refresh` &ndash; refresh Pulumi state to match actual infrastructure.
  Optionally specify a stack and additional args.
- `pulumi_logs` &ndash; get logs from the last Pulumi operation. Optionally
  specify a stack and additional args.
- `pulumi_config_get` &ndash; get a configuration value from Pulumi. Requires
  a key parameter, optionally specify a stack.

The test and lint tools execute commands inside the package directory so
configuration files are discovered correctly. All Pulumi tools execute in the
infra directory.

## Worktree Virtual Environments

When working on multiple branches using Git worktrees, create a separate
virtual environment in each worktree directory. This keeps package
installations isolated and prevents interference between branches.

Example:

```bash
git worktree add ../feature-branch feature-branch
cd ../feature-branch
python -m venv .venv
source .venv/bin/activate
```

Activate the worktree's environment before running `python mcp_server.py` or
other development commands.
# Test Optimization and Execution

## Advanced Test Tools

This repository includes comprehensive test optimization tools for maximum performance:

### Quick Local Testing
```bash
# Recommended: Use the optimized test runner
./scripts/test_runner.sh receipt_dynamo
./scripts/test_runner.sh -t integration -c receipt_dynamo

# Advanced: Use the intelligent test runner
python scripts/run_tests_optimized.py receipt_dynamo tests/unit --test-type unit
```

### Test Analysis and Optimization
```bash
# Analyze test structure and generate optimal parallel groups
python scripts/analyze_tests.py

# Generate dynamic GitHub Actions matrix
python scripts/generate_test_matrix.py

# Identify performance bottlenecks
python scripts/optimize_slow_tests.py
```

### Performance Impact
- **Integration tests**: 39 files, 1,579 tests split into 4 optimal parallel groups
- **Speedup**: 62.8min → 15.8min execution time (4x faster)
- **Load balancing**: Each group has ~395 tests, ~16min execution time

See `scripts/README.md` for detailed documentation of all test optimization tools.

# End-to-End Tests

**IMPORTANT**: The `receipt_dynamo/tests/end_to_end` directory contains end-to-end tests that connect to REAL AWS services.

These tests:
- Require AWS credentials and internet connectivity
- Connect to actual DynamoDB tables and S3 buckets
- May incur AWS costs
- Should NOT be run automatically during code review or CI

To run end-to-end tests:
1. Set up AWS credentials (`aws configure` or environment variables)
2. Use `pulumi stack select tnorlund/portfolio/dev` to access configuration
3. Run with `pytest -m end_to_end`

Always skip these tests during normal development: `pytest -m "not end_to_end"`

See `receipt_dynamo/tests/end_to_end/README.md` for detailed setup instructions.

# Lambda Architecture Configuration

**IMPORTANT**: All Lambda functions in this project must use ARM64 architecture to match the Lambda layers.

The Lambda layers are built using ARM64 architecture (see `infra/fast_lambda_layer.py`). When creating or updating Lambda functions, always include:

```python
architectures=["arm64"],
```

This prevents architecture mismatch errors like "No module named 'pydantic_core._pydantic_core'" which occur when x86_64 Lambdas try to load ARM64-compiled C extensions.

Example:
```python
lambda_function = Function(
    "my-function",
    runtime="python3.12",
    architectures=["arm64"],  # Required for compatibility with layers
    handler="handler.main",
    layers=[dynamo_layer.arn, label_layer.arn],
    # ... other configuration
)
```

# CI/CD Workflow Guidelines

## Understanding PR Status Checks

**IMPORTANT**: The "PR Status" check in our CI/CD pipeline is designed to fail when code requires auto-formatting, even if the code ends up properly formatted. This is intentional behavior to encourage developers to format code locally before pushing.

### How It Works

1. **Format Check**: Runs `black` and `isort` to check if code needs formatting
2. **Auto-Format**: If formatting is needed, the workflow automatically formats and commits the changes
3. **PR Status**: FAILS if auto-formatting was needed (even though code is now correct)

### Why This Matters

This design prevents "formatting thrash" where developers push unformatted code and rely on CI to fix it. The failing PR Status serves as a reminder to run formatters locally.

### Best Practices to Avoid CI Distractions

1. **Always format locally before pushing**:
   ```bash
   make format  # or
   black . && isort .
   ```

2. **When you see formatting failures**:
   - Check WHICH files need formatting (look at the specific error message)
   - Only fix files that are part of your PR
   - Ignore formatting issues in unrelated files

3. **Maintain PR scope discipline**:
   - Don't fix issues outside your PR's scope
   - If you see unrelated formatting issues, create a separate PR
   - Focus only on your intended changes

4. **Understanding "failed" PR Status**:
   - A failed PR Status due to auto-formatting is not a blocker for merging
   - It's a gentle reminder for next time
   - Feature branches can be merged despite this "failure"

### Common Pitfall: The Formatting Rabbit Hole

**Scenario**: Your PR fails due to formatting, you start fixing it, then find more files need formatting, and suddenly you're reformatting half the codebase.

**Solution**:
- STOP and check which files are actually part of your PR
- Only format files you've modified
- Let other files remain as they are

### Example Workflow

```bash
# Before pushing your changes
git status  # Check which files you've modified
make format  # Format everything
git add -p  # Selectively add only YOUR changes
git commit -m "Add new feature"
git push

# If CI still complains about formatting in OTHER files:
# IGNORE IT - those aren't your responsibility
```

# Boto3 Type Safety

**IMPORTANT**: This project uses boto3 type stubs for improved developer experience without runtime overhead.

## Implementation

We use the TYPE_CHECKING pattern to import boto3 type stubs only during development:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_s3 import S3Client
```

## Key Points

1. **Dev Dependencies**: `boto3-stubs[dynamodb,s3]` is in `[dev]` extras, not required at runtime
2. **Zero Runtime Impact**: TYPE_CHECKING imports are skipped during execution
3. **Full IDE Support**: Developers get autocomplete, parameter hints, and type checking
4. **CI Compatibility**: Tests run without dev dependencies installed

## Adding New AWS Services

When adding boto3 clients for new AWS services:
1. Add the service to boto3-stubs extras: `boto3-stubs[dynamodb,s3,new-service]`
2. Use TYPE_CHECKING pattern for imports
3. Add type annotations to client variables: `client: ServiceClient = boto3.client("service")`

This approach provides type safety during development while maintaining fast runtime performance.
EOF < /dev/null
