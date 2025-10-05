#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
from typing import Iterable, List, Dict, Any, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data._pulumi import load_env as load_pulumi_env


def _parse_time(val: str) -> int:
    """Parse ISO8601 or relative (e.g., -15m, -2h) to epoch millis."""
    val = val.strip()
    now = dt.datetime.now(dt.timezone.utc)
    if val.startswith("-") and val[-1] in {"m", "h", "s"}:
        num = int(val[1:-1])
        unit = val[-1]
        if unit == "s":
            delta = dt.timedelta(seconds=num)
        elif unit == "m":
            delta = dt.timedelta(minutes=num)
        else:
            delta = dt.timedelta(hours=num)
        when = now - delta
        return int(when.timestamp() * 1000)
    # Try ISO8601
    try:
        when = dt.datetime.fromisoformat(val.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        return int(when.timestamp() * 1000)
    except Exception as exc:  # noqa: BLE001 - user input parsing
        raise ValueError(f"Invalid time format: {val}") from exc


def discover_log_groups(logs, prefixes: List[str]) -> List[str]:
    groups: List[str] = []
    for pfx in prefixes:
        print(f"Discovering log groups with prefix: {pfx}", file=sys.stderr)
        token = None
        page_num = 0
        while True:
            page_num += 1
            print(f"  Fetching page {page_num}...", file=sys.stderr)
            kwargs = {"logGroupNamePrefix": pfx}
            if token:
                kwargs["nextToken"] = token
            resp = logs.describe_log_groups(**kwargs)
            page_groups = []
            for lg in resp.get("logGroups", []) or []:
                name = lg.get("logGroupName")
                if name:
                    groups.append(name)
                    page_groups.append(name)
            print(
                f"    Found {len(page_groups)} groups on this page",
                file=sys.stderr,
            )
            token = resp.get("nextToken")
            if not token:
                break
    # Prefer only groups relevant to upload/compaction
    preferred = [
        g
        for g in groups
        if any(
            key in g
            for key in [
                "upload",
                "ocr",
                "chromadb",
                "compaction",
                "stream",
                "worker",
            ]
        )
    ]
    # De-duplicate while preserving order
    seen = set()
    out: List[str] = []
    for g in preferred:
        if g not in seen:
            out.append(g)
            seen.add(g)
    return out


def _pulumi_log_groups(env: Optional[str]) -> List[str]:
    """Attempt to extract CloudWatch log group names from Pulumi stack outputs.

    Strategy: load all outputs, look for any string values that look like
    log group names (start with '/aws/lambda' or '/aws/ecs' or '/ecs/'), or
    outputs whose keys mention both 'log' and 'group'.
    """
    if not env:
        return []
    outputs = load_pulumi_env(env)
    if not isinstance(outputs, dict) or not outputs:
        return []
    groups: List[str] = []
    for key, value in outputs.items():
        if isinstance(value, str):
            if (
                value.startswith("/aws/lambda")
                or value.startswith("/aws/ecs")
                or value.startswith("/ecs/")
            ):
                groups.append(value)
            elif (
                "log" in key.lower() and "group" in key.lower()
            ) and value.startswith("/"):
                groups.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and (
                    item.startswith("/aws/lambda")
                    or item.startswith("/aws/ecs")
                    or item.startswith("/ecs/")
                ):
                    groups.append(item)
    # De-dupe while preserving order
    seen = set()
    result: List[str] = []
    for g in groups:
        if g not in seen:
            result.append(g)
            seen.add(g)
    return result


def _expand_and_filter_groups(
    logs, groups: List[str], env: Optional[str]
) -> List[str]:
    """Expand provided group names via prefix lookup and filter by env.

    - If a provided name is a prefix, expand to all matching concrete groups.
    - If env is provided (e.g., 'dev'), prefer groups containing that token.
      Some groups are env-agnostic; keep those as well.
    """
    expanded: List[str] = []
    for g in groups:
        try:
            resp = logs.describe_log_groups(logGroupNamePrefix=g)
            matches = [
                lg.get("logGroupName")
                for lg in resp.get("logGroups", []) or []
            ]
        except (ClientError, BotoCoreError):
            matches = []
        if matches:
            for m in matches:
                if m and m not in expanded:
                    expanded.append(m)
        else:
            # Keep original to attempt filtering (and to report if missing)
            if g not in expanded:
                expanded.append(g)

    if not env:
        return expanded

    env_token = env.lower()
    allow_any_env_substrings = [
        "dynamodb-lambda-stream",
    ]
    filtered: List[str] = []
    for g in expanded:
        name = (g or "").lower()
        if (
            env_token in name
            or f"-{env_token}-" in name
            or name.endswith(f"-{env_token}")
        ):
            filtered.append(g)
        elif any(token in name for token in allow_any_env_substrings):
            filtered.append(g)
    # If filtering removed all, fall back to expanded
    return filtered or expanded


def filter_events(
    logs,
    group: str,
    start_ms: int,
    end_ms: int,
    terms: List[str],
    max_empty_pages: int = 0,
) -> Iterable[Dict[str, Any]]:
    print(f"Filtering events in {group}...", file=sys.stderr)
    kwargs: Dict[str, Any] = {
        "logGroupName": group,
        "startTime": start_ms,
        "endTime": end_ms,
    }
    # CloudWatch filter pattern: space-separated terms means logical AND
    if terms:
        kwargs["filterPattern"] = " ".join(str(t) for t in terms)
        print(
            f"  Using filter pattern: {kwargs['filterPattern']}",
            file=sys.stderr,
        )
    token = None
    page_num = 0
    total_events = 0
    empty_streak = 0
    while True:
        page_num += 1
        if token:
            kwargs["nextToken"] = token
        try:
            resp = logs.filter_log_events(**kwargs)
        except (ClientError, BotoCoreError) as e:  # noqa: PERF203
            print(f"Error filtering {group}: {e}", file=sys.stderr)
            return
        page_events = len(resp.get("events", []) or [])
        total_events += page_events
        print(f"  Page {page_num}: {page_events} events", file=sys.stderr)
        for ev in resp.get("events", []) or []:
            yield ev
        token = resp.get("nextToken")
        if page_events == 0:
            empty_streak += 1
            if max_empty_pages and empty_streak >= max_empty_pages:
                print(
                    f"  Early stop in {group} after {empty_streak} empty pages",
                    file=sys.stderr,
                )
                break
        else:
            empty_streak = 0
        if not token:
            break
    print(f"  Total events from {group}: {total_events}", file=sys.stderr)


def _resolve_table_name(
    explicit: Optional[str], env: Optional[str]
) -> Optional[str]:
    if explicit:
        return explicit
    # Try environment variable first
    t = os.environ.get("DYNAMODB_TABLE_NAME")
    if t:
        return t
    # Try Pulumi stack outputs as a fallback
    if env:
        outputs = load_pulumi_env(env)
        # common keys to try
        candidate_keys = [
            "dynamodbTableName",
            "DYNAMODB_TABLE_NAME",
            "dynamoTableName",
            "tableName",
        ]
        for k in candidate_keys:
            if k in outputs and outputs[k]:
                return outputs[k]
        # heuristic search
        for k, v in outputs.items():
            if (
                isinstance(v, str)
                and "dynamo" in k.lower()
                and "table" in k.lower()
            ):
                return v
    return None


def _fetch_latest_images(
    table_name: str, count: int, region: str
) -> Tuple[List[str], Optional[int], Optional[int]]:
    """Return latest image_ids and inferred [start_ms, end_ms] window.

    Strategy: fetch a page (e.g., up to 200) via list_images, then sort by
    timestamp_added descending locally and take top N.
    """
    print(
        f"Fetching latest {count} images from {table_name}...", file=sys.stderr
    )
    client = DynamoClient(table_name, region=region)
    # Fetch a few pages to have enough to sort by timestamp
    page_limit = max(count * 5, 50)
    images: List[Any] = []
    lek = None
    page_num = 0
    while len(images) < page_limit:
        page_num += 1
        print(f"  Fetching page {page_num}...", file=sys.stderr)
        page, lek = client.list_images(
            limit=min(200, page_limit - len(images)), last_evaluated_key=lek
        )
        images.extend(page or [])
        print(
            f"    Got {len(page or [])} images, total: {len(images)}",
            file=sys.stderr,
        )
        if not lek:
            break
    # Sort by timestamp_added desc (string ISO format)
    images.sort(
        key=lambda im: getattr(im, "timestamp_added", ""), reverse=True
    )
    latest = images[:count]
    ids = [im.image_id for im in latest]
    # infer time window from oldest -> now
    if latest:
        try:
            oldest_iso = latest[-1].timestamp_added
            oldest_dt = dt.datetime.fromisoformat(
                str(oldest_iso).replace("Z", "+00:00")
            )
            if oldest_dt.tzinfo is None:
                oldest_dt = oldest_dt.replace(tzinfo=dt.timezone.utc)
            start_ms = int(
                (oldest_dt - dt.timedelta(minutes=5)).timestamp() * 1000
            )
            end_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
            return ids, start_ms, end_ms
        except Exception:
            pass
    return ids, None, None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate pipeline logs across services"
    )
    parser.add_argument(
        "--start", default="-1h", help="Start time (ISO8601 or -15m/-2h)"
    )
    parser.add_argument(
        "--end", default=None, help="End time (ISO8601). Defaults to now"
    )
    parser.add_argument(
        "--group",
        action="append",
        help="Log group name (repeatable). If omitted, --discover is used",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Discover groups with prefixes /aws/lambda and /aws/ecs",
    )
    parser.add_argument(
        "--image-id", help="Filter by image_id or IMAGE#... token"
    )
    parser.add_argument("--receipt-id", help="Optional receipt_id filter")
    parser.add_argument("--run-id", help="Optional compaction run_id filter")
    parser.add_argument(
        "--region", default=os.environ.get("AWS_REGION", "us-east-1")
    )
    parser.add_argument(
        "--use-latest-images",
        type=int,
        default=0,
        help="Fetch last N images from DynamoDB and infer time window",
    )
    parser.add_argument(
        "--pulumi-env",
        default=os.environ.get("PULUMI_ENV", None),
        help="Pulumi env (e.g., dev, prod) to resolve table name",
    )
    parser.add_argument(
        "--pulumi-groups",
        action="store_true",
        help="Load CloudWatch log group names from Pulumi stack outputs",
    )
    parser.add_argument(
        "--dynamo-table",
        default=os.environ.get("DYNAMODB_TABLE_NAME", None),
        help="Explicit DynamoDB table name (overrides Pulumi/env)",
    )
    parser.add_argument(
        "--out",
        default="-",
        help="Output CSV file or '-' for stdout (default)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("LOG_AGG_CONCURRENCY", "8")),
        help="Number of log groups to fetch in parallel (default: 8)",
    )
    parser.add_argument(
        "--early-stop-empty-pages",
        type=int,
        default=int(os.environ.get("LOG_AGG_EARLY_STOP_EMPTY_PAGES", "0")),
        help=(
            "If >0, stop scanning a group's pages after this many consecutive empty pages"
        ),
    )
    parser.add_argument(
        "--summary-out",
        default=None,
        help="Optional CSV path to write a timing summary per image (and per receipt when available)",
    )
    parser.add_argument(
        "--per-receipt",
        action="store_true",
        help="Include per-receipt timing rows in the summary output",
    )
    parser.add_argument(
        "--include-receipt-terms",
        action="store_true",
        help="When scanning images from Dynamo, also include their receipt_ids as search terms",
    )

    args = parser.parse_args()

    end_ms = (
        _parse_time(args.end)
        if args.end
        else int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    )
    start_ms = _parse_time(args.start)

    # Initialize filter terms
    terms: List[str] = []

    # Optionally fetch latest images and refine filters/window
    image_ids: List[str] = []
    if args.use_latest_images and args.use_latest_images > 0:
        table_name = _resolve_table_name(args.dynamo_table, args.pulumi_env)
        if not table_name:
            print(
                "Could not resolve DynamoDB table name; set --dynamo-table or --pulumi-env or DYNAMODB_TABLE_NAME",
                file=sys.stderr,
            )
        else:
            try:
                image_ids, inferred_start, inferred_end = _fetch_latest_images(
                    table_name, args.use_latest_images, args.region
                )
                # Add ids to filter terms
                for iid in image_ids:
                    terms.extend([f"image_id={iid}", iid])
                # If no explicit --start/--end given, adopt inferred
                if args.start == "-1h" and inferred_start is not None:
                    start_ms = inferred_start
                if args.end is None and inferred_end is not None:
                    end_ms = inferred_end
            except Exception as e:
                print(f"Failed to fetch latest images: {e}", file=sys.stderr)

    logs = boto3.client("logs", region_name=args.region)

    groups: List[str] = args.group or []
    if not groups and args.pulumi_groups:
        groups = _pulumi_log_groups(args.pulumi_env)
        # Fallback to discovery if Pulumi did not export any log groups
        if not groups:
            groups = discover_log_groups(
                logs, ["/aws/lambda", "/aws/ecs", "/ecs/"]
            )
            if not groups:
                print("No log groups discovered", file=sys.stderr)
                sys.exit(2)
    if not groups and args.discover:
        groups = discover_log_groups(
            logs, ["/aws/lambda", "/aws/ecs", "/ecs/"]
        )
        if not groups:
            print("No log groups discovered", file=sys.stderr)
            sys.exit(2)
    if not groups:
        print(
            "Provide --group, --pulumi-groups, or --discover", file=sys.stderr
        )
        sys.exit(2)

    # Expand prefixes and prefer env-specific groups when --pulumi-env is set
    groups = _expand_and_filter_groups(logs, groups, args.pulumi_env)

    if args.image_id:
        # Match both JSON and tokenized forms
        terms.extend([f"image_id={args.image_id}", args.image_id])
    if args.receipt_id:
        terms.append(f"receipt_id={args.receipt_id}")
    if args.run_id:
        terms.append(args.run_id)

    rows: List[Dict[str, Any]] = []
    print(
        f"Processing {len(groups)} log groups with concurrency={args.concurrency}...",
        file=sys.stderr,
    )

    def _process_all_groups(current_terms: List[str]) -> List[Dict[str, Any]]:
        out_rows: List[Dict[str, Any]] = []

        def _process_group(group_name: str) -> List[Dict[str, Any]]:
            group_rows: List[Dict[str, Any]] = []
            for ev in filter_events(
                logs,
                group_name,
                start_ms,
                end_ms,
                current_terms,
                max_empty_pages=max(0, args.early_stop_empty_pages),
            ):
                ts_ms = ev.get("timestamp")
                ts = dt.datetime.fromtimestamp(
                    ts_ms / 1000.0, tz=dt.timezone.utc
                )
                group_rows.append(
                    {
                        "timestamp": ts.isoformat(),
                        "logGroup": group_name,
                        "logStream": ev.get("logStreamName", ""),
                        "message": ev.get("message", "").rstrip(),
                    }
                )
            return group_rows

        with ThreadPoolExecutor(
            max_workers=max(1, args.concurrency)
        ) as executor:
            future_to_group = {
                executor.submit(_process_group, g): g for g in groups
            }
            completed = 0
            for future in as_completed(future_to_group):
                g = future_to_group[future]
                try:
                    group_rows = future.result()
                except Exception as e:  # noqa: BLE001 - robust parallel fetch
                    print(f"Error processing group {g}: {e}", file=sys.stderr)
                    group_rows = []
                out_rows.extend(group_rows)
                completed += 1
                print(
                    f"  Completed {completed}/{len(groups)} groups; total rows so far: {len(out_rows)}",
                    file=sys.stderr,
                )
        return out_rows

    # Helper: optionally get receipt IDs for an image from Dynamo
    def _receipt_ids_for_image(img_id: str) -> List[str]:
        table_name = _resolve_table_name(args.dynamo_table, args.pulumi_env)
        if not table_name:
            return []
        try:
            client = DynamoClient(table_name, region=args.region)
            _image, _lines, receipts = client.get_image_cluster_details(img_id)
            return [r.receipt_id for r in receipts]
        except Exception:  # noqa: BLE001 - best-effort enrichment
            return []

    # When scanning multiple latest images, query per-image (and per-receipt) to avoid AND filter semantics
    if image_ids and len(image_ids) > 1:
        print(
            f"Scanning {len(image_ids)} images individually to avoid AND filter semantics",
            file=sys.stderr,
        )
        for iid in image_ids:
            print(f"-- Image {iid} --", file=sys.stderr)
            if args.include_receipt_terms:
                rids = _receipt_ids_for_image(iid)
                if rids:
                    for rid in rids:
                        terms_for_receipt = [
                            f"image_id={iid}",
                            iid,
                            f"receipt_id={rid}",
                            str(rid),
                        ]
                        rows.extend(_process_all_groups(terms_for_receipt))
                    continue
            # Fallback: just image terms
            rows.extend(_process_all_groups([f"image_id={iid}", iid]))
    else:
        rows.extend(_process_all_groups(terms))

    rows.sort(key=lambda r: r["timestamp"])  # stable by iso

    # Optional summary output (per image and optionally per receipt)
    if args.summary_out:
        uuid_pat = r"[0-9a-fA-F-]{36}"
        img_pat_1 = re.compile(rf"image_id=({uuid_pat})")
        img_pat_2 = re.compile(rf"IMAGE#({uuid_pat})")
        rct_pat_1 = re.compile(rf"receipt_id=({uuid_pat})")
        rct_pat_2 = re.compile(rf"RECEIPT#({uuid_pat})")

        per_image_bounds: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        per_receipt_bounds: Dict[
            str, Tuple[Optional[str], Optional[str], Optional[str]]
        ] = {}

        for r in rows:
            ts = r["timestamp"]
            msg = r["message"]
            image_id = None
            receipt_id = None
            m = img_pat_1.search(msg) or img_pat_2.search(msg)
            if m:
                image_id = m.group(1)
            m = rct_pat_1.search(msg) or rct_pat_2.search(msg)
            if m:
                receipt_id = m.group(1)
            if image_id:
                start, end = per_image_bounds.get(image_id, (None, None))
                if start is None or ts < start:
                    start = ts
                if end is None or ts > end:
                    end = ts
                per_image_bounds[image_id] = (start, end)
            if args.per_receipt and receipt_id:
                img_ref = image_id
                img_old, start_r, end_r = per_receipt_bounds.get(
                    receipt_id, (img_ref, None, None)
                )
                if start_r is None or ts < start_r:
                    start_r = ts
                if end_r is None or ts > end_r:
                    end_r = ts
                per_receipt_bounds[receipt_id] = (
                    img_old or img_ref,
                    start_r,
                    end_r,
                )

        with open(args.summary_out, "w", newline="", encoding="utf-8") as sfh:
            writer = csv.writer(sfh)
            if args.per_receipt:
                writer.writerow(
                    [
                        "type",
                        "image_id",
                        "receipt_id",
                        "start",
                        "end",
                        "duration_seconds",
                    ]
                )
            else:
                writer.writerow(
                    ["type", "image_id", "start", "end", "duration_seconds"]
                )
            for iid, (start_i, end_i) in per_image_bounds.items():
                duration = None
                if start_i and end_i:
                    duration = (
                        dt.datetime.fromisoformat(end_i).timestamp()
                        - dt.datetime.fromisoformat(start_i).timestamp()
                    )
                writer.writerow(
                    [
                        "image",
                        iid,
                        start_i,
                        end_i,
                        f"{duration:.3f}" if duration is not None else "",
                    ]
                )
            if args.per_receipt:
                for rid, (
                    img_ref,
                    start_r,
                    end_r,
                ) in per_receipt_bounds.items():
                    duration = None
                    if start_r and end_r:
                        duration = (
                            dt.datetime.fromisoformat(end_r).timestamp()
                            - dt.datetime.fromisoformat(start_r).timestamp()
                        )
                    writer.writerow(
                        [
                            "receipt",
                            img_ref or "",
                            rid,
                            start_r,
                            end_r,
                            f"{duration:.3f}" if duration is not None else "",
                        ]
                    )

    out_fh = (
        sys.stdout
        if args.out == "-"
        else open(args.out, "w", newline="", encoding="utf-8")
    )
    with out_fh:
        writer = csv.DictWriter(
            out_fh,
            fieldnames=["timestamp", "logGroup", "logStream", "message"],
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
