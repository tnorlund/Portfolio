# Web Analytics (Glue + Athena over CloudFront logs)

Makes the production CloudFront access logs **queryable** so Claude (via the
`analytics_*` MCP tools) can answer "how much real traffic did the site get,
who visited, from where" without hand-parsing gzip logs.

No new data pipeline — it's a thin query layer over logs that already exist.

## Architecture (ETL + serving)

```
INGEST     CloudFront → s3://<logs-bucket>/cloudfront/prod/   (already exists)
              │
TRANSFORM   transform Lambda (scheduled daily + backfillable), Athena INSERT:
              parse beacon · dedup(request_id) · classify warp/bot · PT-ready
              → web_events  (Parquet, partitioned by UTC `dt`)   [single source of truth]
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
  per-day partition rebuild; scheduled daily, and invokable with
  `{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}` to backfill
- **Athena workgroup** `portfolio_analytics` + a **results bucket** (30-day expiry)
- A **curated bucket** for the Parquet output
- **IAM policies** — read-only Athena/Glue/S3 for the MCP Lambda role; a
  read-raw / write-curated / manage-partitions policy for the transform Lambda

## Transform details

- Reads **only the target day's** raw files via the Athena `$path` pseudo-column
  (`WHERE "$path" LIKE '%.<dt>-%'`), so scan cost stays flat as history grows.
- **Idempotent:** drops the `dt` partition + deletes its S3 objects, then
  re-`INSERT`s — safe to re-run / backfill.
- **Dedups** on `request_id` (CloudFront can re-deliver lines).
- Partition is the **UTC** log date; the serve layer buckets to PT and widens
  the scan ±1 day so PT-day edges are correct.

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
| `analytics_sessions(start, end, humans_only)` | reconstructed beacon sessions (pages, scroll, reader-summaries) |
| `analytics_top(dimension, start, end)` | top `page` / `referrer` / `ip` |
| `analytics_ip(ip, start, end)` | full page-level timeline for one IP |
| `analytics_lookup_ip(ips)` | live org / geo / network-type (hosting/proxy) for IPs — who a visitor is |
| `analytics_query(sql)` | read-only SELECT/WITH escape hatch |

## Classification rules (in the tool SQL)

- **WARP / "us":** client IP in `104.28.0.0/16` or `2a09:bac…` (Cloudflare WARP egress)
- **Bot:** user-agent matches a bot/crawler/scanner regex, or the known scanner
  subnet `185.177.72.0/24`
- **Human session:** an analytics-beacon (`/analytics/pixel.txt`) session id,
  excluding WARP + bots

> Note: IP-level geo/org (e.g. "this IP is LangChain") is intentionally **not**
> a materialized column — at this volume it's freshest on demand. Use the
> `analytics_lookup_ip` tool (live IP-info lookup) to resolve org / geo /
> hosting flags for IPs surfaced by the other tools.
> Datacenter/residential-bot beacons that fake a browser UA can
> still slip into `human_sessions`; treat that count as an upper bound and
> confirm individual IPs with `analytics_ip` + an IP-info service.

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
