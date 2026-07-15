"""Random receipt-details route contracts."""

# pylint: disable=wrong-import-position

import json
import os
from types import SimpleNamespace

os.environ.setdefault("DYNAMODB_TABLE_NAME", "test-table")

from . import index  # noqa: E402


class FakeResolvedDetails:
    """Small API-facing resolved-details stand-in."""

    @staticmethod
    def to_document():
        return {
            "image_id": "image-id",
            "receipt_id": 1,
            "schema_version": 1,
            "merchant": {
                "name": {
                    "value": "Vons",
                    "provenance": "fingerprint",
                    "confidence": 0.97,
                }
            },
        }


class FakeDynamoClient:
    """Provide the two reads used by the route."""

    def __init__(self, _table_name):
        pass

    @staticmethod
    def list_receipts(limit, last_evaluated_key=None):
        del limit, last_evaluated_key
        return [SimpleNamespace(image_id="image-id", receipt_id=1)], None

    @staticmethod
    def get_receipt_details(_image_id, _receipt_id):
        return SimpleNamespace(
            receipt={"receipt_id": 1},
            words=[{"text": "TOTAL"}],
            labels=[{"label": "GRAND_TOTAL"}],
            resolved_details=FakeResolvedDetails(),
        )


def test_get_exposes_resolved_details(monkeypatch) -> None:
    monkeypatch.setattr(index, "DynamoClient", FakeDynamoClient)

    response = index.handle_get_request()
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["resolved_details"]["merchant"]["name"] == {
        "value": "Vons",
        "provenance": "fingerprint",
        "confidence": 0.97,
    }


def test_get_exposes_null_before_resolution(monkeypatch) -> None:
    monkeypatch.setattr(index, "DynamoClient", FakeDynamoClient)
    monkeypatch.setattr(
        FakeDynamoClient,
        "get_receipt_details",
        staticmethod(
            lambda *_: SimpleNamespace(
                receipt={"receipt_id": 1},
                words=[],
                labels=[],
                resolved_details=None,
            )
        ),
    )

    body = json.loads(index.handle_get_request()["body"])

    assert body["resolved_details"] is None
