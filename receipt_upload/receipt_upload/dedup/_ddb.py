"""Shared low-level DynamoDB/S3 helpers + typed exceptions for the dedup I/O.

Centralises three things the executor/cleanup modules share:
  * the boto3 client accessor (``DynamoClient`` exposes no public client),
  * a paginated-query helper (was duplicated), and
  * the typed exception tuples we record-and-continue on at an I/O boundary,
    instead of bare ``except Exception``.
"""

from __future__ import annotations

from typing import Iterator

from botocore.exceptions import BotoCoreError, ClientError
from receipt_dynamo.data.shared_exceptions import ReceiptDynamoError

# Failures from the AWS SDK (S3 / low-level DynamoDB) we surface, not crash on.
AWS_ERRORS = (ClientError, BotoCoreError)
# High-level receipt_dynamo calls wrap AWS errors as ReceiptDynamoError; include
# the raw AWS errors too for the low-level ``_client`` calls.
DYNAMO_ERRORS = (ReceiptDynamoError, ClientError, BotoCoreError)


def raw_client(dynamo):
    """Return the underlying boto3 client (no public accessor on DynamoClient)."""
    return dynamo._client  # pylint: disable=protected-access


def paginate(dynamo, **query_kwargs) -> Iterator[dict]:
    """Yield every item from a (possibly paginated) DynamoDB query."""
    client = raw_client(dynamo)
    lek = None
    while True:
        if lek:
            query_kwargs["ExclusiveStartKey"] = lek
        resp = client.query(**query_kwargs)
        yield from resp.get("Items", [])
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
