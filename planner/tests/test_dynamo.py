"""DynamoDB entity and transaction tests, beyond command fixtures."""

import os
from uuid import uuid4

import boto3
import pytest
from moto import mock_aws

from planner.data.client import DynamoClient, table_definition
from planner.entities import PlannerRecord
from planner.errors import ValidationError
from planner.service import Planner


def test_entity_round_trip_and_key_validation():
    record = PlannerRecord(
        "ITEM", "example-id", {"text": "Read", "date": None, "revision": 1}
    )
    assert record.key == {
        "PK": {"S": "PLANNER"},
        "SK": {"S": "ITEM#example-id"},
    }
    assert PlannerRecord.from_item(record.to_item()) == record
    with pytest.raises(ValueError):
        PlannerRecord("ITEM", "invalid#id", {})
    with pytest.raises(ValueError):
        PlannerRecord.from_item({**record.to_item(), "TYPE": {"S": "AREA"}})


@mock_aws
def test_large_change_fails_before_any_record_is_written():
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(**table_definition("PlannerLimitTest"))
    planner = Planner(DynamoClient("PlannerLimitTest", client=client))
    with pytest.raises(ValidationError, match="too many records"):
        planner.execute(
            {
                "action": "batch",
                "changes": [
                    {"action": "save_item", "text": f"Task {i}"}
                    for i in range(99)
                ],
            }
        )
    assert planner.snapshot()["items"] == []
    assert planner.store.get_clock() == 0


@mock_aws
def test_paginated_read_retries_if_clock_changes(monkeypatch):
    client = boto3.client("dynamodb", region_name="us-east-1")
    client.create_table(**table_definition("PlannerPageTest"))
    store = DynamoClient("PlannerPageTest", client=client)
    planner = Planner(store)
    for i in range(3):
        planner.execute({"action": "save_item", "text": f"Task {i}"})
    query = client.query
    pages = []

    def paginated(**args):
        pages.append(args)
        if len(pages) == 2:
            # Simulate a concurrent committed change between read pages.
            client.update_item(
                TableName="PlannerPageTest",
                Key={"PK": {"S": "PLANNER"}, "SK": {"S": "CONFIG#CLOCK"}},
                UpdateExpression="ADD #version :one",
                ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues={":one": {"N": "1"}},
            )
        return query(**args, Limit=1)

    monkeypatch.setattr(client, "query", paginated)
    assert len(store.read()["items"]) == 3
    assert len(pages) >= 8


@pytest.mark.skipif(
    not os.environ.get("PLANNER_TEST_ENDPOINT"),
    reason="Set a loopback DynamoDB Local endpoint for wire tests",
)
def test_real_dynamodb_local_transaction_and_restart():
    endpoint = os.environ["PLANNER_TEST_ENDPOINT"]
    assert endpoint.startswith("http://127.0.0.1:")
    client = boto3.client(
        "dynamodb",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    table = "PlannerWire-" + uuid4().hex[:12]
    client.create_table(**table_definition(table))
    try:
        planner = Planner(DynamoClient(table, client=client))
        added = planner.execute(
            {
                "action": "save_item",
                "text": "Wire proof",
                "date": "2026-09-07",
            },
            "wire-create",
        )
        item = added["result"]
        proposal = planner.execute(
            {
                "action": "propose",
                "title": "Finish it",
                "rationale": "Explicit evaluation action",
                "changes": [
                    {
                        "action": "save_item",
                        "id": item["id"],
                        "revision": item["revision"],
                        "done": True,
                    }
                ],
            },
            "wire-proposal",
        )["result"]
        planner.execute(
            {
                "action": "resolve_proposal",
                "id": proposal["id"],
                "decision": "accept",
            },
            "wire-accept",
        )
        restarted = Planner(DynamoClient(table, client=client))
        assert restarted.snapshot()["items"][0]["done"] is True
        assert restarted.snapshot()["proposals"][0]["status"] == "accepted"
        assert (
            restarted.execute(
                {
                    "action": "save_item",
                    "text": "Wire proof",
                    "date": "2026-09-07",
                },
                "wire-create",
            )
            == added
        )
        assert restarted.store.get_clock() == 3
    finally:
        client.delete_table(TableName=table)
