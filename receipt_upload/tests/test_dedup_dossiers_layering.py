"""Dedup dossiers must use the receipt_dynamo public API (PR #1183 P2).

All DynamoDB operations belong in receipt_dynamo; reaching into
``DynamoClient._client`` from receipt_upload breaks that layering and
silently detaches this census from centralized query/error handling.
"""

from receipt_upload.dedup.dossiers import _receipt_place_merchants


class _FakeClient:
    """Public-API-only stand-in: no _client attribute at all."""

    table_name = "TestTable"

    def __init__(self):
        self.calls = []

    def list_receipt_places_raw(self, limit=None, last_evaluated_key=None):
        self.calls.append(last_evaluated_key)
        if last_evaluated_key is None:
            return (
                [
                    {
                        "image_id": {"S": "img-1"},
                        "receipt_id": {"N": "1"},
                        "merchant_name": {"S": "Mart One"},
                    }
                ],
                {"cursor": {"S": "next"}},
            )
        return (
            [
                {
                    # drifted row: identity only via PK/SK
                    "PK": {"S": "IMAGE#img-2"},
                    "SK": {"S": "RECEIPT#00002#PLACE"},
                    "merchant_name": {"S": "Mart Two"},
                },
                {
                    # malformed fallback keys are ignored, not reinterpreted
                    "PK": {"S": "img-3"},
                    "SK": {"S": "RECEIPT#00003#PLACE"},
                    "merchant_name": {"S": "Ignored Mart"},
                },
            ],
            None,
        )


def test_merchants_load_through_public_raw_census_api():
    fake = _FakeClient()
    merchants = _receipt_place_merchants(fake)
    assert merchants == {
        ("img-1", 1): "Mart One",
        ("img-2", 2): "Mart Two",
    }
    assert fake.calls == [None, {"cursor": {"S": "next"}}]
