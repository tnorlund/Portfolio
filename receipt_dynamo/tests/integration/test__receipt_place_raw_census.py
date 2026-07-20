"""Raw ReceiptPlace census operation (PR #1183 codex P2).

A table-wide census cannot rely on strict entity conversion because
historical place rows can carry drifted secondary-index projections. The
data layer must expose a raw, non-converting read so callers
(receipt_upload dedup, census scripts) never reach into ``_client``.
"""

import pytest

from receipt_dynamo import DynamoClient


@pytest.fixture
def client(dynamodb_table):
    return DynamoClient(table_name=dynamodb_table)


def _put_raw_place(client, image_id, receipt_id, merchant, drifted=False):
    item = {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": f"RECEIPT#{receipt_id:05d}#PLACE"},
        "TYPE": {"S": "RECEIPT_PLACE"},
        "image_id": {"S": image_id},
        "receipt_id": {"N": str(receipt_id)},
        "merchant_name": {"S": merchant},
        "GSITYPE": {"S": "RECEIPT_PLACE"},
    }
    if drifted:
        # stale projection: entity conversion would reject this row, the
        # raw census must still return it
        item.pop("image_id")
        item["place_id"] = {"NULL": True}
    client._client.put_item(TableName=client.table_name, Item=item)


@pytest.mark.integration
def test_raw_census_returns_all_rows_including_drifted(client):
    _put_raw_place(
        client, "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Clean Mart"
    )
    _put_raw_place(
        client,
        "3f52804b-2fad-4e00-92c8-b593da3a8ed4",
        2,
        "Drifted Mart",
        drifted=True,
    )
    items, lek = client.list_receipt_places_raw()
    assert lek is None
    merchants = sorted(i.get("merchant_name", {}).get("S", "") for i in items)
    assert merchants == ["Clean Mart", "Drifted Mart"]
    # raw means raw: low-level attribute maps, no entity conversion
    assert all(isinstance(i.get("TYPE"), dict) for i in items)


@pytest.mark.integration
def test_raw_census_pagination(client):
    for n in range(1, 5):
        _put_raw_place(
            client,
            f"3f52804b-2fad-4e00-92c8-b593da3a8e{n:02d}",
            n,
            f"M{n}",
        )
    items, lek = client.list_receipt_places_raw(limit=2)
    assert len(items) == 2 and lek is not None
    rest, lek2 = client.list_receipt_places_raw(last_evaluated_key=lek)
    assert len(rest) == 2 and lek2 is None
