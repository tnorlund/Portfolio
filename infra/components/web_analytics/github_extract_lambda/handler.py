"""Scheduled GitHub traffic extractor: Traffic API -> github_traffic NDJSON.

GitHub's traffic API only exposes a **14-day rolling window**, so the history
is lost unless it is snapshotted. This Lambda runs daily and merges each pull
into a single newline-delimited JSON file behind the ``github_traffic`` Glue
table, so daily views/clones accumulate a durable history well past 14 days and
referrer/path snapshots are kept over time. The ``analytics_github`` MCP tool
reads it.

Only depends on the stdlib + boto3 (already in the Lambda runtime), so it ships
as a plain zip (no container image / layer needed).

Auth: a GitHub token (fine-grained PAT with **Administration: read**, or a
classic token with ``repo``) from the ``GITHUB_TOKEN`` env var, injected from a
Pulumi secret. Repos from ``GITHUB_REPOS`` (comma-separated ``owner/name``).
Invoke with no payload.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import date

import boto3

CURATED_BUCKET = os.environ["CURATED_BUCKET"]
GH_PREFIX = os.environ.get("GH_PREFIX", "github_traffic/")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPOS = [
    r.strip()
    for r in os.environ.get("GITHUB_REPOS", "").split(",")
    if r.strip()
]
_API = "https://api.github.com"


def _get(path: str):
    req = urllib.request.Request(
        f"{_API}{path}",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "portfolio-web-analytics",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _snapshot(repo: str, snap: str) -> list:
    """Return github_traffic rows for one repo's current 14-day window."""
    rows = []

    # Daily timeseries: keyed on the actual event day so re-pulls overwrite
    # that day with the latest (most complete) reading; older days survive.
    for metric, path in (("views", "views"), ("clones", "clones")):
        try:
            data = _get(f"/repos/{repo}/traffic/{path}")
        except urllib.error.HTTPError as ex:
            # 404 = repo has no traffic data / not found: an expected absence.
            # 401/403 (bad/expired/under-permissioned token, rate limit) and
            # 5xx are operational failures — raise so the scheduled run fails
            # loudly and retries, rather than silently returning zero rows and
            # losing the 14-day window forever.
            if ex.code == 404:
                continue
            raise
        for d in data.get(metric, []):
            # 'YYYY-MM-DDT..' -> 'YYYY-MM-DD'
            day = d.get("timestamp", "")[:10]
            if not day:
                continue
            rows.append({
                "repo": repo, "metric": metric, "item": day,
                "cnt": int(d.get("count", 0)),
                "uniques": int(d.get("uniques", 0)),
                "event_day": day, "snapshot_date": snap,
            })

    # 14-day aggregates: keep one row per snapshot so referrer/path trends are
    # visible over time.
    for metric, path, name_key in (
        ("referrer", "popular/referrers", "referrer"),
        ("path", "popular/paths", "path"),
    ):
        try:
            data = _get(f"/repos/{repo}/traffic/{path}")
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                continue
            raise
        for d in data:
            rows.append({
                "repo": repo, "metric": metric,
                "item": d.get(name_key, ""),
                "cnt": int(d.get("count", 0)),
                "uniques": int(d.get("uniques", 0)),
                "event_day": "", "snapshot_date": snap,
            })
        # An empty list after a previously non-empty snapshot must be visible,
        # or analytics_github's max(snapshot_date) keeps reporting stale rows
        # as the latest. Write a sentinel (item='') so this snapshot exists;
        # the reader filters item<>'' so it surfaces as "no referrers/paths".
        if not data:
            rows.append({
                "repo": repo, "metric": metric, "item": "",
                "cnt": 0, "uniques": 0,
                "event_day": "", "snapshot_date": snap,
            })
    return rows


def _merge_key(r: dict) -> str:
    # Timeseries de-dup on (repo, metric, day) so the latest reading wins;
    # aggregates de-dup on (repo, metric, item, snapshot) to retain history.
    if r["metric"] in ("views", "clones"):
        return f"{r['repo']}|{r['metric']}|{r['event_day']}"
    return f"{r['repo']}|{r['metric']}|{r['item']}|{r['snapshot_date']}"


def handler(event, _context):
    if not GH_TOKEN or not REPOS:
        return {"error": "GITHUB_TOKEN and GITHUB_REPOS must be set"}
    snap = (event or {}).get("snapshot") or date.today().isoformat()

    pulled = {}
    for repo in REPOS:
        for r in _snapshot(repo, snap):
            pulled[_merge_key(r)] = r

    s3 = boto3.client("s3")
    key = f"{GH_PREFIX}data.json"
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
                r = json.loads(line)
                merged[_merge_key(r)] = r
    except s3.exceptions.NoSuchKey:
        pass
    merged.update(pulled)

    body = (
        "\n".join(json.dumps(merged[k]) for k in sorted(merged)) + "\n"
    ).encode()
    s3.put_object(
        Bucket=CURATED_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/x-ndjson",
    )
    return {
        "repos": REPOS,
        "rows_pulled": len(pulled),
        "rows_total": len(merged),
        "snapshot": snap,
    }
