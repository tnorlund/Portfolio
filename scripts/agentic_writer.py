#!/usr/bin/env python3
"""P3 writer: single-flight, guarded application of adjudicated verdicts.

The ONLY component of the agentic review loop with a DynamoDB write
path (docs/line-items/agentic-review/OPERATING_MODEL.md). It consumes
`.dev-harness/verdicts/<pass-id>.jsonl` and applies:

- every T0 entry (auto-apply tier), and
- every T1 entry whose merchant x mode group the human approved in
  `.dev-harness/approvals/<pass-id>.json` (`approved_groups`).

Golden entries are NEVER applied here, approved or not.

One receipt at a time, through the same guarded path as the MCP tool
(``extend_items_section_impl``: arithmetic guard, validation_status
preserved, summary ``timestamp_computed`` bumped so the stream stage
regenerates line items), plus a ``+agentic-vision-v1`` provenance
suffix on the section's ``model_source``. After each apply the writer
waits for the stream, re-reads the line items, and CONFIRMS the
observed delta matches the dry-run prediction. ANY divergence — a
guard refusal at write time, an apply error, or a delta that lands
differently than predicted — halts the whole run, writes a divergence
report, and exits nonzero. Never a retry.

Safety rails: ``--dry-run`` is the default (``--apply`` required for
writes); a lockfile in `.dev-harness/writer.lock` enforces single
flight; the prod table (d7ff76a) is refused outright; and freeze
markers in `.dev-harness/freeze/` (tier names or A-J mode-class
letters, same semantics as the adjudicator's) are honored here too —
re-read before EVERY write, so an audit-deck disagreement landing
mid-session stops the remaining entries of that class immediately.

Duplicate retirement (destructive; T2 sign-off required):

    agentic_writer.py retire-duplicate --group-file <group.json> \
        --approvals-file .dev-harness/approvals/<pass-id>.json \
        --raw-bucket <raw-bucket> --apply

Backup-first, following the rewarp recipe: every raw-bucket object
under the victim image is server-side copied to
``rewarp-backups/retired-<UTC>/`` in the raw bucket (NEVER the site
bucket — the prod deploy's sync --delete wipes non-build prefixes),
and every DynamoDB row of the image is exported to a local JSON file,
before the delete_image path runs. The group must be listed under
``t2_retirements`` in the approvals file.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = REPO_ROOT / ".dev-harness"
DEFAULT_VERDICTS_DIR = HARNESS_DIR / "verdicts"
DEFAULT_APPROVALS_DIR = HARNESS_DIR / "approvals"
DEFAULT_BACKUP_DIR = HARNESS_DIR / "backups"
DEFAULT_FREEZE_DIR = HARNESS_DIR / "freeze"
DEFAULT_LOCK_PATH = HARNESS_DIR / "writer.lock"

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_TABLE_FRAGMENT = "d7ff76a"  # hard refusal; this loop is dev-only
PROVENANCE_SUFFIX = "agentic-vision-v1"
DELTA_CONFIRM_TOLERANCE = 0.005

DEFAULT_POLL_ATTEMPTS = 10
DEFAULT_POLL_INTERVAL = 3.0


class WriterLockedError(RuntimeError):
    """Another writer holds the single-flight lock."""


class ProdTableRefusedError(RuntimeError):
    """The writer refuses to touch the prod table, ever."""


class DivergenceError(RuntimeError):
    """The world did not move as the dry run predicted. Halt, report."""

    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def guard_table(table_name: str) -> None:
    if PROD_TABLE_FRAGMENT in (table_name or ""):
        raise ProdTableRefusedError(
            f"refusing table {table_name!r}: the agentic writer is "
            "dev-only (prod table d7ff76a is hard-blocked)"
        )


class WriterLock:
    """Single-flight lockfile: O_CREAT|O_EXCL, removed on exit."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: Optional[int] = None

    def __enter__(self) -> "WriterLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise WriterLockedError(
                f"writer lock already held: {self.path} (remove it only "
                "if you are certain no other writer is running)"
            ) from None
        os.write(
            self._fd,
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            ).encode("utf-8"),
        )
        return self

    def __exit__(self, *_exc) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _load_by_path(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_helpers():
    """Load scripts/agentic_triage_helpers.py (shares the MCP loader)."""
    return _load_by_path(
        "_agentic_writer_helpers", "agentic_triage_helpers.py"
    )


def _load_adjudicator():
    """Load scripts/agentic_adjudicate.py for its freeze semantics.

    ``load_frozen`` (marker names in the freeze dir: tier names or A-J
    class letters) and ``mode_class`` are imported, never mirrored, so
    the writer and the adjudicator can never disagree about what a
    freeze means.
    """
    return _load_by_path(
        "_agentic_writer_adjudicator", "agentic_adjudicate.py"
    )


def _frozen_marker(entry: dict[str, Any], frozen: set[str]) -> Optional[str]:
    """The freeze marker hitting this entry's tier or mode class."""
    if not frozen:
        return None
    adjudicator = _load_adjudicator()
    cls = adjudicator.mode_class(entry)
    return next(
        (name for name in (entry.get("tier"), cls) if name in frozen),
        None,
    )


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def load_verdicts(path: Path) -> list[dict[str, Any]]:
    entries = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_no}: invalid verdict line: {exc}"
                ) from exc
    return entries


def load_approvals(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"approved_groups": [], "t2_retirements": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: approvals file must be a JSON object")
    return data


def select_applicable(
    entries: list[dict[str, Any]],
    approvals: dict[str, Any],
    frozen: set[str] = frozenset(),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(to_apply, skipped). T0 always; T1 only when its group is
    approved; golden never; nothing whose tier or mode class carries a
    freeze marker (an audit disagreement outranks any verdict)."""
    approved_groups = set(approvals.get("approved_groups") or [])
    to_apply, skipped = [], []
    for entry in entries:
        proposal = entry.get("proposal") or {}
        reason = None
        marker = _frozen_marker(entry, frozen)
        if marker is not None:
            reason = f"frozen:{marker}"
        elif entry.get("golden"):
            reason = "golden-never-auto-applied"
        elif entry.get("tier") == "T0":
            pass
        elif entry.get("tier") == "T1":
            if entry.get("group_id") not in approved_groups:
                reason = "t1-group-not-approved"
        else:
            reason = f"tier-{entry.get('tier')}-not-writable"
        if reason is None and not (
            proposal.get("verified") and proposal.get("add_line_ids")
        ):
            reason = "no-verified-proposal"
        if reason is None:
            to_apply.append(entry)
        else:
            skipped.append({**entry, "skip_reason": reason})
    return to_apply, skipped


def _stamp_provenance(client, image_id: str, receipt_id: int) -> str:
    """Append +agentic-vision-v1 to the ITEMS section's model_source.

    Cosmetic provenance only — the guard math and the apply itself
    live in extend_items_section_impl and are not reimplemented here.
    """
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    sections = (
        client.get_receipt_sections_from_receipt(image_id, receipt_id) or []
    )
    items = next((s for s in sections if s.section_type == "ITEMS"), None)
    if items is None:  # pragma: no cover - apply just succeeded
        raise RuntimeError("ITEMS section vanished after apply")
    model_source = items.model_source or ""
    if PROVENANCE_SUFFIX in model_source:
        return model_source
    model_source = (
        f"{model_source}+{PROVENANCE_SUFFIX}"
        if model_source
        else PROVENANCE_SUFFIX
    )
    client.update_receipt_section(
        ReceiptSection(
            receipt_id=items.receipt_id,
            image_id=items.image_id,
            section_type="ITEMS",
            line_ids=items.line_ids,
            created_at=items.created_at,
            confidence=items.confidence,
            model_source=model_source,
            validation_status=items.validation_status,
            row_ids=items.row_ids,
        )
    )
    return model_source


def apply_one(
    mcp_server,
    client,
    entry: dict[str, Any],
    *,
    apply: bool,
    poll_attempts: int = DEFAULT_POLL_ATTEMPTS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Apply one verdict through the guarded path and confirm it.

    Raises DivergenceError on ANY deviation from the prediction; the
    caller halts the whole run.
    """
    image_id = entry["image_id"]
    receipt_id = int(entry["receipt_id"])
    add_line_ids = [int(x) for x in entry["proposal"]["add_line_ids"]]
    where = {"image_id": image_id, "receipt_id": receipt_id}

    dry = _run(
        mcp_server.extend_items_section_impl(
            client, image_id, receipt_id, add_line_ids, dry_run=True
        )
    )
    if dry.get("error") or not dry.get("verified"):
        raise DivergenceError(
            "guard refused at write time (world changed since "
            "adjudication)",
            {
                **where,
                "kind": "guard-refusal",
                "add_line_ids": add_line_ids,
                "refusal": dry.get("refusal") or dry.get("error"),
            },
        )
    predicted = dry.get("after") or {}
    record = {
        **where,
        "add_line_ids": add_line_ids,
        "predicted_status": predicted.get("status"),
        "predicted_delta": predicted.get("delta"),
        "applied": False,
        "confirmed": False,
    }
    if not apply:
        record["note"] = "dry-run: guard verified, nothing written"
        return record

    applied = _run(
        mcp_server.extend_items_section_impl(
            client, image_id, receipt_id, add_line_ids, dry_run=False
        )
    )
    if applied.get("error") or not applied.get("applied"):
        raise DivergenceError(
            "apply failed after a passing dry run",
            {
                **where,
                "kind": "apply-failed",
                "add_line_ids": add_line_ids,
                "error": applied.get("error")
                or applied.get("refusal")
                or "not applied",
            },
        )
    record["applied"] = True
    record["validation_status"] = applied.get("validation_status")
    record["model_source"] = _stamp_provenance(client, image_id, receipt_id)

    # The apply bumped the summary timestamp; the stream stage now
    # regenerates the line items. Wait for it, then CONFIRM the delta
    # landed exactly as the dry run predicted.
    predicted_delta = predicted.get("delta")
    observed_delta = None
    for attempt in range(poll_attempts):
        if attempt:
            sleep(poll_interval)
        view = _run(
            mcp_server.get_receipt_line_items_impl(
                client, image_id, receipt_id
            )
        )
        observed_delta = view.get("delta")
        if (
            observed_delta is not None
            and predicted_delta is not None
            and abs(observed_delta - predicted_delta) < DELTA_CONFIRM_TOLERANCE
        ):
            record["confirmed"] = True
            record["observed_delta"] = observed_delta
            return record

    raise DivergenceError(
        "post-apply delta diverged from the dry-run prediction",
        {
            **where,
            "kind": "delta-divergence",
            "add_line_ids": add_line_ids,
            "predicted_delta": predicted_delta,
            "observed_delta": observed_delta,
            "poll_attempts": poll_attempts,
        },
    )


def _write_divergence_report(
    out_dir: Path, pass_id: str, report: dict[str, Any]
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{pass_id}.divergence.json"
    path.write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return path


def run_apply(
    pass_id: str,
    *,
    client,
    mcp_server,
    table_name: str,
    apply: bool,
    verdicts_dir: Path = DEFAULT_VERDICTS_DIR,
    approvals_dir: Path = DEFAULT_APPROVALS_DIR,
    freeze_dir: Path = DEFAULT_FREEZE_DIR,
    lock_path: Path = DEFAULT_LOCK_PATH,
    poll_attempts: int = DEFAULT_POLL_ATTEMPTS,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    guard_table(table_name)
    verdicts_path = Path(verdicts_dir) / f"{pass_id}.jsonl"
    if not verdicts_path.exists():
        print(f"no verdicts file: {verdicts_path}", file=sys.stderr)
        return 1
    entries = load_verdicts(verdicts_path)
    approvals = load_approvals(Path(approvals_dir) / f"{pass_id}.json")
    adjudicator = _load_adjudicator()
    to_apply, skipped = select_applicable(
        entries, approvals, frozen=adjudicator.load_frozen(Path(freeze_dir))
    )

    applied_records: list[dict[str, Any]] = []
    processed = 0
    with WriterLock(lock_path):
        for entry in to_apply:
            processed += 1
            # Re-read the freeze dir before EVERY write: an audit deck
            # disagreement mid-session must stop the writer from
            # applying the very class the audit just condemned, not
            # merely the adjudicator's next run.
            marker = _frozen_marker(
                entry, adjudicator.load_frozen(Path(freeze_dir))
            )
            if marker is not None:
                skipped.append({**entry, "skip_reason": f"frozen:{marker}"})
                continue
            try:
                applied_records.append(
                    apply_one(
                        mcp_server,
                        client,
                        entry,
                        apply=apply,
                        poll_attempts=poll_attempts,
                        poll_interval=poll_interval,
                        sleep=sleep,
                    )
                )
            except DivergenceError as exc:
                report = {
                    "pass_id": pass_id,
                    "halted_at": datetime.now(timezone.utc).isoformat(),
                    "divergence": exc.report,
                    "applied_before_halt": applied_records,
                    "remaining_unapplied": [
                        {
                            "image_id": e.get("image_id"),
                            "receipt_id": e.get("receipt_id"),
                        }
                        for e in to_apply[processed:]
                    ],
                }
                path = _write_divergence_report(
                    Path(verdicts_dir), pass_id, report
                )
                print(
                    f"DIVERGENCE — run halted, report: {path}\n{exc}",
                    file=sys.stderr,
                )
                return 2

    summary = {
        "pass_id": pass_id,
        "mode": "apply" if apply else "dry-run",
        "applied": applied_records,
        "skipped": [
            {
                "image_id": e.get("image_id"),
                "receipt_id": e.get("receipt_id"),
                "skip_reason": e.get("skip_reason"),
            }
            for e in skipped
        ],
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Duplicate retirement (destructive; backup-first; T2 sign-off required)
# ---------------------------------------------------------------------------


def _serialize_entity(entity: Any) -> Any:
    to_item = getattr(entity, "to_item", None)
    if callable(to_item):
        try:
            return to_item()
        except Exception:  # pragma: no cover - fall through to repr
            pass
    return repr(entity)


def export_image_rows(client, image_id: str, out_path: Path) -> int:
    """Dump every DynamoDB row of the image to a local JSON backup."""
    details = client.get_image_details(image_id)
    export: dict[str, list] = {}
    total = 0
    for attr in (
        "images",
        "lines",
        "words",
        "letters",
        "receipts",
        "receipt_lines",
        "receipt_words",
        "receipt_letters",
        "receipt_word_labels",
        "receipt_places",
        "ocr_jobs",
        "ocr_routing_decisions",
    ):
        items = getattr(details, attr, None) or []
        if items:
            export[attr] = [_serialize_entity(item) for item in items]
            total += len(items)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {"image_id": image_id, "row_count": total, "rows": export},
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return total


def backup_raw_objects(
    s3_client, raw_bucket: str, image_id: str, backup_prefix: str
) -> list[str]:
    """Server-side copy every raw object of the image aside.

    Destination is the RAW bucket (rewarp recipe): the site bucket's
    non-build prefixes are wiped by the prod deploy's sync --delete.
    """
    copied = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(
        Bucket=raw_bucket, Prefix=f"assets/{image_id}"
    ):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            s3_client.copy_object(
                Bucket=raw_bucket,
                Key=f"{backup_prefix}{key}",
                CopySource={"Bucket": raw_bucket, "Key": key},
            )
            copied.append(key)
    return copied


def run_retire(
    group_file: Path,
    *,
    client,
    mcp_server,
    s3_client,
    table_name: str,
    raw_bucket: str,
    approvals_file: Path,
    apply: bool,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    lock_path: Path = DEFAULT_LOCK_PATH,
) -> int:
    guard_table(table_name)
    group = json.loads(Path(group_file).read_text(encoding="utf-8"))
    group_id = group.get("group_id")
    victims = group.get("retire") or []
    if not group_id or not victims:
        print(
            f"{group_file}: needs 'group_id' and a non-empty 'retire' list",
            file=sys.stderr,
        )
        return 1

    approvals = load_approvals(Path(approvals_file))
    signed_off = set(approvals.get("t2_retirements") or [])
    if group_id not in signed_off:
        print(
            f"refusing retirement: group {group_id!r} has no T2 sign-off "
            f"in {approvals_file} (t2_retirements)",
            file=sys.stderr,
        )
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_prefix = f"rewarp-backups/retired-{stamp}/"
    results = []
    with WriterLock(lock_path):
        for victim in victims:
            image_id = victim["image_id"]
            # Backup FIRST — both stores — before anything destructive.
            row_backup = Path(backup_dir) / f"retired-{stamp}-{image_id}.json"
            rows = export_image_rows(client, image_id, row_backup)
            copied = backup_raw_objects(
                s3_client, raw_bucket, image_id, backup_prefix
            )
            deletion = _run(
                mcp_server.delete_image_impl(
                    client, image_id, dry_run=not apply
                )
            )
            results.append(
                {
                    "image_id": image_id,
                    "rows_exported": rows,
                    "row_backup": str(row_backup),
                    "s3_objects_backed_up": copied,
                    "s3_backup_prefix": backup_prefix,
                    "deletion": deletion,
                }
            )

    print(
        json.dumps(
            {
                "group_id": group_id,
                "mode": "apply" if apply else "dry-run",
                "retired": results,
            },
            indent=2,
            default=str,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_clients(table_name: str):
    from receipt_dynamo import DynamoClient

    helpers = _load_helpers()
    return DynamoClient(table_name), helpers.load_mcp_server()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_apply = sub.add_parser("apply", help="apply T0 + approved T1 verdicts")
    p_apply.add_argument("--pass-id", required=True)
    p_apply.add_argument("--table", default=DEV_TABLE)
    p_apply.add_argument(
        "--apply",
        action="store_true",
        help="actually write (default is dry-run)",
    )
    p_apply.add_argument(
        "--verdicts-dir", type=Path, default=DEFAULT_VERDICTS_DIR
    )
    p_apply.add_argument(
        "--approvals-dir", type=Path, default=DEFAULT_APPROVALS_DIR
    )
    p_apply.add_argument(
        "--freeze-dir",
        type=Path,
        default=DEFAULT_FREEZE_DIR,
        help="freeze markers (tier names or A-J class letters); "
        "re-checked before every write",
    )
    p_apply.add_argument(
        "--poll-attempts", type=int, default=DEFAULT_POLL_ATTEMPTS
    )
    p_apply.add_argument(
        "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL
    )

    p_retire = sub.add_parser(
        "retire-duplicate",
        help="retire inferior duplicate scans (backup-first; needs "
        "T2 sign-off in the approvals file)",
    )
    p_retire.add_argument("--group-file", type=Path, required=True)
    p_retire.add_argument("--approvals-file", type=Path, required=True)
    p_retire.add_argument("--raw-bucket", required=True)
    p_retire.add_argument("--table", default=DEV_TABLE)
    p_retire.add_argument(
        "--apply",
        action="store_true",
        help="actually delete (default is dry-run; backups run either " "way)",
    )

    args = parser.parse_args(argv)

    try:
        guard_table(args.table)
    except ProdTableRefusedError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    client, mcp_server = _build_clients(args.table)

    try:
        if args.command == "apply":
            return run_apply(
                args.pass_id,
                client=client,
                mcp_server=mcp_server,
                table_name=args.table,
                apply=args.apply,
                verdicts_dir=args.verdicts_dir,
                approvals_dir=args.approvals_dir,
                freeze_dir=args.freeze_dir,
                poll_attempts=args.poll_attempts,
                poll_interval=args.poll_interval,
            )
        import boto3

        return run_retire(
            args.group_file,
            client=client,
            mcp_server=mcp_server,
            s3_client=boto3.client("s3"),
            table_name=args.table,
            raw_bucket=args.raw_bucket,
            approvals_file=args.approvals_file,
            apply=args.apply,
        )
    except WriterLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
