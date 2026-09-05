"""Single-owner DynamoDB access with atomic edits, clock, and retry receipts."""

import copy
import hashlib
import json
import time
from typing import Callable

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from planner.entities import PlannerRecord
from planner.errors import Conflict, ValidationError

COLLECTIONS = {
    "AREA": "areas",
    "ITEM": "items",
    "ROUTINE": "routines",
    "PROPOSAL": "proposals",
}


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def empty_state() -> dict:
    return {
        "version": 0,
        "content_version": 0,
        "areas": [],
        "items": [],
        "routines": [],
        "weeks": {},
        "proposals": [],
        "preferences": {
            "daily_minutes": 180,
            "timezone": "America/Los_Angeles",
        },
    }


def table_definition(name: str) -> dict:
    return {
        "TableName": name,
        "BillingMode": "PAY_PER_REQUEST",
        "AttributeDefinitions": [
            {"AttributeName": x, "AttributeType": "S"}
            for x in ("PK", "SK", "TYPE")
        ],
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "GSITYPE",
                "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    }


class DynamoClient:
    def __init__(
        self, table_name: str, region: str = "us-east-1", *, client=None
    ):
        if not isinstance(table_name, str) or not table_name:
            raise ValueError("Pass an explicit planner table name")
        self.table_name = table_name
        self._client = client or boto3.client(
            "dynamodb",
            region_name=region,
            config=Config(retries={"mode": "standard", "max_attempts": 5}),
        )

    def _get(self, key: dict) -> dict | None:
        return self._client.get_item(
            TableName=self.table_name, Key=key, ConsistentRead=True
        ).get("Item")

    def get_clock(self) -> int:
        item = self._get(PlannerRecord("CONFIG", "CLOCK", {}).key)
        return int(item["version"]["N"]) if item else 0

    def read(self) -> dict:
        # Bracket paginated strongly consistent queries with the atomic clock.
        # A writer crossing the read forces a fresh snapshot, never a mixed view.
        for _ in range(6):
            version = self.get_clock()
            state = empty_state()
            cursor = None
            while True:
                args = {
                    "TableName": self.table_name,
                    "KeyConditionExpression": "PK = :pk",
                    "ExpressionAttributeValues": {":pk": {"S": "PLANNER"}},
                    "ConsistentRead": True,
                }
                if cursor:
                    args["ExclusiveStartKey"] = cursor
                page = self._client.query(**args)
                for raw in page.get("Items", []):
                    record = PlannerRecord.from_item(raw)
                    if record.record_type == "CONFIG":
                        state.update(record.fields)
                    elif record.record_type == "WEEK":
                        state["weeks"][record.record_id] = record.fields
                    else:
                        state[COLLECTIONS[record.record_type]].append(
                            record.fields
                        )
                cursor = page.get("LastEvaluatedKey")
                if not cursor:
                    break
            if self.get_clock() == version == state["version"]:
                state["areas"].sort(
                    key=lambda area: (area.get("sort_order", 0), area["name"])
                )
                return state
        raise Conflict("The planner is changing. Please refresh in a moment.")

    @staticmethod
    def _records(state: dict) -> dict[str, PlannerRecord]:
        records = {}
        for kind, collection in COLLECTIONS.items():
            for entity in state[collection]:
                record = PlannerRecord(kind, entity["id"], entity)
                records[record.key["SK"]["S"]] = record
        for week, entity in state["weeks"].items():
            record = PlannerRecord("WEEK", week, entity)
            records[record.key["SK"]["S"]] = record
        return records

    def execute(
        self, request_id: str, command: dict, apply: Callable[[dict], object]
    ) -> dict:
        serialized = encode(command)
        if len(serialized.encode()) > 65536:
            raise ValidationError(
                "This change is too large. Submit a smaller plan."
            )
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        request_key = {
            "PK": {
                "S": "OPERATION#"
                + hashlib.sha256(request_id.encode()).hexdigest()
            },
            "SK": {"S": "RECEIPT"},
        }
        for attempt in range(6):
            cached = self._get(request_key)
            if cached:
                if cached["digest"]["S"] != digest:
                    raise Conflict(
                        "This request id was already used for another change."
                    )
                return json.loads(cached["result"]["S"])
            old = self.read()
            state = copy.deepcopy(old)
            result = apply(state)
            changed = state != old
            if changed:
                state["version"] += 1
            response = {"result": result, "version": state["version"]}
            if len(encode(response).encode()) > 300000:
                raise ValidationError(
                    "The result is too large. Submit fewer changes."
                )
            before, after = self._records(old), self._records(state)
            writes = []
            for key, record in after.items():
                if record != before.get(key):
                    writes.append(
                        {
                            "Put": {
                                "TableName": self.table_name,
                                "Item": record.to_item(),
                                "ConditionExpression": (
                                    "attribute_exists(PK)"
                                    if key in before
                                    else "attribute_not_exists(PK)"
                                ),
                            }
                        }
                    )
            clock = PlannerRecord(
                "CONFIG",
                "CLOCK",
                {
                    k: state[k]
                    for k in ("version", "content_version", "preferences")
                },
            )
            condition = (
                {
                    "ConditionExpression": "#version = :version",
                    "ExpressionAttributeNames": {"#version": "version"},
                    "ExpressionAttributeValues": {
                        ":version": {"N": str(old["version"])}
                    },
                }
                if old["version"]
                else {"ConditionExpression": "attribute_not_exists(PK)"}
            )
            if changed:
                writes.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": clock.to_item(),
                            **condition,
                        }
                    }
                )
            else:
                writes.append(
                    {
                        "ConditionCheck": {
                            "TableName": self.table_name,
                            "Key": clock.key,
                            **condition,
                        }
                    }
                )
            writes.append(
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": {
                            **request_key,
                            "TYPE": {"S": "OPERATION"},
                            "digest": {"S": digest},
                            "result": {"S": encode(response)},
                        },
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                }
            )
            if len(writes) > 100:
                raise ValidationError(
                    "This change affects too many records. Select at most 98 records at once."
                )
            try:
                self._client.transact_write_items(TransactItems=writes)
                return response
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in {
                    "TransactionCanceledException",
                    "TransactionConflictException",
                }:
                    raise
                reasons = exc.response.get("CancellationReasons", [])
                if any(
                    r.get("Code")
                    not in {
                        "None",
                        "ConditionalCheckFailed",
                        "TransactionConflict",
                    }
                    for r in reasons
                ):
                    raise
                time.sleep(0.02 * (attempt + 1))
        raise Conflict(
            "Another writer is updating the planner. Retry the same request."
        )
