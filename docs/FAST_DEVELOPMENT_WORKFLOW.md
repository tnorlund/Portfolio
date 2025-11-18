# Fast Development Workflow for Step Functions

## Problem
Deploying to AWS and testing end-to-end takes 10-20 minutes per iteration, which is too slow for rapid development.

## Solution: Multi-Layer Testing Strategy

### 1. **Local Lambda Handler Testing** (Fastest - < 1 second)

Test individual Lambda handlers locally with mocked dependencies:

```python
# infra/embedding_step_functions/unified_embedding/handlers/tests/test_split_into_chunks.py
import json
import os
from unittest.mock import patch, MagicMock
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from split_into_chunks import handle

def test_split_into_chunks_small_payload():
    """Test split_into_chunks with small payload (returns inline)."""
    event = {
        "batch_id": "test-batch-123",
        "poll_results": [
            {"delta_key": "delta/1/", "collection": "lines"},
            {"delta_key": "delta/2/", "collection": "lines"},
        ],
        "poll_results_s3_key": None,
        "poll_results_s3_bucket": None,
    }

    with patch.dict(os.environ, {"CHROMADB_BUCKET": "test-bucket"}):
        with patch('split_into_chunks.s3_client') as mock_s3:
            result = handle(event, None)

            assert result["batch_id"] == "test-batch-123"
            assert result["total_chunks"] == 1
            assert result["use_s3"] == False
            assert "chunks" in result
            assert result["poll_results_s3_key"] is None  # Pass through check

def test_split_into_chunks_large_payload():
    """Test split_into_chunks with large payload (uploads to S3)."""
    # Create 100 deltas to trigger S3 upload
    large_poll_results = [
        {"delta_key": f"delta/{i}/", "collection": "lines"}
        for i in range(100)
    ]

    event = {
        "batch_id": "test-batch-456",
        "poll_results": large_poll_results,
        "poll_results_s3_key": None,
        "poll_results_s3_bucket": None,
    }

    with patch.dict(os.environ, {"CHROMADB_BUCKET": "test-bucket"}):
        with patch('split_into_chunks.s3_client') as mock_s3:
            result = handle(event, None)

            assert result["use_s3"] == True
            assert "chunks_s3_key" in result
            assert result["poll_results_s3_key"] is None  # Pass through check
            # Verify S3 upload was called
            assert mock_s3.upload_file.called

def test_split_into_chunks_passes_through_s3_keys():
    """Test that poll_results_s3_key is passed through correctly."""
    event = {
        "batch_id": "test-batch-789",
        "poll_results": None,  # Using S3
        "poll_results_s3_key": "poll_results/batch-789/poll_results.json",
        "poll_results_s3_bucket": "test-bucket",
    }

    with patch.dict(os.environ, {"CHROMADB_BUCKET": "test-bucket"}):
        with patch('split_into_chunks.s3_client') as mock_s3:
            # Mock S3 download
            mock_s3.download_file.return_value = None
            with patch('builtins.open', create=True) as mock_open:
                mock_open.return_value.__enter__.return_value.read.return_value = json.dumps([
                    {"delta_key": "delta/1/", "collection": "lines"}
                ])

                result = handle(event, None)

                # Verify poll_results_s3_key is passed through
                assert result["poll_results_s3_key"] == "poll_results/batch-789/poll_results.json"
                assert result["poll_results_s3_bucket"] == "test-bucket"
```

**Run tests:**
```bash
cd infra/embedding_step_functions/unified_embedding/handlers
python -m pytest tests/test_split_into_chunks.py -v
```

### 2. **Step Function Definition Validation** (Fast - < 5 seconds)

Validate Step Function JSON syntax without deploying:

```python
# scripts/validate_step_function.py
import json
import sys
from pathlib import Path

def validate_step_function_definition(definition_path: str):
    """Validate Step Function definition JSON syntax."""
    with open(definition_path, 'r') as f:
        definition = json.load(f)

    # Basic validation
    assert "StartAt" in definition, "Missing StartAt"
    assert "States" in definition, "Missing States"

    # Validate all states
    start_state = definition["StartAt"]
    visited = set()

    def validate_state(state_name: str, state_def: dict):
        if state_name in visited:
            return  # Already validated
        visited.add(state_name)

        assert "Type" in state_def, f"State {state_name} missing Type"

        state_type = state_def["Type"]

        # Validate transitions
        if "Next" in state_def:
            next_state = state_def["Next"]
            assert next_state in definition["States"], \
                f"State {state_name} references non-existent state: {next_state}"
            validate_state(next_state, definition["States"][next_state])

        # Validate catch blocks
        if "Catch" in state_def:
            for catch in state_def["Catch"]:
                if "Next" in catch:
                    next_state = catch["Next"]
                    assert next_state in definition["States"], \
                        f"Catch in {state_name} references non-existent state: {next_state}"
                    validate_state(next_state, definition["States"][next_state])

        # Validate choice states
        if state_type == "Choice":
            assert "Choices" in state_def, f"Choice state {state_name} missing Choices"
            for choice in state_def["Choices"]:
                if "Next" in choice:
                    next_state = choice["Next"]
                    assert next_state in definition["States"], \
                        f"Choice in {state_name} references non-existent state: {next_state}"
                    validate_state(next_state, definition["States"][next_state])
            if "Default" in state_def:
                default_state = state_def["Default"]
                assert default_state in definition["States"], \
                    f"Choice state {state_name} default references non-existent state: {default_state}"
                validate_state(default_state, definition["States"][default_state])

    # Start validation from StartAt
    validate_state(start_state, definition["States"][start_state])

    print(f"✅ Step Function definition is valid: {len(visited)} states")
    return True

if __name__ == "__main__":
    # Get definition from Pulumi component
    # This would need to be extracted from the Python code
    # For now, we can test by importing the component
    sys.path.insert(0, str(Path(__file__).parent.parent))

    # Import and generate definition
    from infra.embedding_step_functions.components.line_workflow import LineEmbeddingWorkflow
    # ... extract definition and validate

    print("✅ Step Function validation passed")
```

### 3. **JSONPath Validation** (Fast - < 1 second)

Validate JSONPath expressions match expected data structures:

```python
# scripts/validate_jsonpath.py
import json
from jsonpath_ng import parse

def validate_jsonpath(jsonpath_expr: str, sample_data: dict):
    """Validate that a JSONPath expression works with sample data."""
    try:
        jsonpath = parse(jsonpath_expr)
        matches = [match.value for match in jsonpath.find(sample_data)]
        return matches
    except Exception as e:
        raise ValueError(f"Invalid JSONPath '{jsonpath_expr}': {e}")

# Test the GroupChunksForMerge fix
sample_input = {
    "chunked_data": {
        "batch_id": "test-123",
        "total_chunks": 5,
    },
    "poll_results_data": {
        "poll_results_s3_key": "poll_results/test-123/poll_results.json",
        "poll_results_s3_bucket": "test-bucket",
    },
    "chunk_results": [],
    "poll_results": [],
}

# Test old (broken) path
try:
    result = validate_jsonpath("$.chunked_data.poll_results_s3_key", sample_input)
    print(f"❌ Old path returned: {result} (should be empty)")
except Exception as e:
    print(f"✅ Old path correctly fails: {e}")

# Test new (fixed) path
result = validate_jsonpath("$.poll_results_data.poll_results_s3_key", sample_input)
assert result == ["poll_results/test-123/poll_results.json"]
print(f"✅ New path works: {result}")
```

### 4. **Incremental Deployment** (Medium - 2-5 minutes)

Only deploy what changed:

```bash
# Deploy only Step Functions (not Lambdas)
pulumi up --target urn:pulumi:dev::portfolio::aws:sfn/stateMachine:StateMachine::line-ingest-sf-dev

# Or deploy only specific Lambda
pulumi up --target urn:pulumi:dev::portfolio::aws:lambda/function:Function::embedding-vector-compact-lambda-dev
```

### 5. **LocalStack / Moto for AWS Services** (Medium - 5-10 minutes)

Test with local AWS service mocks:

```python
# tests/integration/test_step_function_flow.py
import boto3
from moto import mock_s3, mock_stepfunctions, mock_lambda
import json

@mock_s3
@mock_stepfunctions
@mock_lambda
def test_step_function_flow():
    """Test Step Function flow with mocked AWS services."""
    # Setup S3
    s3 = boto3.client('s3', region_name='us-east-1')
    s3.create_bucket(Bucket='test-bucket')

    # Upload test manifest
    manifest = {
        "batches": [
            {"batch_id": "batch-1", "openai_batch_id": "batch_xxx"}
        ]
    }
    s3.put_object(
        Bucket='test-bucket',
        Key='manifest.json',
        Body=json.dumps(manifest)
    )

    # Test Lambda handler with S3 manifest
    from handlers.line_polling import handle

    event = {
        "batch_index": 0,
        "manifest_s3_key": "manifest.json",
        "manifest_s3_bucket": "test-bucket",
    }

    result = handle(event, None)
    assert result["batch_id"] == "batch-1"
```

## Recommended Workflow

### For Step Function Definition Changes:

1. **Validate JSON syntax** (1 sec)
   ```bash
   python scripts/validate_step_function.py
   ```

2. **Validate JSONPath expressions** (1 sec)
   ```bash
   python scripts/validate_jsonpath.py
   ```

3. **Deploy only Step Function** (2-3 min)
   ```bash
   pulumi up --target <step-function-urn>
   ```

4. **Test with real execution** (5-10 min)
   ```bash
   aws stepfunctions start-execution --state-machine-arn <arn> --input '{}'
   ```

### For Lambda Handler Changes:

1. **Write unit test** (5 min)
   ```bash
   python -m pytest tests/test_<handler>.py -v
   ```

2. **Test locally** (1 sec)
   ```bash
   python -m pytest tests/test_<handler>.py::test_specific_case -v
   ```

3. **Deploy only Lambda** (3-5 min)
   ```bash
   pulumi up --target <lambda-urn>
   ```

4. **Test with real execution** (2-5 min)
   ```bash
   aws lambda invoke --function-name <name> --payload '{}' response.json
   ```

## Quick Test Scripts

Create these helper scripts:

```bash
# scripts/test_handler_locally.sh
#!/bin/bash
# Test a Lambda handler locally with sample event

HANDLER=$1
EVENT_FILE=$2

python -c "
import json
import sys
sys.path.insert(0, 'infra/embedding_step_functions/unified_embedding/handlers')
from $HANDLER import handle

with open('$EVENT_FILE') as f:
    event = json.load(f)

result = handle(event, None)
print(json.dumps(result, indent=2))
"
```

```bash
# scripts/validate_step_function_jsonpath.sh
#!/bin/bash
# Validate all JSONPath expressions in step function definition

python scripts/validate_jsonpath.py
```

## Benefits

- **10-20x faster**: Local tests run in seconds vs minutes
- **Catch errors early**: Syntax and logic errors before deployment
- **Iterate faster**: Test → Fix → Test cycle in < 1 minute
- **Confidence**: Deploy knowing the code works
- **Cost savings**: Fewer failed AWS executions

## Next Steps

1. Create unit tests for `split_into_chunks`, `normalize_poll_batches_data`, `GroupChunksForMerge` logic
2. Add JSONPath validation script
3. Add Step Function definition validator
4. Create local test fixtures with sample events

