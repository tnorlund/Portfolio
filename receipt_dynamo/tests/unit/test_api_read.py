"""Tests for the latency-sensitive API DynamoDB query client."""

from infra.components import api_dynamo


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
