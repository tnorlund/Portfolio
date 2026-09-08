"""Tests for the latency-sensitive API DynamoDB query client."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import Image, Receipt
from receipt_dynamo import api_read as api_dynamo


def _value_map(x, y):
    return {"M": {"x": {"N": str(x)}, "y": {"N": str(y)}}}


def _image_item():
    return {
        "PK": {"S": "IMAGE#image-id"},
        "SK": {"S": "IMAGE"},
        "GSI3PK": {"S": "IMAGE#PHOTO"},
        "GSI3SK": {"S": "NUM_RECEIPTS#00002"},
        "width": {"N": "960"},
        "height": {"N": "1280"},
        "timestamp_added": {"S": "2026-08-20T00:00:00+00:00"},
        "raw_s3_bucket": {"S": "raw-bucket"},
        "raw_s3_key": {"S": "raw-key"},
        "image_type": {"S": "PHOTO"},
        "receipt_count": {"N": "2"},
        "cdn_s3_bucket": {"S": "cdn-bucket"},
    }


def _receipt_item():
    return {
        "PK": {"S": "IMAGE#image-id"},
        "SK": {"S": "RECEIPT#00003"},
        "TYPE": {"S": "RECEIPT"},
        "width": {"N": "320"},
        "height": {"N": "640"},
        "timestamp_added": {"S": "2026-08-20T00:00:00+00:00"},
        "raw_s3_bucket": {"S": "raw-bucket"},
        "raw_s3_key": {"S": "receipt-key"},
        "top_left": _value_map(0.1, 0.2),
        "top_right": _value_map(0.8, 0.2),
        "bottom_left": _value_map(0.1, 0.9),
        "bottom_right": _value_map(0.8, 0.9),
    }


class FakeDynamoDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_lists_and_converts_one_image_with_precise_limit() -> None:
    next_key = {"PK": {"S": "next"}}
    dynamodb = FakeDynamoDB(
        [{"Items": [_image_item()], "LastEvaluatedKey": next_key}]
    )
    client = api_dynamo.ApiDynamoClient("table", dynamodb)

    images, returned_key = client.list_images_by_type("PHOTO", limit=1)

    assert returned_key == next_key
    assert images[0] == {
        **{field: None for field in api_dynamo.CDN_FIELDS},
        "cdn_s3_bucket": "cdn-bucket",
        "image_id": "image-id",
        "width": 960,
        "height": 1280,
        "timestamp_added": "2026-08-20T00:00:00+00:00",
        "raw_s3_bucket": "raw-bucket",
        "raw_s3_key": "raw-key",
        "image_type": "PHOTO",
        "receipt_count": 2,
    }
    assert dynamodb.calls[0]["Limit"] == 1
    assert dynamodb.calls[0]["IndexName"] == "GSI3"


def test_lists_and_converts_receipts() -> None:
    dynamodb = FakeDynamoDB([{"Items": [_receipt_item()]}])
    client = api_dynamo.ApiDynamoClient("table", dynamodb)

    receipts, returned_key = client.list_receipts(limit=1)

    assert returned_key is None
    assert receipts[0]["image_id"] == "image-id"
    assert receipts[0]["receipt_id"] == 3
    assert receipts[0]["top_left"] == {"x": 0.1, "y": 0.2}
    assert receipts[0]["cdn_s3_key"] is None
    assert dynamodb.calls[0]["IndexName"] == "GSITYPE"


def test_reuses_one_client_per_table(monkeypatch) -> None:
    created = []

    class FakeApiClient:
        def __init__(self, table_name):
            self.table_name = table_name
            created.append(self)

    monkeypatch.setattr(api_dynamo, "ApiDynamoClient", FakeApiClient)
    monkeypatch.setattr(api_dynamo, "_clients", {})

    first = api_dynamo.get_api_dynamo_client("table")
    second = api_dynamo.get_api_dynamo_client("table")

    assert first is second
    assert len(created) == 1


def test_serialization_matches_current_entities() -> None:
    common = dict(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        width=100,
        height=200,
        timestamp_added="2026-09-08T00:00:00+00:00",
        raw_s3_bucket="offline",
        raw_s3_key="offline.png",
        **{name: f"offline/{name}" for name in api_dynamo.CDN_FIELDS},
    )
    image = Image(**common, receipt_count=0)
    receipt = Receipt(
        **common,
        receipt_id=1,
        top_left={"x": 0, "y": 1},
        top_right={"x": 1, "y": 1},
        bottom_left={"x": 0, "y": 0},
        bottom_right={"x": 1, "y": 0},
    )
    assert api_dynamo.image_to_api(image.to_item()) == dict(image)
    assert api_dynamo.receipt_to_api(receipt.to_item()) == dict(receipt)


def test_query_pages_use_remaining_limit() -> None:
    cursor = {"PK": {"S": "next"}}
    dynamodb = FakeDynamoDB(
        [
            {"Items": [], "LastEvaluatedKey": cursor},
            {"Items": [_image_item()], "LastEvaluatedKey": cursor},
            {"Items": [_image_item()]},
        ]
    )
    images, returned_key = api_dynamo.ApiDynamoClient(
        "table", dynamodb
    ).list_images_by_type("PHOTO", limit=2)
    assert len(images) == 2
    assert returned_key is None
    assert [call["Limit"] for call in dynamodb.calls] == [2, 2, 1]
    assert dynamodb.calls[1]["ExclusiveStartKey"] == cursor


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_limit_never_reaches_dynamodb(limit) -> None:
    dynamodb = FakeDynamoDB([])
    with pytest.raises(api_dynamo.errors.EntityValidationError):
        api_dynamo.ApiDynamoClient("table", dynamodb).list_receipts(limit)
    assert dynamodb.calls == []


@pytest.mark.parametrize(
    "code,expected",
    [
        ("ValidationException", "EntityValidationError"),
        ("ResourceNotFoundException", "OperationError"),
        ("ThrottlingException", "DynamoDBThroughputError"),
        ("InternalServerError", "DynamoDBServerError"),
        ("AccessDeniedException", "DynamoDBError"),
    ],
)
def test_query_errors_use_shared_exceptions(code, expected) -> None:
    class FailingDynamo:
        def query(self, **kwargs):
            raise ClientError({"Error": {"Code": code}}, "Query")

    client = api_dynamo.ApiDynamoClient("table", FailingDynamo())
    with pytest.raises(getattr(api_dynamo.errors, expected)):
        client.list_receipts(limit=1)


def test_route_bundle_imports_without_general_client(tmp_path: Path) -> None:
    package = Path(api_dynamo.__file__).parent
    shutil.copy(package / "api_read.py", tmp_path / "_api_dynamo.py")
    shutil.copy(
        package / "data" / "shared_exceptions.py",
        tmp_path / "_api_dynamo_errors.py",
    )
    probe = f"""
import sys
sys.path.insert(0, {str(tmp_path)!r})
import _api_dynamo
assert 'receipt_dynamo' not in sys.modules
class OfflineDynamo:
    def query(self, **kwargs):
        assert kwargs['Limit'] == 1
        return {{'Items': []}}
client = _api_dynamo.ApiDynamoClient('offline', OfflineDynamo())
assert client.list_receipts(limit=1) == ([], None)
try:
    client.list_receipts(limit=0)
except _api_dynamo.errors.EntityValidationError:
    pass
else:
    raise AssertionError('missing validation')
"""
    subprocess.run(
        [sys.executable, "-I", "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
