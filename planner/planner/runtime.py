"""Explicit environment selection; no implicit AWS or production target."""

import os
from urllib.parse import urlparse

import boto3

from planner.data.client import DynamoClient, table_definition
from planner.errors import ValidationError
from planner.service import Planner


def configured_planner(create_local: bool = False) -> Planner:
    env = os.environ.get("PLANNER_ENV")
    table = os.environ.get("PLANNER_TABLE")
    endpoint = os.environ.get("PLANNER_ENDPOINT")
    if env not in {"local", "dev"} or not table:
        raise ValidationError(
            "Set PLANNER_ENV=local|dev and PLANNER_TABLE explicitly. Production is not supported."
        )
    if env == "local":
        parsed = urlparse(endpoint or "")
        if parsed.scheme != "http" or parsed.hostname not in {
            "localhost",
            "127.0.0.1",
        }:
            raise ValidationError(
                "Local mode requires a loopback http PLANNER_ENDPOINT."
            )
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            endpoint_url=endpoint,
            aws_access_key_id="local",
            aws_secret_access_key="local",
        )
    else:
        if endpoint or os.environ.get("PLANNER_ALLOW_DEV") != "1":
            raise ValidationError(
                "Dev requires explicit PLANNER_ALLOW_DEV=1 and no endpoint override."
            )
        if (
            boto3.client("sts").get_caller_identity()["Account"]
            != "681647709217"
        ):
            raise ValidationError(
                "Unexpected AWS account for the dev planner."
            )
        client = boto3.client("dynamodb", region_name="us-east-1")
    if create_local:
        if env != "local":
            raise ValidationError("Automatic table creation is local-only.")
        try:
            client.describe_table(TableName=table)
        except client.exceptions.ResourceNotFoundException:
            client.create_table(**table_definition(table))
            client.get_waiter("table_exists").wait(TableName=table)
    return Planner(DynamoClient(table, client=client))


def context() -> dict:
    return {
        "env": os.environ["PLANNER_ENV"],
        "table": os.environ["PLANNER_TABLE"],
    }
