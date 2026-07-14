"""Incremental ETL: CloudFront raw logs -> curated ``web_events`` Parquet.

A light, Spark-free transform run by a scheduled Lambda (and backfillable on
demand). For each target UTC date it rebuilds that partition idempotently:

1. ``UNLOAD`` a parsed / de-duplicated / classified projection to a unique
   per-run Parquet staging location, then
2. atomically repoint the Glue partition to the complete staging location and
   delete objects from superseded runs.

The source query reads *only that day's* raw CloudFront files (pruned via the
Athena ``$path`` pseudo-column), so the scan stays cheap as history grows.

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
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3

DB = os.environ.get("ANALYTICS_DB", "portfolio_analytics")
WORKGROUP = os.environ.get("ANALYTICS_WORKGROUP", "portfolio_analytics")
CURATED_BUCKET = os.environ["CURATED_BUCKET"]
CURATED_PREFIX = os.environ.get("CURATED_PREFIX", "web_events/")
STAGING_PREFIX = os.environ.get("STAGING_PREFIX", "staging/web_events/")
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

# Datacenter / hosting / proxy org signal. ip-api's `hosting` flag alone misses
# a lot of headless-browser scanners that run the beacon (they present a real
# browser UA and hosting=false), so we also fold ip-api's `proxy` flag and
# match the network owner (org/isp/asn) against known datacenter, cloud, and
# proxy providers. Deliberately conservative: residential ISPs (Cox, Comcast,
# Spectrum, TalkTalk) and company names never match, so real visitors stay
# human.
_DC_RE = (
    "hosting|datacenter|data center|colocation|\\bcolo\\b|\\bvps\\b|"
    "dedicated server|leaseweb|\\bovh\\b|hetzner|digitalocean|linode|"
    "vultr|contabo|\\bm247\\b|scaleway|choopa|quadranet|psychz|zenlayer|"
    "gcore|\\baws\\b|\\bec2\\b|amazon|google llc|googleusercontent|\\bgcp\\b|"
    "azure|microsoft corp|oracle cloud|alibaba|tencent|ultahost|sprious|"
    "moack|versatel"
)

_athena = boto3.client("athena", region_name=REGION)
_glue = boto3.client("glue", region_name=REGION)
_s3 = boto3.client("s3", region_name=REGION)


def _run(sql: str, deadline: float) -> str:
    qid = _athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DB},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]
    if not isinstance(qid, str):
        raise RuntimeError("Athena did not return a query execution ID")
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


def _enrich_ips(dts: list[str], deadline: float) -> tuple[int, bool]:
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
        batch = new_ips[i : i + 100]
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


def _projection_sql(dt: str) -> str:
    # CloudFront percent-encodes each log field ONCE. The beacon query string's
    # nested param values were additionally url-encoded by the client, so we
    # single-decode the field and decode only each extracted beacon value —
    # never double-decode the whole field (a non-beacon value like "100%-off"
    # would make the second url_decode throw and fail the whole rebuild).
    qs = "url_decode(query_string)"
    ua = "lower(url_decode(user_agent))"

    def beacon_param(name: str) -> str:
        return f"url_decode(regexp_extract({qs}, '{name}=([^&]+)', 1))"

    # Non-residential (datacenter/cloud/proxy) client: ip-api hosting OR proxy
    # flag, OR the network owner matches a known hosting/cloud/proxy provider.
    dc = (
        "(COALESCE(g.hosting, false) OR COALESCE(g.proxy, false)"
        " OR regexp_like(lower(concat("
        "coalesce(g.org, ''), ' ', coalesce(g.isp, ''), ' ',"
        f" coalesce(g.asn, ''))), '{_DC_RE}'))"
    )

    return f"""WITH src AS (
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
     OR {dc}) AS is_bot,
  location AS edge_location,
  g.country AS country,
  g.city AS city,
  g.org AS org,
  g.asn AS asn,
  {dc} AS is_hosting
FROM dedup
LEFT JOIN {DB}.ip_geo g ON dedup.request_ip = g.ip
"""


def _unload_sql(dt: str, location: str) -> str:
    # ``dt`` is intentionally absent from the Parquet payload: it is the Glue
    # partition key, supplied by the partition metadata after the swap. The
    # remaining SELECT order exactly matches ``web_events``' non-partition
    # columns in ``__init__.py``.
    return f"""UNLOAD (
{_projection_sql(dt)}
)
TO '{location}'
WITH (format = 'PARQUET', compression = 'SNAPPY')
"""


def _partition_location(dt: str) -> str | None:
    try:
        partition = _glue.get_partition(
            DatabaseName=DB,
            TableName="web_events",
            PartitionValues=[dt],
        )["Partition"]
    except _glue.exceptions.EntityNotFoundException:
        return None
    location = partition["StorageDescriptor"].get("Location")
    return location if isinstance(location, str) else None


def _location_prefix(location: str, dt: str) -> str:
    root = f"s3://{CURATED_BUCKET}/"
    if not location.startswith(root):
        raise ValueError(
            f"Refusing to clean partition outside curated bucket: {location}"
        )
    prefix = location[len(root) :].lstrip("/").rstrip("/") + "/"
    if f"dt={dt}/" not in prefix:
        raise ValueError(
            f"Refusing to clean location without dt={dt}: {location}"
        )
    return prefix


def _delete_prefix(prefix: str, keep_prefix: str | None = None) -> None:
    paginator = _s3.get_paginator("list_objects_v2")
    batch = []
    for page in paginator.paginate(Bucket=CURATED_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            if keep_prefix and obj["Key"].startswith(keep_prefix):
                continue
            batch.append({"Key": obj["Key"]})
            if len(batch) == 1000:
                _s3.delete_objects(
                    Bucket=CURATED_BUCKET, Delete={"Objects": batch}
                )
                batch = []
    if batch:
        _s3.delete_objects(Bucket=CURATED_BUCKET, Delete={"Objects": batch})


def _cleanup_partition_objects(
    dt: str,
    active_location: str,
    retired_location: str | None = None,
) -> None:
    """Delete every known layout for ``dt`` except the active partition.

    Sweeping both the legacy INSERT path and all inactive staging runs makes
    cleanup retry-safe after a Lambda timeout between the metadata swap and
    object deletion.
    """
    active_prefix = _location_prefix(active_location, dt)
    roots = [
        f"{CURATED_PREFIX.rstrip('/')}/dt={dt}/",
        f"{STAGING_PREFIX.rstrip('/')}/dt={dt}/",
    ]
    if retired_location:
        retired_prefix = _location_prefix(retired_location, dt)
        if not any(retired_prefix.startswith(root) for root in roots):
            roots.append(retired_prefix)
    for prefix in roots:
        _delete_prefix(prefix, keep_prefix=active_prefix)


def _rebuild_partition(dt: str, deadline: float) -> str:
    previous_location = _partition_location(dt)
    if previous_location:
        # Clear objects orphaned by an earlier interrupted run, while retaining
        # the complete location that queries currently read.
        _cleanup_partition_objects(dt, previous_location)

    run_id = uuid.uuid4().hex
    staging_prefix = f"{STAGING_PREFIX.rstrip('/')}/dt={dt}/run={run_id}/"
    staging_location = f"s3://{CURATED_BUCKET}/{staging_prefix}"
    swapped = False
    try:
        _run(_unload_sql(dt, staging_location), deadline)
        if previous_location:
            swap_sql = (
                f"ALTER TABLE {DB}.web_events PARTITION (dt='{dt}') "
                f"SET LOCATION '{staging_location}'"
            )
        else:
            swap_sql = (
                f"ALTER TABLE {DB}.web_events ADD PARTITION (dt='{dt}') "
                f"LOCATION '{staging_location}'"
            )
        _run(swap_sql, deadline)
        swapped = True
    finally:
        if not swapped:
            # Athena can leave partial UNLOAD output on failure. It is never
            # visible through web_events, and is safe to remove immediately.
            _delete_prefix(staging_prefix)

    # The metadata swap above is the only visibility boundary. Once it
    # succeeds, remove the previous location and any older abandoned runs.
    _cleanup_partition_objects(
        dt,
        staging_location,
        retired_location=previous_location,
    )
    return dt


def _target_dates(event: dict) -> list[str]:
    if event.get("start") and event.get("end"):
        start = datetime.strptime(event["start"], "%Y-%m-%d").date()
        end = datetime.strptime(event["end"], "%Y-%m-%d").date()
        days, day = [], start
        while day <= end:
            days.append(day.isoformat())
            day += timedelta(days=1)
        return days
    # Rebuild today + yesterday (UTC) so late-delivered CloudFront logs (which
    # lag hours, not days) get folded into their partitions. On the hourly
    # cadence each run re-absorbs stragglers within the hour, so a 2-day window
    # keeps partitions fresh while halving the destructive-rebuild footprint of
    # the former 4-day default (and matches this module's documented default).
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=n)).isoformat() for n in (1, 0)]


def handler(event: dict | None, context: Any) -> dict:
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
