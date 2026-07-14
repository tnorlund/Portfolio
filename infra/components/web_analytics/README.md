# Web Analytics (Glue + Athena over CloudFront logs)

Makes the production CloudFront access logs **queryable** so Claude (via the
`analytics_*` MCP tools) can answer "how much real traffic did the site get,
who visited, from where" without hand-parsing gzip logs.

No new data pipeline — it's a thin query layer over logs that already exist.

## Architecture (ETL + serving)

```
INGEST     CloudFront → s3://<logs-bucket>/cloudfront/prod/   (already exists)
              │
TRANSFORM   transform Lambda (scheduled hourly + backfillable), Athena UNLOAD:
              parse beacon · dedup(request_id) · classify warp/bot · PT-ready
              → per-run Parquet staging → atomic `dt` location swap
              → web_events                                  [single source of truth]
              │
SERVE       analytics_* MCP tools SELECT from web_events (partition-pruned)
```

## What this creates

- **Glue database** `portfolio_analytics`
- **Raw external table** `cloudfront_logs_prod` over
  `s3://<cloudfront-logs-bucket>/cloudfront/prod/` (gzip TSV, 2 header lines
  skipped; AWS CloudFront standard log column order) — the transform's input
- **Curated table** `web_events` — Parquet, **partitioned by UTC `dt`**, parsed
  + de-duplicated + WARP/bot-classified; what the MCP tools read
- **Transform Lambda** (`transform_lambda/handler.py`) — incremental, idempotent
  per-day partition rebuild; scheduled hourly, and invokable with
  `{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}` to backfill
- **Athena workgroup** `portfolio_analytics` + a **results bucket** (30-day expiry)
- A **curated bucket** for the Parquet output
- **IAM policies** — read-only Athena/Glue/S3 for the MCP Lambda role; a
  read-raw / write-curated / manage-partitions policy for the transform Lambda

## Transform details

- Reads **only the target day's** raw files via the Athena `$path` pseudo-column
  (`WHERE "$path" LIKE '%.<dt>-%'`), so scan cost stays flat as history grows.
- **Gap-free and idempotent:** writes each rebuilt day to a unique
  `staging/web_events/dt=<dt>/run=<uuid>/` Parquet location, atomically repoints
  the existing Glue partition with `SET LOCATION`, then removes the superseded
  partition objects and abandoned staging runs. First-time partitions are
  registered only after their staged output is complete.
- **Dedups** on `request_id` (CloudFront can re-deliver lines).
- Partition is the **UTC** log date; the serve layer buckets to PT and widens
  the scan ±1 day so PT-day edges are correct.
- **IP enrichment:** before each rebuild the transform resolves any newly-seen
  (non-WARP) IP via an IP-info service, caching org/geo/hosting/proxy in the
  `ip_geo` table (each IP looked up once). The rebuild LEFT JOINs `ip_geo` to
  materialize `org` / `city` / `country` / `is_hosting` on `web_events` and to
  fold **`hosting` into `is_bot`** — so datacenter beacons (Azure, Hetzner, AWS)
  that fake a browser UA no longer leak into human counts. `human_sessions` is
  now trustworthy without any client-side join.

### Backfill (after first deploy)
```
aws lambda invoke --function-name <web-analytics-transform> \
  --payload '{"start":"2026-06-19","end":"2026-06-30"}' /dev/stdout
```

## Why the logic lives in the MCP tools, not a Glue view

Beacon parsing, bot/WARP classification, and PT-timezone bucketing are encoded
as SQL **inside the `analytics_*` MCP tools** (`scripts/receipt_mcp_server.py`
and `infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`), not as a
Glue/Athena view. That keeps the analytics logic version-controlled,
diff-reviewable, and testable, and avoids managing Athena view definitions in
IaC.

## MCP tools

| Tool | Purpose |
|---|---|
| `analytics_traffic(start, end)` | per-PT-day requests, outside-human vs WARP vs bot, human sessions/pageviews |
| `analytics_sessions(start, end, humans_only)` | reconstructed beacon sessions with **org / city / country / is_hosting** inline (pages, scroll, reader-summaries) |
| `analytics_top(dimension, start, end)` | top `page` / `referrer` / `ip` / `country` / `org` |
| `analytics_ip(ip, start, end)` | full page-level timeline for one IP |
| `analytics_ga(start, end)` | daily GA4 metrics (sessions/users/pageviews/engaged) |
| `analytics_reconcile(start, end)` | GA4 vs first-party beacon, per day (reveals adblock gap / bot leakage) |
| `analytics_github(start, end)` | GitHub repo traffic: daily views/clones + top referrers/paths (snapshotted past the API's 14-day window) |
| `analytics_query(sql)` | read-only SELECT/WITH escape hatch |

## GitHub traffic (optional)

A scheduled **zip Lambda** (`github_extract_lambda/`, stdlib + boto3 only) pulls
the GitHub Traffic API daily and merges it into the `github_traffic` Glue table
(NDJSON). The API only exposes a **14-day rolling window**, so daily snapshots
are the only way to keep history: daily views/clones are keyed on the event day
(latest reading wins, older days preserved), and referrer/path aggregates are
kept per snapshot so trends are visible over time. Read via `analytics_github`.
Only built when a token is configured:

```
pulumi config set --secret portfolio:githubTrafficToken <PAT>
pulumi config set portfolio:githubTrafficRepos tnorlund/Portfolio
```

The token is a **fine-grained PAT** with **Administration: read** on the target
repos (or a classic token with `repo`). Clones are mostly CI/bot noise; the
referrers + views are the human signal (e.g. explains GitHub-sourced site
visits). Backfill/first run: `aws lambda invoke --function-name
<web-analytics-gh-extract> /dev/stdout`.

## GA4 second source (optional)

A scheduled **container Lambda** (`ga_extract_lambda/`) pulls daily metrics from
the GA4 Data API (service account `ga4-reader`) and overwrites a small NDJSON
file behind the `ga_daily` Glue table. `google-analytics-data` is ~52 MB, so it
ships as a container image (the repo's `CodeBuildDockerImage` pattern) rather
than a layer. It's only built when configured:

```
pulumi config set --secret portfolio:gaServiceAccountKey @ga4-reader-key.json
pulumi config set portfolio:gaPropertyId 542366301
```

GA applies Google's own bot filtering but is blocked by adblock/ITP (it
under-counts); the beacon is complete but raw. `analytics_reconcile` puts them
side by side per day — beacon > GA usually means adblock loss.

## Classification rules (in the tool SQL)

- **WARP / "us":** client IP in `104.28.0.0/16` or `2a09:bac…` (Cloudflare WARP egress)
- **Bot:** user-agent matches a bot/crawler/scanner regex, or the known scanner
  subnet `185.177.72.0/24`
- **Human session:** an analytics-beacon (`/analytics/pixel.txt`) session id,
  excluding WARP + bots

> Note: IP org/geo/hosting is now a **materialized column** on `web_events`
> (enriched once per IP into `ip_geo`), and both `is_hosting` and `is_bot` fold
> in a **datacenter signal** — so `analytics_sessions` returns
> org/city/country/is_hosting directly and `human_sessions` excludes datacenter
> beacons without any client-side lookup.
>
> **Datacenter signal (`_DC_RE` in the transform):** ip-api's `hosting` flag
> alone misses headless-browser scanners that run the beacon (real browser UA,
> `hosting=false`), so a client counts as non-residential if ip-api's `hosting`
> **or** `proxy` flag is set, **or** the network owner (org/isp/asn) matches a
> conservative list of known datacenter/cloud/proxy providers. Residential ISPs
> (Cox, Comcast, Spectrum, TalkTalk) and company names never match, so real
> visitors stay human. Residual gap: a hosting reseller announced from a
> residential ASN (e.g. a "Software LLC" org on a Cox ASN) can still slip
> through — inspect with `analytics_top('country'/'org')` if a spike looks off.
> After changing the rule, **backfill** so history reclassifies:
> `aws lambda invoke --function-name <web-analytics-transform>
> --payload '{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}' /dev/stdout`.

## Deployment

- **Dev** is deployed manually (`pulumi up` on the dev stack); **prod** deploys
  via CI on merge to `main`.
- The Glue DB/table may already exist from validation — if `pulumi up` reports a
  conflict, `pulumi import` the two Glue resources (or delete them) so Pulumi
  can manage them.
- The `analytics_*` MCP tools require the MCP server to pick up the new code:
  redeploy the MCP Lambda, and for the local stdio server, reload it
  (`/mcp` reconnect).

## Cost

Negligible. Athena bills per TB scanned; the logs are KB–MB/day. Results expire
after 30 days.
