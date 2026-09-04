# receipt_dynamo/ (DynamoDB data layer)

Deltas to the root `AGENTS.md`.

- This package owns every DynamoDB call, retry, batch, and resilience pattern.
  It never imports other `receipt_*` packages. Other packages use
  `DynamoClient` methods; if a method is missing, add it here.
- Entities: `receipt_dynamo/entities/<entity>.py` (dataclass, `to_item()`,
  `item_to_<entity>()`). Accessors: `receipt_dynamo/data/_<entity>.py` mixins
  composed into `DynamoClient`. Copy the structure of a neighbouring
  entity/accessor pair when adding a new one.
- Errors: raise the types in `receipt_dynamo/data/shared_exceptions.py`
  (`EntityAlreadyExistsError`, `EntityNotFoundError`, `EntityValidationError`,
  `OperationError`, `DynamoDBThroughputError`, `DynamoDBServerError`,
  `DynamoDBError`). Never leak `botocore.ClientError` or raise bare
  `ValueError` from accessors.
- Tests: `pytest receipt_dynamo/tests -m unit` for fast checks;
  `-m integration` runs the moto-backed suite (`dynamodb_table` fixture);
  `-m end_to_end` needs AWS and is skipped by agents. Entities not used by
  `infra/` are marked `unused_in_production`. Test conventions and the
  ClientError → exception mapping are in the `receipt-dynamo-integration-tests`
  skill.
- `make lint` runs `mypy` and `pylint` on this package only; keep new code clean
  under both.
