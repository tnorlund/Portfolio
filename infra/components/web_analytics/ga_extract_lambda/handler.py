"""Scheduled GA4 extractor: GA4 Data API -> ga_daily NDJSON (curated bucket).

GA history for past days is stable and tiny, so each run re-pulls a rolling
window of daily metrics and overwrites a single newline-delimited JSON file.
The `ga_daily` Glue table and the `analytics_ga` / `analytics_reconcile` MCP
tools read it.

Credentials come from the ``GA_SERVICE_ACCOUNT_KEY`` env var (the ga4-reader
service-account JSON, injected from a Pulumi secret); falls back to ADC.
Invoke with no payload (rolling window) or ``{"start","end"}`` to backfill.
"""

import json
import os
from datetime import date, timedelta

import boto3
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    DateRange,
    Dimension,
    Metric,
    RunReportRequest,
)
from google.oauth2 import service_account

PROPERTY_ID = os.environ["GA_PROPERTY_ID"]
CURATED_BUCKET = os.environ["CURATED_BUCKET"]
GA_PREFIX = os.environ.get("GA_PREFIX", "ga_daily/")
LOOKBACK_DAYS = int(os.environ.get("GA_LOOKBACK_DAYS", "90"))
_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
_METRICS = [
    "sessions",
    "totalUsers",
    "newUsers",
    "screenPageViews",
    "engagedSessions",
]


def _client() -> BetaAnalyticsDataClient:
    # Pulumi-encrypted config secret, injected as a Lambda env var at deploy.
    key = os.environ.get("GA_SERVICE_ACCOUNT_KEY", "")
    if key:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(key), scopes=_SCOPES
        )
        return BetaAnalyticsDataClient(credentials=creds)
    return BetaAnalyticsDataClient()


def handler(event, _context):
    event = event or {}
    end = event.get("end") or date.today().isoformat()
    start = event.get("start") or (
        date.today() - timedelta(days=LOOKBACK_DAYS)
    ).isoformat()
    report = _client().run_report(
        RunReportRequest(
            property=f"properties/{PROPERTY_ID}",
            date_ranges=[DateRange(start_date=start, end_date=end)],
            dimensions=[Dimension(name="date")],
            metrics=[Metric(name=m) for m in _METRICS],
        )
    )
    pulled = {}
    for row in report.rows:
        day = row.dimension_values[0].value  # YYYYMMDD (property TZ = PT)
        vals = [m.value for m in row.metric_values]
        dt = f"{day[0:4]}-{day[4:6]}-{day[6:8]}"
        pulled[dt] = {
            "dt": dt,
            "sessions": int(vals[0]),
            "total_users": int(vals[1]),
            "new_users": int(vals[2]),
            "pageviews": int(vals[3]),
            "engaged_sessions": int(vals[4]),
        }

    # Merge with existing rows so a narrow backfill never drops history:
    # newly-pulled dates overwrite, all other dates are preserved.
    s3 = boto3.client("s3")
    key = f"{GA_PREFIX}data.json"
    merged = {}
    try:
        existing = (
            s3.get_object(Bucket=CURATED_BUCKET, Key=key)["Body"]
            .read()
            .decode()
        )
        for line in existing.splitlines():
            line = line.strip()
            if line:
                row = json.loads(line)
                merged[row["dt"]] = row
    except s3.exceptions.NoSuchKey:
        pass
    merged.update(pulled)

    body = (
        "\n".join(json.dumps(merged[d]) for d in sorted(merged)) + "\n"
    ).encode()
    s3.put_object(
        Bucket=CURATED_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/x-ndjson",
    )
    return {
        "rows_pulled": len(pulled),
        "rows_total": len(merged),
        "start": start,
        "end": end,
    }
