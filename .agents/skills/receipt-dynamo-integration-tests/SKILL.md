---
name: receipt-dynamo-integration-tests
description: >-
  Write or fix receipt_dynamo integration tests against the moto DynamoDB
  fixture. Use when adding an entity accessor, updating tests under
  receipt_dynamo/tests/integration/, or matching DynamoDB ClientError codes to
  the package's exception types.
---

# receipt_dynamo integration tests

Tests run offline against the `dynamodb_table` moto fixture
(`Literal["MyMockedTable"]`). Every test function is type-annotated and
returns `None`.

## Exception mapping

`ClientError` codes map to `receipt_dynamo.data.shared_exceptions` types:

- `ConditionalCheckFailedException` → `EntityAlreadyExistsError` on `add_*`;
  `EntityNotFoundError` on `update_*` and `delete_*`.
- `ValidationException` → `EntityValidationError`.
- `ResourceNotFoundException` → `OperationError`.
- `ProvisionedThroughputExceededException`, `ThrottlingException` → `DynamoDBThroughputError`.
- `InternalServerError`, `ServiceUnavailable` → `DynamoDBServerError`.
- `AccessDeniedException` and anything else → `DynamoDBError`.

Assert on these types, never on `ValueError`, and match the real message
substring (`"already exists"`, `"not found"`, `"item cannot be None"`,
`"items must be a list of <Entity> objects"`).

## Standard imports

```python
"""Integration tests for <Entity> operations in DynamoDB."""
import time
from datetime import datetime
from typing import List, Literal, Type
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.<entity_module> import <Entity>
```

## Coverage each entity needs

- CRUD: add success, add duplicate raises, get missing returns `None`,
  update/delete missing raise `EntityNotFoundError`.
- Batch: `add_<entities>` for a small list and for 100 items (spot-check a sample).
- Validation: `None` and wrong-type arguments raise `EntityValidationError`.
- Error handling: one parametrized test per operation that patches
  `client._client.<api>` with `ClientError({"Error": {"Code": code, ...}}, "PutItem")`
  and asserts the mapped exception. Define the scenarios once as an
  `ERROR_SCENARIOS` list of `(code, exception_type, message_match)` tuples.
- List/query: pagination with `limit` and `last_evaluated_key`, no overlap
  between pages.
- Edge cases: unicode and special characters, zero/max numeric values, empty
  batch lists.

Fixtures return fully populated entities; use `datetime.now().isoformat()` for
timestamps and `int(time.time()) + 3600` for TTLs. Full examples of each pattern
are in `references/patterns.md`.

## Running

```bash
cd receipt_dynamo
pytest tests/integration/test__<entity>.py -v
pytest tests/integration -m "integration and not unused_in_production"
```

Entities not used by `infra/` are marked `unused_in_production`; do not spend
effort on them unless asked. Reference PRs for the current patterns: #283,
#284, #285, #287.
