# Web Analytics (Glue + Athena over CloudFront logs)

Makes the production CloudFront access logs **queryable** so Claude (via the
`analytics_*` MCP tools) can answer "how much real traffic did the site get,
who visited, from where" without hand-parsing gzip logs.

No new data pipeline — it's a thin query layer over logs that already exist.

## What this creates

- **Glue database** `portfolio_analytics`
- **Glue external table** `cloudfront_logs_prod` over
  `s3://<cloudfront-logs-bucket>/cloudfront/prod/` (gzip TSV, 2 header lines
  skipped; column order matches the AWS CloudFront standard log format)
- **Athena workgroup** `portfolio_analytics` with a dedicated **results bucket**
  (objects expire after 30 days)
- **IAM policy** granting read-only Athena/Glue/S3 access, attached in
  `__main__.py` to the MCP server's Lambda role

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
| `analytics_query(sql)` | read-only SELECT/WITH escape hatch |

## Classification rules (in the tool SQL)

- **WARP / "us":** client IP in `104.28.0.0/16` or `2a09:bac…` (Cloudflare WARP egress)
- **Bot:** user-agent matches a bot/crawler/scanner regex, or the known scanner
  subnet `185.177.72.0/24`
- **Human session:** an analytics-beacon (`/analytics/pixel.txt`) session id,
  excluding WARP + bots

> Note: IP-level geo/org (e.g. "this IP is LangChain") is **not** in the SQL —
> the `analytics_ip` tool returns the raw activity; org/geo is a separate
> external lookup. Datacenter/residential-bot beacons that fake a browser UA can
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
