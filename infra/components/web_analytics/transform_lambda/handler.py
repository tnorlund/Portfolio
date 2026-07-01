"""Incremental ETL: CloudFront raw logs -> curated ``web_events`` Parquet.

A light, Spark-free transform run by a scheduled Lambda (and backfillable on
demand). For each target UTC date it rebuilds that partition idempotently:

1. ``ALTER TABLE ... DROP PARTITION`` + delete the partition's S3 objects, then
2. ``INSERT INTO web_events`` a parsed / de-duplicated / classified projection
   of *only that day's* raw CloudFront files (pruned via the Athena ``$path``
   pseudo-column, so the scan stays cheap as history grows).

All beacon parsing, WARP/bot classification and dedup live here, so the curated
table is the single source of truth; the MCP ``analytics_*`` tools just SELECT.

Invoke with no payload (defaults to yesterday + today UTC, to absorb
late-delivered logs) or ``{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`` to
backfill a range.
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

DB = os.environ.get("ANALYTICS_DB", "portfolio_analytics")
WORKGROUP = os.environ.get("ANALYTICS_WORKGROUP", "portfolio_analytics")
CURATED_BUCKET = os.environ["CURATED_BUCKET"]
CURATED_PREFIX = os.environ.get("CURATED_PREFIX", "web_events/")
IP_GEO_KEY = os.environ.get("IP_GEO_KEY", "ip_geo/data.json")
REGION = os.environ.get("AWS_REGION", "us-east-1")
_WARP_RE = "^(104\\.28\\.|2a09:bac)"
# Safety cap so a huge backfill can't hammer the IP-info service in one run.
MAX_NEW_IPS = int(os.environ.get("GEO_MAX_NEW_IPS", "2000"))

# Keep in sync with the MCP analytics tools' classification.
_BOT_RE = (
    "bot|crawl|spider|slurp|curl|python-requests|go-http|headless|wget|"
    "monitor|preview|scan|http.?client|java/|okhttp|axios|node-fetch|libwww|"
    "facebookexternal|meta-external|petalbot|ahrefs|semrush|dataforseo"
)

_athena = boto3.client("athena", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)


def _run(sql: str, deadline: float) -> str:
    qid = _athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DB},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]
    while True:
        status = _athena.get_query_execution(QueryExecutionId=qid)[
            "QueryExecution"
        ]["Status"]
        if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.time() >= deadline:
            # Stop the query before the Lambda is killed, so it can't keep
            # mutating the partition after the function dies.
            _athena.stop_query_execution(QueryExecutionId=qid)
            raise TimeoutError("Athena query stopped near Lambda deadline")
        time.sleep(1.0)
    if status["State"] != "SUCCEEDED":
        raise RuntimeError(
            f"Athena {status['State']}: {status.get('StateChangeReason')}"
        )
    return qid


def _query_rows(sql: str, deadline: float) -> list:
    qid = _run(sql, deadline)
    rows, token = [], None
    while True:
        kwargs = {"QueryExecutionId": qid, "MaxResults": 1000}
        if token:
            kwargs["NextToken"] = token
        resp = _athena.get_query_results(**kwargs)
        for row in resp["ResultSet"]["Rows"]:
            rows.append([c.get("VarCharValue") for c in row["Data"]])
        token = resp.get("NextToken")
        if not token:
            break
    return rows[1:]  # drop header row


def _enrich_ips(dts: list, deadline: float) -> int:
    """Resolve org/geo/hosting for newly-seen (non-WARP) IPs and upsert the
    ip_geo NDJSON. Each IP is looked up once and cached; web_events joins it.
    """
    likes = " OR ".join(f"\"$path\" LIKE '%.{dt}-%'" for dt in dts)
    # Anti-join against ip_geo and LIMIT in Athena so the Lambda only fetches
    # up to the cap of *new* (uncached) IPs — never the full distinct set,
    # which could OOM a small Lambda on a backfill or bot spike.
    rows = _query_rows(
        f"SELECT d.ip FROM ("
        f" SELECT DISTINCT request_ip AS ip FROM {DB}.cloudfront_logs_prod"
        f" WHERE ({likes}) AND NOT regexp_like(request_ip, '{_WARP_RE}')"
        f") d LEFT JOIN {DB}.ip_geo g ON d.ip = g.ip"
        f" WHERE g.ip IS NULL LIMIT {MAX_NEW_IPS + 1}",
        deadline,
    )
    new_candidates = [r[0] for r in rows if r and r[0]]
    # More than the cap came back → truncated; mark incomplete so the caller
    # defers the rebuild (retries resume, each resolved IP is cached).
    complete = len(new_candidates) <= MAX_NEW_IPS
    new_ips = new_candidates[:MAX_NEW_IPS]

    existing = {}
    try:
        body = (
            _s3.get_object(Bucket=CURATED_BUCKET, Key=IP_GEO_KEY)["Body"]
            .read()
            .decode()
        )
        for line in body.splitlines():
            if line.strip():
                rec = json.loads(line)
                existing[rec["ip"]] = rec
    except _s3.exceptions.NoSuchKey:
        pass
    fields = (
        "query,country,regionName,city,isp,org,as,mobile,proxy,hosting,status"
    )
    for i in range(0, len(new_ips), 100):
        if time.time() >= deadline:
            complete = False
            break
        batch = new_ips[i:i + 100]
        payload = json.dumps(
            [{"query": ip, "fields": fields} for ip in batch]
        ).encode()
        req = urllib.request.Request(
            "http://ip-api.com/batch",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
        except Exception:  # pylint: disable=broad-except
            complete = False  # unresolved this run; a retry re-attempts it
            continue
        for g in data:
            ip = g.get("query")
            if not ip:
                continue
            existing[ip] = {
                "ip": ip,
                "country": g.get("country"),
                "region": g.get("regionName"),
                "city": g.get("city"),
                "isp": g.get("isp"),
                "org": g.get("org"),
                "asn": g.get("as"),
                "hosting": bool(g.get("hosting")),
                "proxy": bool(g.get("proxy")),
                "mobile": bool(g.get("mobile")),
            }
        # ip-api's batch endpoint allows ~15 requests/min; stay under it.
        time.sleep(4.0)

    # Zero bytes (not a blank line) when empty, so the JSON SerDe never trips
    # over an empty record on the LEFT JOIN.
    body_out = (
        "\n".join(json.dumps(existing[ip]) for ip in sorted(existing)) + "\n"
        if existing
        else ""
    )
    _s3.put_object(
        Bucket=CURATED_BUCKET,
        Key=IP_GEO_KEY,
        Body=body_out.encode(),
        ContentType="application/x-ndjson",
    )
    return len(new_ips), complete


def _insert_sql(dt: str) -> str:
    # CloudFront percent-encodes each log field ONCE. The beacon query string's
    # nested param values were additionally url-encoded by the client, so we
    # single-decode the field and decode only each extracted beacon value —
    # never double-decode the whole field (a non-beacon value like "100%-off"
    # would make the second url_decode throw and fail the whole rebuild).
    qs = "url_decode(query_string)"
    ua = "lower(url_decode(user_agent))"

    def beacon_param(name: str) -> str:
        return f"url_decode(regexp_extract({qs}, '{name}=([^&]+)', 1))"

    return f"""
INSERT INTO {DB}.web_events
WITH src AS (
  SELECT *, {beacon_param('eid')} AS _eid
  FROM {DB}.cloudfront_logs_prod WHERE "$path" LIKE '%.{dt}-%'
),
dedup AS (
  SELECT * FROM (
    SELECT *, row_number() OVER (
      PARTITION BY (CASE WHEN _eid <> '' THEN _eid ELSE request_id END)
      ORDER BY time
    ) AS rn FROM src
  ) WHERE rn = 1
)
SELECT
  request_id,
  cast(concat(cast(date as varchar), ' ', time) as timestamp) AS ts_utc,
  request_ip,
  uri,
  status,
  url_decode(referrer) AS referrer,
  {ua} AS user_agent,
  {qs} AS query_decoded,
  (uri = '/analytics/pixel.txt') AS is_beacon,
  {beacon_param('event')} AS event,
  {beacon_param('path')} AS evt_path,
  {beacon_param('sid')} AS sid,
  _eid AS eid,
  regexp_like(request_ip, '{_WARP_RE}') AS is_warp,
  (regexp_like({ua}, '{_BOT_RE}')
     OR regexp_like(request_ip, '^185\\.177\\.72\\.')
     OR COALESCE(g.hosting, false)) AS is_bot,
  location AS edge_location,
  g.country AS country,
  g.city AS city,
  g.org AS org,
  g.asn AS asn,
  COALESCE(g.hosting, false) AS is_hosting,
  cast(date as varchar) AS dt
FROM dedup
LEFT JOIN {DB}.ip_geo g ON dedup.request_ip = g.ip
"""


def _delete_partition_objects(dt: str) -> None:
    prefix = f"{CURATED_PREFIX}dt={dt}/"
    paginator = _s3.get_paginator("list_objects_v2")
    batch = []
    for page in paginator.paginate(Bucket=CURATED_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            batch.append({"Key": obj["Key"]})
            if len(batch) == 1000:
                _s3.delete_objects(
                    Bucket=CURATED_BUCKET, Delete={"Objects": batch}
                )
                batch = []
    if batch:
        _s3.delete_objects(Bucket=CURATED_BUCKET, Delete={"Objects": batch})


def _rebuild_partition(dt: str, deadline: float) -> str:
    _run(
        f"ALTER TABLE {DB}.web_events DROP IF EXISTS PARTITION (dt='{dt}')",
        deadline,
    )
    _delete_partition_objects(dt)
    _run(_insert_sql(dt), deadline)
    return dt


def _target_dates(event: dict) -> list:
    if event.get("start") and event.get("end"):
        start = datetime.strptime(event["start"], "%Y-%m-%d").date()
        end = datetime.strptime(event["end"], "%Y-%m-%d").date()
        days, day = [], start
        while day <= end:
            days.append(day.isoformat())
            day += timedelta(days=1)
        return days
    # Reprocess a few trailing UTC days so late-delivered CloudFront logs
    # (delivery can lag hours) get folded into their partitions. Idempotent.
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=n)).isoformat() for n in (3, 2, 1, 0)]


def handler(event, context):
    event = event or {}
    # Bound all Athena polling to the Lambda's own deadline (minus a buffer) so
    # a slow/queued query is stopped rather than orphaned when we're killed.
    remaining_ms = (
        context.get_remaining_time_in_millis()
        if context is not None
        and hasattr(context, "get_remaining_time_in_millis")
        else 540_000
    )
    deadline = time.time() + max(0.0, remaining_ms / 1000.0 - 15)
    targets = _target_dates(event)
    # Enrich newly-seen IPs first so the rebuild can classify hosting/bot and
    # materialize org/geo by joining ip_geo.
    new_ips, enrich_complete = _enrich_ips(targets, deadline)
    if not enrich_complete:
        # Don't rebuild with partial geo (unresolved IPs would misclassify as
        # non-hosting); fail so the invocation retries and finishes enrichment.
        raise RuntimeError(
            f"IP enrichment incomplete ({new_ips} resolved this run); "
            "deferring partition rebuild for retry"
        )
    rebuilt = []
    for dt in targets:
        if time.time() >= deadline:
            break
        rebuilt.append(_rebuild_partition(dt, deadline))
    skipped = [dt for dt in targets if dt not in rebuilt]
    if skipped:
        # Fail loudly so the invocation is retried rather than reported as a
        # success that silently dropped dates (esp. a long manual backfill).
        raise RuntimeError(
            f"Deadline reached: rebuilt {rebuilt}, skipped {skipped}"
        )
    return {"rebuilt_partitions": rebuilt, "ips_enriched": new_ips}
