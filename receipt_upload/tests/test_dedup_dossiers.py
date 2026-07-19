"""Tests for canonical merchant census loading in dedup dossiers."""

from receipt_upload.dedup import dossiers


def test_load_inputs_reads_merchants_from_raw_receipt_places(monkeypatch):
    class RawClient:
        def __init__(self):
            self.calls = []

        def query(self, **kwargs):
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {
                    "Items": [
                        {
                            "image_id": {"S": "image-1"},
                            "receipt_id": {"N": "1"},
                            "merchant_name": {"S": "First Merchant"},
                        }
                    ],
                    "LastEvaluatedKey": {"PK": {"S": "next"}},
                }
            return {
                "Items": [
                    {
                        "PK": {"S": "IMAGE#image-2"},
                        "SK": {"S": "RECEIPT#00002#PLACE"},
                        "merchant_name": {"S": "Second Merchant"},
                    }
                ]
            }

    class PlaceOnlyDynamo:
        table_name = "ReceiptsTable-dc5be22"

        def __init__(self):
            self._client = RawClient()

        def list_receipts(self):
            return ["receipt"], None

        def list_receipt_words(self):
            return [], None

        def list_receipt_word_labels(self):
            return [], None

        def list_receipt_summaries(self):
            return [], None

    dc = PlaceOnlyDynamo()
    monkeypatch.setattr(dossiers, "DynamoClient", lambda _table: dc)

    receipts, words, labels, totals, merchants = dossiers.load_inputs(
        dc.table_name, with_summaries=True
    )

    assert receipts == ["receipt"]
    assert not words and not labels and not totals
    assert merchants == {
        ("image-1", 1): "First Merchant",
        ("image-2", 2): "Second Merchant",
    }
    assert all(call["IndexName"] == "GSITYPE" for call in dc._client.calls)
    assert all(
        call["ExpressionAttributeValues"][":entity_type"]
        == {"S": "RECEIPT_PLACE"}
        for call in dc._client.calls
    )
