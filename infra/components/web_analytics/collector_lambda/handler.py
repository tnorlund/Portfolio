"""Request-time web analytics collector.

CloudFront forwards the analytics beacon, viewer location headers, and the
viewer user-agent to this Lambda Function URL.  The handler performs no network
lookups: it classifies the request and writes the event directly to DynamoDB.

Malformed/no-op requests receive a cache-disabled 1x1 GIF response. Persistence
failures are logged and re-raised so CloudFront marks the primary origin as
failed and serves the static S3 pixel instead; visitors still receive a 200.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

import boto3

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("WEB_EVENTS_LIVE_TABLE_NAME", "web_events_live")
TTL_DAYS = int(os.environ.get("WEB_EVENTS_LIVE_TTL_DAYS", "90"))

_WARP_RE = re.compile(r"^(104\.28\.|2a09:bac)", re.IGNORECASE)
_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|curl|python-requests|go-http|headless|wget|"
    r"monitor|preview|scan|http.?client|java/|okhttp|axios|node-fetch|libwww|"
    r"facebookexternal|meta-external|petalbot|ahrefs|semrush|dataforseo",
    re.IGNORECASE,
)
_SCANNER_PREFIX = "185.177.72."
_ANALYTICS_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# CloudFront supplies a numeric viewer ASN but not the network-owner string
# consumed by the batch transform's _DC_RE.  This deliberately conservative
# set covers the well-known infrastructure networks named by that regex.  It
# avoids residential/corporate access networks and can be extended as a new
# provider is confirmed.  No request-time ASN or geo lookup is performed.
_HOSTING_ASNS = frozenset(
    {
        7224,  # Amazon/AWS
        8068,  # Microsoft
        8075,  # Microsoft Azure
        8100,  # QuadraNet
        8987,  # Amazon/AWS
        9009,  # M247
        12876,  # Scaleway
        13335,  # Cloudflare Workers/datacenter (WARP is also caught by IP)
        14061,  # DigitalOcean
        14618,  # Amazon/AWS
        15169,  # Google
        16276,  # OVH
        16509,  # Amazon/AWS
        20473,  # Choopa/Vultr
        21859,  # Zenlayer
        24940,  # Hetzner
        28753,  # Leaseweb
        31898,  # Oracle Cloud
        37963,  # Alibaba Cloud
        396982,  # Google Cloud
        40676,  # Psychz Networks
        45090,  # Tencent Cloud
        45102,  # Alibaba Cloud
        51167,  # Contabo
        60781,  # Leaseweb
        62567,  # DigitalOcean
        62785,  # Amazon/AWS
        63949,  # Linode/Akamai Connected Cloud
        132203,  # Tencent Cloud
        199524,  # Gcore
        213230,  # Hetzner
    }
)

_EVENT_PARAM_LIMITS = {
    "event": 120,
    "sid": 180,
    "eid": 180,
    "path": 1024,
    "page_path": 1024,
    "ref": 2048,
    "utm_source": 256,
    "utm_medium": 256,
    "utm_campaign": 256,
}
_METRIC_PARAMS = (
    "percent_scrolled",
    "metric_name",
    "metric_value",
    "time_to_bottom_ms",
    "active_scroll_ms",
    "page_height",
    "scrollable_pixels",
    "screens_per_minute",
    "reader_delta_percent",
    "baseline_sample_size",
    "session_page_views",
    "quick_jump",
)

_PIXEL_BODY = "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="

_table: Any = None


def _get_table() -> Any:
    global _table  # pylint: disable=global-statement
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _response() -> dict[str, Any]:
    return {
        "statusCode": 200,
        "headers": {
            "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
            "Content-Type": "image/gif",
            "Pragma": "no-cache",
        },
        "isBase64Encoded": True,
        "body": _PIXEL_BODY,
    }


def _headers(event: dict[str, Any]) -> dict[str, str]:
    return {
        str(key).lower(): unquote(str(value))
        for key, value in (event.get("headers") or {}).items()
        if value is not None
    }


def _query_params(event: dict[str, Any]) -> dict[str, str]:
    raw = event.get("rawQueryString")
    if isinstance(raw, str):
        parsed = parse_qs(raw, keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items() if values}

    return {
        str(key): str(value)
        for key, value in (event.get("queryStringParameters") or {}).items()
        if value is not None
    }


def _clean(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _clean_id(value: Any, limit: int) -> str:
    """Bound key material to the ASCII alphabet emitted by the frontend."""
    cleaned = _clean(value, limit)
    return cleaned if _ANALYTICS_ID_RE.fullmatch(cleaned) else ""


def _clean_ref(value: Any, limit: int) -> str:
    """Retain only an HTTP(S) referrer origin and path."""
    try:
        parsed = urlsplit(str(value or "").strip())
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
        ):
            return ""

        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"

        sanitized = urlunsplit(
            (parsed.scheme.lower(), host, parsed.path or "/", "", "")
        )
        return _clean(sanitized, limit)
    except (TypeError, ValueError):
        return ""


def _viewer_ip(headers: dict[str, str]) -> str:
    address = headers.get("cloudfront-viewer-address", "").strip()
    if address:
        candidates = [address]
        if address.startswith("[") and "]" in address:
            candidates.insert(0, address[1 : address.index("]")])
        elif address.count(":") == 1:
            candidates.insert(0, address.rsplit(":", 1)[0])

        for candidate in candidates:
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue

    # CloudFront appends the actual viewer address to any viewer-supplied XFF,
    # so prefer the last valid address to avoid trusting a spoofed first hop.
    forwarded = headers.get("x-forwarded-for", "")
    for candidate in reversed(forwarded.split(",")):
        try:
            return str(ipaddress.ip_address(candidate.strip()))
        except ValueError:
            continue
    return ""


def _viewer_asn(headers: dict[str, str]) -> tuple[str, bool]:
    value = _clean(headers.get("cloudfront-viewer-asn"), 20)
    try:
        return value, int(value) in _HOSTING_ASNS
    except (TypeError, ValueError):
        return value, False


def _build_item(event: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    method = ((event.get("requestContext") or {}).get("http") or {}).get(
        "method"
    ) or "GET"
    if method != "GET":
        return None

    params = _query_params(event)
    event_name = _clean(params.get("event"), _EVENT_PARAM_LIMITS["event"])
    if not event_name:
        return None

    request_id = _clean_id(
        (event.get("requestContext") or {}).get("requestId"), 180
    )
    # The same canonical request is captured by DynamoDB now and CloudFront
    # standard logs for the durable batch, so both layers retain the same eid.
    eid = _clean_id(params.get("eid"), _EVENT_PARAM_LIMITS["eid"])
    if not eid:
        eid = request_id or f"evt_{uuid.uuid4().hex}"
    sid = _clean_id(params.get("sid"), _EVENT_PARAM_LIMITS["sid"])
    if not sid:
        sid = _clean(f"anon_{eid}", _EVENT_PARAM_LIMITS["sid"])

    headers = _headers(event)
    ip = _viewer_ip(headers)
    ua = _clean(headers.get("user-agent"), 1024)
    asn, is_hosting = _viewer_asn(headers)
    is_warp = bool(_WARP_RE.match(ip))
    is_bot = bool(_BOT_RE.search(ua)) or ip.startswith(_SCANNER_PREFIX)
    is_bot = is_bot or is_hosting

    epoch_ms = int(now.timestamp() * 1000)
    item: dict[str, Any] = {
        "dt": now.date().isoformat(),
        "sk": f"{epoch_ms:013d}#{sid}#{eid}",
        "ts": now.isoformat(timespec="milliseconds"),
        "epoch_ms": epoch_ms,
        "expires_at": int((now + timedelta(days=TTL_DAYS)).timestamp()),
        "sid": sid,
        "eid": eid,
        "event": event_name,
        "path": _clean(params.get("path"), _EVENT_PARAM_LIMITS["path"]),
        "page_path": _clean(
            params.get("page_path"), _EVENT_PARAM_LIMITS["page_path"]
        ),
        "ref": _clean_ref(params.get("ref"), _EVENT_PARAM_LIMITS["ref"]),
        "utm_source": _clean(
            params.get("utm_source"), _EVENT_PARAM_LIMITS["utm_source"]
        ),
        "utm_medium": _clean(
            params.get("utm_medium"), _EVENT_PARAM_LIMITS["utm_medium"]
        ),
        "utm_campaign": _clean(
            params.get("utm_campaign"), _EVENT_PARAM_LIMITS["utm_campaign"]
        ),
        "ip": ip,
        "ua": ua,
        "country": _clean(headers.get("cloudfront-viewer-country"), 8),
        "region": _clean(headers.get("cloudfront-viewer-country-region"), 32),
        "city": _clean(headers.get("cloudfront-viewer-city"), 256),
        "latitude": _clean(headers.get("cloudfront-viewer-latitude"), 32),
        "longitude": _clean(headers.get("cloudfront-viewer-longitude"), 32),
        "timezone": _clean(headers.get("cloudfront-viewer-time-zone"), 128),
        "asn": asn,
        "is_warp": is_warp,
        "is_bot": is_bot,
        "is_hosting": is_hosting,
    }

    for key in _METRIC_PARAMS:
        value = _clean(params.get(key), 120)
        if value:
            item[key] = value

    return item


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Persist one live beacon or let CloudFront fail over to static S3."""
    try:
        item = _build_item(event, _utc_now())
        if item is not None:
            _get_table().put_item(Item=item)
    except Exception:  # CloudFront converts this origin error to a static 200.
        LOGGER.exception("Failed to persist live web analytics event")
        raise
    return _response()
