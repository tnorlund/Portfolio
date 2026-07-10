# Web Analytics (real-time DynamoDB + durable CloudFront batch)

Provides two complementary production analytics layers so the `analytics_*`
MCP tools can answer both "who is here now?" and durable historical questions
without hand-parsing CloudFront logs:

- **Live overlay:** request-time collection into TTL'd DynamoDB, queryable in
  seconds with edge geo and UTM/referrer attribution.
- **Durable batch:** the existing daily CloudFront-log transform into curated
  Parquet. This remains the source of truth and is not replaced by DynamoDB.

## Architecture

```
BROWSER     /analytics/pixel.txt (one canonical eid-bearing request)
              → CloudFront viewer headers + caching disabled
              → origin group
                   ├─ primary: signed Function URL → collector Lambda
                   │    → web_events_live (DynamoDB, ~90-day TTL)
                   │    → analytics_live / analytics_attribution ─┐
                   └─ failover: existing static S3 pixel (always 200)
              → CloudFront standard logs in S3
              → unchanged daily transform + ip_geo enrichment
              → web_events (Parquet) → existing analytics_* tools ─┤
                                                                  └→ analytics_auto
```

## What this creates

- **Glue database** `portfolio_analytics`
- **Live DynamoDB table** `web_events_live` — on-demand capacity, UTC date
  partition key (`dt`), time/session/event sort key (`sk`), keys-only
  `sid-index`, and an `expires_at` TTL of approximately 90 days
- **Collector Lambda + Function URL** — invoked only through a SigV4-signed
  CloudFront origin access control; writes one request-time event to DynamoDB;
  its CloudWatch log group has 30-day retention
- **CloudFront `/analytics/pixel.txt` behavior + origin group** — caching
  disabled; the Lambda URL is primary and the existing static S3 object is the
  failover; query strings are forwarded without cookies and viewer geo/ASN
  headers are added at the edge
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
- **IAM policies** — Athena/Glue/S3 plus live-table `Query`/`GetItem` for the
  MCP Lambda role; live-table `PutItem` only for the collector; and the
  existing read-raw / write-curated / manage-partitions policy for the
  transform Lambda

## Transform details

- Reads **only the target day's** raw files via the Athena `$path` pseudo-column
  (`WHERE "$path" LIKE '%.<dt>-%'`), so scan cost stays flat as history grows.
- **Idempotent:** drops the `dt` partition + deletes its S3 objects, then
  re-`INSERT`s — safe to re-run / backfill.
- **Dedups** on beacon `eid` when present, otherwise `request_id` (CloudFront
  can re-deliver lines and browsers can retry a beacon).
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

## Real-time collection and attribution

Production builds keep
`NEXT_PUBLIC_CLOUDFRONT_ANALYTICS_BEACON_PATH=/analytics/pixel.txt`. The browser
automatically adds `utm_source`, `utm_medium`, `utm_campaign`, and
`document.referrer` (`ref`) to each live beacon. Referrers are reduced to
HTTP(S) origin + pathname so query tokens and fragments are not copied into
the beacon URL. Outreach links should use a stable campaign name, for example:

```
https://tylernorlund.com/receipt?utm_source=li&utm_medium=dm&utm_campaign=arthur-babylist
```

The canonical pixel path is intentional. A separate collector path would need
a second pixel request to preserve the unchanged batch pipeline, inflating
existing request/IP/top metrics. Origin failover keeps one request and retains
the static object's non-breaking 200 response.

CloudFront forwards the query string, user agent, viewer address/ASN, and the
`CloudFront-Viewer-Country`, `-Country-Region`, `-City`, `-Latitude`,
`-Longitude`, and `-Time-Zone` headers. The collector percent-decodes and
persists available fields directly; it performs **no external geo lookup**.
City can be unavailable for some IPs, and detailed location headers can be
absent for AWS-network viewers, so those fields are nullable. The edge ASN is
also checked against a conservative set of the datacenter/cloud providers
represented by the batch classifier.

Each event makes one same-origin, fire-and-forget request. CloudFront first
tries the signed collector Function URL; on configured 4xx/5xx failures it
retries the same GET against the existing S3 origin, where the static pixel
returns 200. Malformed/no-op requests receive a cache-disabled 1x1 GIF from the
collector. Persistence errors are re-raised so Lambda's error metric records
the failure and CloudFront exercises the static 200 fallback. CloudFront
standard logging records that same canonical request for the unchanged batch
pipeline, so the live overlay neither doubles nor steals events from existing
metrics.

Live items use `dt` (UTC) and
`sk=<13-digit epoch_ms>#<sid>#<eid>`, plus a `sid-index` for session access.
`analytics_live` queries only the intersecting UTC date partitions with
strongly consistent DynamoDB reads and deduplicates retries by `eid`, matching
the durable transform. `analytics_attribution` starts a campaign segment at
the matching tagged landing event and retains subsequent untagged pages until
another tagged campaign begins. If a browser later returns to the requested
campaign after a different campaign, the tool reports a separate segment
rather than spanning the intervening visit.

Examples:

```
analytics_auto(minutes=60)
analytics_auto(since="2026-07-01T00:00:00Z")
analytics_auto(campaign="arthur-babylist")
analytics_live(minutes=60, humans_only=true)
analytics_attribution(campaign="arthur-babylist")
analytics_attribution(since="2026-07-10T17:00:00Z")
```

The live table is an operational overlay, not a historical replacement. TTL
removes old items; use `web_events` and the existing Athena-backed tools for
durable analysis.

### Unified MCP routing

`analytics_auto` is the default read surface when a visitor question may cross
the live/durable boundary. It accepts a UTC `[since, until)` interval (or a
minute lookback), then:

1. Finds the latest materialized durable event using Glue partition metadata
   plus a partition-pruned Athena `max(ts_utc)` query, cached for five minutes
   on warm MCP processes.
2. Reads the older interval from `web_events` and DynamoDB across a four-day
   overlap matching the transform's trailing repair horizon.
3. Reconstructs `ref`, UTM, and event metrics from the existing durable
   `query_decoded` field. No batch schema or transform change is needed.
4. Merges events before filtering/rollup and de-duplicates only nonempty
   `eid`s. Durable classification/org data win, live millisecond ordering is
   retained, and other live-only edge fields fill durable gaps.
5. Returns `source` (`live`, `durable`, or `mixed`), freshness, truncation,
   errors, and explicit queried/unqueried ranges. These describe which stores
   were queried; they do not claim DynamoDB contained pre-rollout history.

Campaign-only calls default to 24 hours, while other calls default to 60
minutes. Explicit `since` values can reach beyond the live table's TTL because
the older portion is served by Athena, with a one-year maximum interval to
bound scan cost. Existing tools remain unchanged and available when a caller
explicitly wants one source.

## Where classification and query logic live

The durable transform owns batch beacon parsing, deduplication, geo enrichment,
and bot/WARP/hosting classification. Existing Athena-backed MCP tools query the
materialized `web_events` fields. The collector mirrors the same WARP, bot-UA,
known-scanner, and datacenter-provider intent at request time using only edge
data; the two live MCP tools explicitly exclude `is_bot`, `is_warp`, and
`is_hosting` when `humans_only=true`. All tool definitions remain duplicated
in lockstep in `scripts/receipt_mcp_server.py` and
`infra/mcp_server_lambda/lambdas/receipt_mcp_server_server.py`.

## MCP tools

| Tool | Purpose |
|---|---|
| `analytics_auto(minutes, since, until, campaign, humans_only=true)` | default unified visitor/session query; routes and merges live DynamoDB with durable Athena data |
| `analytics_live(minutes=60, humans_only=true)` | request-time sessions from `web_events_live`, with edge geo, referrer, UTM fields, pages, and events |
| `analytics_attribution(campaign, since, humans_only=true)` | exact live campaign/time-window attribution with full-session page timelines |
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

## Classification rules

- **WARP / "us":** client IP in `104.28.0.0/16` or `2a09:bac…` (Cloudflare WARP egress)
- **Bot:** user-agent matches a bot/crawler/scanner regex, or the known scanner
  subnet `185.177.72.0/24`
- **Hosting:** batch uses the materialized `ip_geo` hosting/proxy/owner signal;
  live collection uses `CloudFront-Viewer-ASN` and a conservative provider ASN
  set because CloudFront does not expose org/ISP or a hosting flag
- **Human session:** an analytics-beacon (`/analytics/pixel.txt`) session id,
  excluding WARP + bots/hosting in batch; live tools explicitly exclude all
  three flags

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

- **Dev** is deployed manually (`pulumi up` on the dev stack). **Prod** deploys
  only from a manually dispatched CI run on `main`, after all test jobs pass.
- The live table, collector, and `/analytics/pixel.txt` origin-group behavior
  are production only. The browser path does not change during rollout.
- Keep `portfolio/public/analytics/pixel.txt`. It is both the no-op fallback
  and the canonical path captured by durable logs; removing it breaks
  non-breaking origin failover.
- The Glue DB/table may already exist from validation — if `pulumi up` reports a
  conflict, `pulumi import` the two Glue resources (or delete them) so Pulumi
  can manage them.
- The `analytics_*` MCP tools require the MCP server to pick up the new code:
  redeploy the MCP Lambda, and for the local stdio server, reload it
  (`/mcp` reconnect).

### Post-deploy smoke and rollback

Runtime acceptance remains pending until the manual production deployment.
Use a normal, non-AWS-network browser for the geo assertion because CloudFront
can omit detailed geo fields for AWS viewers.

1. Open a unique tagged URL such as
   `/receipt?utm_source=smoke&utm_medium=manual&utm_campaign=smoke-<UTC epoch>`.
2. Within five seconds, call `analytics_live(minutes=5, humans_only=false)` and
   assert one deduplicated `page_view` with the expected path and nonempty
   country; assert region/city when CloudFront supplies them.
3. Call `analytics_attribution(campaign="smoke-<UTC epoch>",
   humans_only=false)` and assert the same session, referrer, and campaign.
4. Call `analytics_auto(campaign="smoke-<UTC epoch>", humans_only=false)` and
   assert `source` includes `live`, no unexpected unqueried range is reported,
   and the same session is returned.
5. Check the collector Lambda `Errors` metric and log group, then verify the
   DynamoDB item has `expires_at`, edge geo, `sid`, and `eid`.
6. In a controlled fallback test, temporarily set the collector's reserved
   concurrency to zero, request `/analytics/pixel.txt?...`, and verify the
   CloudFront response is still 200 from S3. Restore concurrency to 5
   immediately afterward.

For an emergency collector rollback, setting reserved concurrency to zero
routes eligible beacon failures to the static pixel while the CloudFront
behavior is reverted through the normal manual Pulumi review. Do not delete
`portfolio/public/analytics/pixel.txt`.

### Security and abuse boundary

The beacon path is intentionally public. Reserved Lambda concurrency bounds
simultaneous compute but is not a request-rate or DynamoDB cost ceiling; watch
Lambda invocations/errors and DynamoDB consumed requests, and use a URI-scoped
WAF rate rule if traffic stops matching portfolio-scale usage.

The existing receipt MCP Function URL currently uses unauthenticated public
access. Because the live tools expose visitor telemetry, authenticating or
isolating that remote endpoint is a release decision before enabling its live
table read policy. The local stdio MCP server continues to use local AWS
credentials.

## Cost

Negligible at portfolio traffic: DynamoDB uses on-demand capacity, the
collector is one small Lambda invocation per event, and Athena scans
KB–MB/day. Live items expire after approximately 90 days; Athena results expire
after 30 days.
