"""Calendar-date summary filters must exclude unknown dates on both servers.

Filters run on ``effective_date`` (printed date, else an eligible bank
date); the fixtures below mirror the real record's contract:
``effective_date`` defaults to ``date`` and ``date_source`` follows it.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_receipt_mcp_section_tools import SERVER_FILES, _load_module

_UNSET = object()


def _record(
    image_id: str,
    date: str | None,
    total: float = 10.0,
    *,
    effective: str | None | object = _UNSET,
):
    """A summary-record stand-in. ``effective`` overrides the fallback
    date (a bank-dated receipt has ``date=None`` but an effective date);
    by default it equals ``date`` like a receipt with no bank match."""
    effective_str = date if effective is _UNSET else effective
    if date:
        source = "label"
    elif effective_str:
        source = "bank"
    else:
        source = None
    return SimpleNamespace(
        image_id=image_id,
        receipt_id=1,
        merchant_name="Example Market",
        date=datetime.fromisoformat(date) if date else None,
        effective_date=(
            datetime.fromisoformat(effective_str) if effective_str else None
        ),
        date_source=source,
        to_dict=lambda: {
            "image_id": image_id,
            "date": date,
            "effective_date": effective_str,
            "date_source": source,
            "grand_total": total,
            "tax": 0.0,
            "tip": None,
        },
    )


@pytest.fixture(params=sorted(SERVER_FILES))
def server(request):
    return _load_module(request.param, SERVER_FILES[request.param])


def _query(server, records, **filters):
    client = Mock()
    client.list_receipt_summaries.return_value = (records, None)
    client.list_receipt_places.return_value = ([], None)
    return asyncio.run(server.get_receipt_summaries_impl(client, **filters))


@pytest.mark.parametrize(
    "filters",
    [
        {"start_date": "2026-09-04"},
        {"end_date": "2026-09-06"},
        {"start_date": "2026-09-04", "end_date": "2026-09-06"},
    ],
)
def test_unknown_dates_do_not_consume_limit_or_inflate_totals(server, filters):
    result = _query(
        server,
        [_record("unknown", None, 500.0), _record("recent", "2026-09-05")],
        limit=1,
        **filters,
    )
    assert [row["image_id"] for row in result["summaries"]] == ["recent"]
    assert result["count"] == result["receipts_with_totals"] == 1
    assert result["total_spending"] == result["average_receipt"] == 10.0


@pytest.mark.parametrize(
    "filters",
    [
        {"start_date": "2026-09-04"},
        {"end_date": "2026-09-06"},
        {"start_date": "2026-09-04", "end_date": "2026-09-06"},
    ],
)
def test_bank_dated_receipt_is_filtered_by_its_effective_date(server, filters):
    """No printed date, but an eligible bank date inside the range."""
    records = [
        _record("bank_in", None, 12.0, effective="2026-09-05T00:00:00"),
        _record("bank_out", None, 99.0, effective="2026-09-20T00:00:00"),
        _record("unknown", None, 500.0),
    ]
    result = _query(server, records, **filters)
    ids = [row["image_id"] for row in result["summaries"]]
    assert "bank_in" in ids
    assert "unknown" not in ids
    if "end_date" in filters:
        assert "bank_out" not in ids
    row = next(r for r in result["summaries"] if r["image_id"] == "bank_in")
    assert row["date"] is None
    assert row["effective_date"] == "2026-09-05T00:00:00"
    assert row["date_source"] == "bank"


def test_ineligible_bank_date_is_treated_as_unknown(server):
    """A low-confidence bank match yields no effective date, so the
    receipt is excluded from date-filtered queries like any undated one."""
    records = [
        _record("weak", None, 500.0, effective=None),
        _record("recent", "2026-09-05"),
    ]
    result = _query(server, records, start_date="2026-09-04")
    assert [row["image_id"] for row in result["summaries"]] == ["recent"]
    assert result["total_spending"] == 10.0


def test_unknown_dates_remain_available_without_date_filters(server):
    result = _query(server, [_record("unknown", None)])
    assert result["count"] == 1
    assert result["summaries"][0]["date"] is None


def test_range_includes_entire_end_day_and_handles_offsets(server):
    records = [
        _record("before", "2026-09-03T23:59:59-07:00"),
        _record("start", "2026-09-04T00:00:00"),
        _record("end", "2026-09-06T23:59:59-07:00"),
        _record("after", "2026-09-07T00:00:00"),
    ]
    result = _query(
        server, records, start_date="2026-09-04", end_date="2026-09-06"
    )
    assert "error" not in result
    assert [row["image_id"] for row in result["summaries"]] == ["start", "end"]
    assert result["total_spending"] == 20.0


def test_reversed_range_returns_an_error(server):
    result = _query(server, [], start_date="2026-09-06", end_date="2026-09-04")
    assert "start_date must be on or before end_date" in result["error"]


@pytest.mark.parametrize("field", ["start_date", "end_date"])
def test_invalid_date_returns_an_error(server, field):
    result = _query(server, [], **{field: "not-a-date"})
    assert f"Invalid {field}" in result["error"]
