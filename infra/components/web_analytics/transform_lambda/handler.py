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

import os
import time
from datetime import datetime, timedelta, timezone

import boto3

DB = os.environ.get("ANALYTICS_DB", "portfolio_analytics")
WORKGROUP = os.environ.get("ANALYTICS_WORKGROUP", "portfolio_analytics")
CURATED_BUCKET = os.environ["CURATED_BUCKET"]
CURATED_PREFIX = os.environ.get("CURATED_PREFIX", "web_events/")
REGION = os.environ.get("AWS_REGION", "us-east-1")

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
  regexp_like(request_ip, '^(104\\.28\\.|2a09:bac)') AS is_warp,
  (regexp_like({ua}, '{_BOT_RE}')
     OR regexp_like(request_ip, '^185\\.177\\.72\\.')) AS is_bot,
  location AS edge_location,
  cast(date as varchar) AS dt
FROM dedup
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
    return {"rebuilt_partitions": rebuilt}
