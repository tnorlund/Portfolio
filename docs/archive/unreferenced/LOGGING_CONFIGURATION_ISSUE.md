# Logging Configuration Issue - Validation Messages Not Appearing

## Problem

Validation log messages from `legacy_helpers.py` and `chromadb_client.py` are not appearing in CloudWatch logs, even though:
- ✅ The validation code is in the Docker image
- ✅ The code path is correct
- ✅ Other log messages from `line_polling.py` ARE appearing

## Root Cause

### Logger Configuration Mismatch

1. **Handler Logger** (`line_polling.py`):
   - Uses `utils.logging.get_logger()` which configures handlers
   - Logger name: `handlers.line_polling`
   - ✅ **Has handlers configured** → Messages appear in CloudWatch

2. **Receipt Label Loggers** (`legacy_helpers.py`, `chromadb_client.py`):
   - Use plain `logging.getLogger(__name__)`
   - Logger names: `receipt_label.vector_store.legacy_helpers`, `receipt_label.vector_store.client.chromadb_client`
   - ❌ **No handlers configured** → Messages don't appear in CloudWatch

### Code Evidence

**line_polling.py** (line 61):
```python
from utils.logging import get_logger, get_operation_logger
logger = get_operation_logger(__name__)  # ✅ Has handlers
```

**legacy_helpers.py** (line 22):
```python
import logging
logger = logging.getLogger(__name__)  # ❌ No handlers
```

**chromadb_client.py** (line 26):
```python
import logging
logger = logging.getLogger(__name__)  # ❌ No handlers
```

## Solution

Configure handlers for `receipt_label` loggers in the Lambda handler, similar to what's done in `enhanced_compaction_handler.py` (lines 186-206).

### Implementation

Add logger configuration in `line_polling.py::handle()` or `_handle_internal()`:

```python
import logging
import os

# Configure receipt_label loggers to output to CloudWatch
receipt_label_logger = logging.getLogger("receipt_label")
log_level = os.environ.get("LOG_LEVEL", "INFO")

if not receipt_label_logger.handlers:
    handler = logging.StreamHandler()

    # Use structured JSON formatter if available
    try:
        from utils.logging import StructuredFormatter
        formatter = StructuredFormatter()
    except ImportError:
        formatter = logging.Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)03dZ %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    receipt_label_logger.addHandler(handler)
    receipt_label_logger.setLevel(log_level)
    receipt_label_logger.propagate = False  # Prevent duplicate logs
```

### Reference Implementation

See `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py` lines 186-206 for a working example.

## Impact

Once this is fixed, we should see:
- ✅ "Validating uploaded delta..." messages
- ✅ "Delta validation successful" messages
- ✅ "Successfully uploaded and validated delta to S3" messages
- ✅ Any error messages from validation failures

## Verification

After deploying the fix, check CloudWatch logs for:
1. Validation messages appearing during delta creation
2. Confirmation that validation is actually running
3. Any validation failures being properly logged

