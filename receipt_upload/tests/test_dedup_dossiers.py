"""Tests for canonical merchant census loading in dedup dossiers."""

from receipt_upload.dedup import dossiers


def test_load_inputs_reads_merchants_from_raw_receipt_places(monkeypatch):
    class PlaceOnlyDynamo:
        table_name = "ReceiptsTable-dc5be22"

        def __init__(self):
            self.place_calls = []

        def list_receipt_places_raw(self, limit=None, last_evaluated_key=None):
            self.place_calls.append((limit, last_evaluated_key))
            if last_evaluated_key is None:
                return (
                    [
                        {
                            "image_id": {"S": "image-1"},
                            "receipt_id": {"N": "1"},
                            "merchant_name": {"S": "First Merchant"},
                        }
                    ],
                    {"PK": {"S": "next"}},
                )
            return (
                [
                    {
                        "PK": {"S": "IMAGE#image-2"},
                        "SK": {"S": "RECEIPT#00002#PLACE"},
                        "merchant_name": {"S": "Second Merchant"},
                    },
                    {
                        # A malformed primary key must not become an image ID.
                        "PK": {"S": "image-3"},
                        "SK": {"S": "RECEIPT#00003#PLACE"},
                        "merchant_name": {"S": "Ignored Merchant"},
                    },
                ],
                None,
            )

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
    assert dc.place_calls == [
        (None, None),
        (None, {"PK": {"S": "next"}}),
    ]
