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
