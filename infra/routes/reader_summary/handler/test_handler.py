import json
import os

from botocore.exceptions import ClientError

os.environ.setdefault("READER_SUMMARY_TABLE_NAME", "test-table")

from . import index  # noqa: E402


class FakeTable:
    def __init__(self, *, duplicate=False):
        self.duplicate = duplicate
        self.put_items = []
        self.update_items = []
        self.get_calls = 0

    def get_item(self, Key):
        self.get_calls += 1

        if self.get_calls == 1:
            return {
                "Item": {
                    "sample_size": 5,
                    "total_time_to_bottom_ms": 500000,
                    "total_active_scroll_ms": 450000,
                    "total_screens_per_minute": 12.5,
                    "updated_at": "2026-06-19T00:00:00Z",
                }
            }

        return {
            "Item": {
                "sample_size": 6,
                "total_time_to_bottom_ms": 580000,
                "total_active_scroll_ms": 530000,
                "total_screens_per_minute": 14.5,
                "updated_at": "2026-06-19T00:00:01Z",
            }
        }

    def put_item(self, **kwargs):
        if self.duplicate:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "duplicate",
                    }
                },
                "PutItem",
            )

        self.put_items.append(kwargs)

    def update_item(self, **kwargs):
        self.update_items.append(kwargs)


def make_event(**overrides):
    payload = {
        "page_path": "/receipt",
        "analytics_session_id": "ses_123",
        "analytics_event_id": "evt_456",
        "time_to_bottom_ms": 80000,
        "active_scroll_ms": 75000,
        "scrollable_pixels": 12000,
        "screens_per_minute": 2.1,
        "quick_jump": False,
    }
    payload.update(overrides)

    return {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"origin": "https://tylernorlund.com"},
        "body": json.dumps(payload),
    }


def test_handler_counts_valid_reader_summary(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(index, "table", fake_table)

    response = index.handler(make_event(), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["counted"] is True
    assert body["quickJump"] is False
    assert body["comparison"]["sampleSize"] == 5
    assert body["comparison"]["readerDeltaPercent"] == 20
    assert len(fake_table.put_items) == 1
    assert len(fake_table.update_items) == 1
    assert fake_table.put_items[0]["Item"]["time_to_live"] > 0
    assert fake_table.put_items[0]["Item"]["PK"].startswith(
        "READER_SUMMARY#EVENT#"
    )
    assert "analytics_session_id" not in fake_table.put_items[0]["Item"]


def test_handler_does_not_double_count_duplicate_event(monkeypatch):
    fake_table = FakeTable(duplicate=True)
    monkeypatch.setattr(index, "table", fake_table)

    response = index.handler(make_event(), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["counted"] is False
    assert len(fake_table.update_items) == 0


def test_handler_marks_fast_scroll_as_quick_jump(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(index, "table", fake_table)

    response = index.handler(make_event(active_scroll_ms=100), None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["quickJump"] is True
    assert body["counted"] is False
    assert len(fake_table.update_items) == 0


def test_handler_accepts_allowed_referer(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(index, "table", fake_table)

    event = make_event()
    event["headers"] = {
        "referer": "https://tylernorlund.com/receipt",
    }

    response = index.handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["counted"] is True
    assert len(fake_table.put_items) == 1
    assert len(fake_table.update_items) == 1


def test_handler_rejects_untrusted_origin(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(index, "table", fake_table)

    event = make_event()
    event["headers"] = {"origin": "https://attacker.example"}

    response = index.handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 403
    assert body["error"] == "Forbidden"
    assert len(fake_table.put_items) == 0
    assert len(fake_table.update_items) == 0


def test_handler_rejects_missing_origin(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(index, "table", fake_table)

    event = make_event()
    event["headers"] = {}

    response = index.handler(event, None)
    body = json.loads(response["body"])

    assert response["statusCode"] == 403
    assert body["error"] == "Forbidden"
    assert len(fake_table.put_items) == 0
    assert len(fake_table.update_items) == 0
