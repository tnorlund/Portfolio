"""Unit tests for the request-time web analytics collector."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from urllib.parse import quote

import pytest


def _load_handler_module() -> Any:
    handler_path = (
        Path(__file__).resolve().parents[1] / "collector_lambda" / "handler.py"
    )
    spec = importlib.util.spec_from_file_location(
        "web_analytics_collector_handler", handler_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = _load_handler_module()
NOW = datetime(2026, 7, 10, 18, 30, 0, 123000, tzinfo=timezone.utc)


def _event(raw_query: str, **headers: str) -> dict[str, object]:
    return {
        "rawQueryString": raw_query,
        "headers": headers,
        "requestContext": {
            "requestId": "request-1",
            "http": {"method": "GET"},
        },
    }


def test_build_item_parses_attribution_geo_and_ttl() -> None:
    event = _event(
        "event=page_view&sid=ses_1&eid=evt_1&path=%2Freceipt&"
        "page_path=%2Freceipt%3Futm_campaign%3Dbabylist&"
        "ref=https%3A%2F%2Fgithub.com%2Ftnorlund%2FPortfolio%3F"
        "token%3Dprivate%23message&"
        "utm_source=li&utm_medium=dm&utm_campaign=arthur-babylist&"
        "percent_scrolled=90",
        **{
            "CloudFront-Viewer-Address": "203.0.113.9:43120",
            "CloudFront-Viewer-ASN": "7922",
            "CloudFront-Viewer-Country": "US",
            "CloudFront-Viewer-Country-Region": "WA",
            "CloudFront-Viewer-City": "Vancouver%20Heights",
            "CloudFront-Viewer-Latitude": "45.63",
            "CloudFront-Viewer-Longitude": "-122.67",
            "CloudFront-Viewer-Time-Zone": "America%2FLos_Angeles",
            "User-Agent": "Mozilla/5.0",
        },
    )

    item = collector._build_item(event, NOW)

    assert item is not None
    assert item["dt"] == "2026-07-10"
    assert item["sk"] == "1783708200123#ses_1#evt_1"
    assert item["ts"] == "2026-07-10T18:30:00.123+00:00"
    assert item["eid"] == "evt_1"
    assert item["expires_at"] == int(
        datetime(
            2026, 10, 8, 18, 30, 0, 123000, tzinfo=timezone.utc
        ).timestamp()
    )
    assert item["path"] == "/receipt"
    assert item["ref"] == "https://github.com/tnorlund/Portfolio"
    assert item["utm_campaign"] == "arthur-babylist"
    assert item["ip"] == "203.0.113.9"
    assert item["city"] == "Vancouver Heights"
    assert item["timezone"] == "America/Los_Angeles"
    assert item["percent_scrolled"] == "90"
    assert item["is_warp"] is False
    assert item["is_bot"] is False
    assert item["is_hosting"] is False


def test_build_item_sanitizes_referrer_at_the_trust_boundary() -> None:
    trusted = "https://user:secret@example.com:8443/path?token=private#message"
    trusted_item = collector._build_item(
        _event(
            "event=page_view&sid=trusted&eid=1&ref="
            f"{quote(trusted, safe='')}"
        ),
        NOW,
    )
    untrusted_item = collector._build_item(
        _event(
            "event=page_view&sid=untrusted&eid=2&ref="
            "javascript%3Aalert(1)"
        ),
        NOW,
    )

    assert trusted_item is not None
    assert trusted_item["ref"] == "https://example.com:8443/path"
    assert untrusted_item is not None
    assert untrusted_item["ref"] == ""


def test_live_classification_covers_known_batch_signals() -> None:
    warp = collector._build_item(
        _event(
            "event=page_view&sid=warp&eid=1",
            **{
                "CloudFront-Viewer-Address": "104.28.42.1:1234",
                "User-Agent": "Mozilla/5.0",
            },
        ),
        NOW,
    )
    bot = collector._build_item(
        _event(
            "event=page_view&sid=bot&eid=2",
            **{
                "CloudFront-Viewer-Address": "203.0.113.10:1234",
                "User-Agent": "Mozilla/5.0 HeadlessChrome",
            },
        ),
        NOW,
    )
    scanner = collector._build_item(
        _event(
            "event=page_view&sid=scanner&eid=3",
            **{
                "CloudFront-Viewer-Address": "185.177.72.44:1234",
                "User-Agent": "Mozilla/5.0",
            },
        ),
        NOW,
    )
    hosting = collector._build_item(
        _event(
            "event=page_view&sid=hosting&eid=4",
            **{
                "CloudFront-Viewer-Address": "198.51.100.4:1234",
                "CloudFront-Viewer-ASN": "16509",
                "User-Agent": "Mozilla/5.0",
            },
        ),
        NOW,
    )
    cloudflare_worker = collector._build_item(
        _event(
            "event=page_view&sid=worker&eid=5",
            **{
                "CloudFront-Viewer-Address": "198.51.100.5:1234",
                "CloudFront-Viewer-ASN": "13335",
                "User-Agent": "Mozilla/5.0",
            },
        ),
        NOW,
    )

    assert warp is not None and warp["is_warp"] is True
    assert bot is not None and bot["is_bot"] is True
    assert scanner is not None and scanner["is_bot"] is True
    assert hosting is not None and hosting["is_hosting"] is True
    assert hosting["is_bot"] is True
    assert cloudflare_worker is not None
    assert cloudflare_worker["is_hosting"] is True
    assert cloudflare_worker["is_bot"] is True


def test_viewer_address_wins_and_xff_fallback_uses_last_valid_ip() -> None:
    preferred = collector._build_item(
        _event(
            "event=page_view&sid=a&eid=1",
            **{
                "CloudFront-Viewer-Address": "[2001:db8::9]:443",
                "X-Forwarded-For": "192.0.2.1, 192.0.2.2",
            },
        ),
        NOW,
    )
    fallback = collector._build_item(
        _event(
            "event=page_view&sid=b&eid=2",
            **{"X-Forwarded-For": "spoofed, 198.51.100.8"},
        ),
        NOW,
    )

    assert preferred is not None and preferred["ip"] == "2001:db8::9"
    assert fallback is not None and fallback["ip"] == "198.51.100.8"


def test_eid_is_shared_with_the_durable_batch_event() -> None:
    item = collector._build_item(
        _event("event=page_view&sid=ses_1&eid=legacy_evt"), NOW
    )

    assert item is not None
    assert item["eid"] == "legacy_evt"
    assert item["sk"].endswith("#ses_1#legacy_evt")


def test_non_ascii_ids_cannot_overflow_the_dynamodb_sort_key() -> None:
    item = collector._build_item(
        _event(
            "event=page_view&sid=%F0%9F%92%A5%F0%9F%92%A5&"
            "eid=%F0%9F%92%A5%F0%9F%92%A5"
        ),
        NOW,
    )

    assert item is not None
    assert item["eid"] == "request-1"
    assert item["sid"] == "anon_request-1"
    assert len(item["sk"].encode("utf-8")) < 1024


def test_handler_writes_immediately_and_returns_gif() -> None:
    table = Mock()
    collector._table = table
    collector._utc_now = lambda: NOW

    response = collector.handler(
        _event("event=page_view&sid=ses_1&eid=evt_1"), None
    )

    table.put_item.assert_called_once()
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "image/gif"
    assert response["isBase64Encoded"] is True


def test_handler_reraises_dynamodb_failure_for_cloudfront_failover() -> None:
    table = Mock()
    table.put_item.side_effect = RuntimeError("DynamoDB unavailable")
    collector._table = table
    collector._utc_now = lambda: NOW

    with pytest.raises(RuntimeError, match="DynamoDB unavailable"):
        collector.handler(_event("event=page_view&sid=ses_1&eid=evt_1"), None)
