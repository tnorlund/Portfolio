"""list_receipt_places census uses the standard DynamoClient resolution
(PR #1183 codex P2): region defaults + DYNAMODB_ENDPOINT_URL honored, and
pagination flows through the data layer's raw census read."""

from __future__ import annotations

from scripts import list_receipt_places as lrp


class _FakeClient:
    table_name = "T"

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def list_receipt_places_raw(self, limit=None, last_evaluated_key=None):
        self.calls.append((limit, last_evaluated_key))
        return self.pages[len(self.calls) - 1]


def test_list_all_places_paginates_through_raw_census():
    pages = [
        ([{"merchant_name": {"S": "A"}}], {"k": {"S": "1"}}),
        ([{"merchant_name": {"S": "B"}}], None),
    ]
    fake = _FakeClient(pages)
    places = lrp.list_all_places(fake)
    assert [p["merchant_name"] for p in places] == ["A", "B"]
    assert fake.calls[0] == (None, None)
    assert fake.calls[1] == (None, {"k": {"S": "1"}})


def test_limit_short_circuits():
    pages = [
        (
            [{"merchant_name": {"S": "A"}}, {"merchant_name": {"S": "B"}}],
            {"k": {"S": "1"}},
        )
    ]
    fake = _FakeClient(pages)
    places = lrp.list_all_places(fake, limit=1)
    assert len(places) == 1
