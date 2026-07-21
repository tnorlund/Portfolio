#!/usr/bin/env python3
"""Owner-facing G2 activation CLI for versioned merchant truth (#1188 P4).

The write-side companion to scripts/merchant_truth_diff.py (the read-only
review artifact). Where the diff tool renders the G2 decision document,
this tool performs the decision — and only when the owner explicitly says
so:

  DRY-RUN (default):
      activate_merchant_truth.py --slug costco_wholesale --version 1
      Loads the SEALED bundle fail-closed (same verification path as the
      diff tool), prints a condensed decision summary (gate, bundle_hash,
      component hashes) plus the exact mutation a flip WOULD perform,
      and stops. Zero writes.

  FLIP (explicit):
      activate_merchant_truth.py --slug costco_wholesale --version 1 --flip
      Performs the activation: ``DynamoClient.initial_activate`` when no
      ACTIVE pointer exists (conditional Put, converges idempotently), or
      ``DynamoClient.flip_active`` conditioned on the CURRENT active
      {version, bundle_hash} as expected-prev otherwise. Prints the
      resulting ACTIVE pointer and audit confirmation.

  FLEET:
      activate_merchant_truth.py --all-sealed [--flip]
      One activation per SEALED-pending version (the same pending set
      synthesis_loop/fleet_status.py reports), continuing past
      per-merchant failures with a summary table at the end. Still a
      dry-run without --flip.

Safety, matching the sibling scripts' conventions exactly: the dev table
(ReceiptsTable-dc5be22) is the default via DYNAMODB_TABLE_NAME; the prod
table (ReceiptsTable-d7ff76a, or any name carrying the d7ff76a marker) is
refused unconditionally BEFORE any client construction. ``--flip``
additionally prints a banner (table, slug(s), versions) before writing.

Exit codes: 0 success (including dry-run and idempotent convergence);
1 data/integrity error (or any per-merchant failure under --all-sealed);
2 refused configuration; 3 lost activation race
(MerchantTruthConflictError).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (REPO, os.path.join(REPO, "receipt_dynamo")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from receipt_dynamo.data.shared_exceptions import (  # noqa: E402
    MerchantTruthConflictError,
)
from receipt_dynamo.entities.merchant_truth import (  # noqa: E402
    MerchantTruthActive,
)

# The loading/verification helpers are the diff tool's: import, don't copy.
from scripts.merchant_truth_diff import (  # noqa: E402
    COMPONENT_ORDER,
    DEV_TABLE_NAME,
    PROD_TABLE_MARKER,
    PROD_TABLE_NAME,
    Bundle,
    DiffError,
    load_bundle,
    parse_version,
    short_hash,
)
from synthesis_loop.fleet_status import build_sealed_pending  # noqa: E402

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient

DEFAULT_ACTIVATED_BY = "owner-cli"


class ActivationError(Exception):
    """A data or integrity problem that must stop the activation."""


def resolve_table(cli_table: str | None) -> str:
    """Resolve the target table and refuse prod explicitly.

    Same stance as merchant_truth_diff.resolve_table / fleet_status:
    default dev via DYNAMODB_TABLE_NAME, and the prod table name or any
    name carrying the prod marker substring is refused unconditionally —
    before any DynamoDB client is constructed.
    """
    table = (
        cli_table or os.environ.get("DYNAMODB_TABLE_NAME") or DEV_TABLE_NAME
    )
    if table == PROD_TABLE_NAME or PROD_TABLE_MARKER in table:
        raise ValueError(
            f"activate_merchant_truth never touches prod; refusing table "
            f"{table!r} (prod = {PROD_TABLE_NAME!r})"
        )
    return table


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Plan: what one activation would do (shared by dry-run and --flip)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivationPlan:
    """A verified bundle plus the exact mutation a flip would perform."""

    slug: str
    version: int
    bundle: Bundle
    current: MerchantTruthActive | None
    action: str  # "initial" | "flip" | "noop"

    @property
    def bundle_hash(self) -> str:
        return self.bundle.manifest.bundle_hash

    @property
    def normalized_aliases(self) -> list[str]:
        payload = self.bundle.payload("identity")
        aliases = (payload or {}).get("normalized_aliases")
        if not isinstance(aliases, list) or not aliases:
            raise ActivationError(
                f"{self.slug} v{self.version} identity component carries no "
                "normalized_aliases; ACTIVE cannot be built"
            )
        return [str(alias) for alias in aliases]


def build_plan(
    client: "DynamoClient", slug: str, version: int
) -> ActivationPlan:
    """Load the bundle fail-closed and decide the mutation."""
    bundle = load_bundle(client, slug, version)
    manifest = bundle.manifest
    if manifest.gate_status != "PASS":
        raise ActivationError(
            f"{slug} v{version} gate_status is {manifest.gate_status!r}; "
            "only gate-PASS bundles are activatable"
        )
    # Consistent read: the accessor's expected-prev condition already
    # guarantees safety, but planning from a lagging replica could either
    # raise a spurious conflict or report a stale "already converged".
    current = client.get_active_merchant_truth(slug, consistent_read=True)
    if current is None:
        action = "initial"
    elif (
        current.version == version
        and current.bundle_hash == manifest.bundle_hash
    ):
        action = "noop"
    else:
        action = "flip"
    plan = ActivationPlan(
        slug=slug,
        version=version,
        bundle=bundle,
        current=current,
        action=action,
    )
    # Fail closed on a malformed identity component before any write plan
    # is shown, not at flip time.
    plan.normalized_aliases  # noqa: B018  (validation side effect)
    return plan


def _mutation_lines(plan: ActivationPlan) -> list[str]:
    if plan.action == "initial":
        return [
            "- INITIAL ACTIVATION: no ACTIVE pointer exists for "
            f"{plan.slug}.",
            f"- Mutation: initial_activate -> ACTIVE = v{plan.version} "
            f"(`{short_hash(plan.bundle_hash)}`) via conditional Put "
            "(ACTIVE absent; converges idempotently).",
        ]
    if plan.action == "noop":
        assert plan.current is not None
        return [
            f"- ALREADY ACTIVE: the pointer is v{plan.current.version} "
            f"(`{short_hash(plan.current.bundle_hash)}`), activated_at "
            f"{plan.current.activated_at} by {plan.current.activated_by}.",
            "- Mutation: none — activation has already converged.",
        ]
    assert plan.current is not None
    return [
        f"- FLIP: ACTIVE currently points at v{plan.current.version} "
        f"(`{short_hash(plan.current.bundle_hash)}`).",
        f"- Mutation: flip_active -> ACTIVE = v{plan.version} "
        f"(`{short_hash(plan.bundle_hash)}`), conditioned on exact "
        f"expected {{version: {plan.current.version}, bundle_hash: "
        f"`{short_hash(plan.current.bundle_hash)}`}}.",
    ]


def render_plan(plan: ActivationPlan, *, table: str) -> list[str]:
    """The condensed decision summary + the exact mutation."""
    manifest = plan.bundle.manifest
    lines = [
        f"# Activation: {plan.slug} v{plan.version}",
        "",
        f"Table: `{table}`",
        "",
        f"- gate_status: {manifest.gate_status}",
        f"- bundle_hash: `{manifest.bundle_hash}`",
        f"- manifest: {manifest.status}, sealed_at {manifest.sealed_at}",
        "- components: "
        + ", ".join(
            f"{name} `{short_hash(plan.bundle.components[name].content_hash)}`"
            for name in COMPONENT_ORDER
        ),
        "",
        "## Mutation",
        "",
        *_mutation_lines(plan),
    ]
    return lines


# --------------------------------------------------------------------------
# Execution (--flip only)
# --------------------------------------------------------------------------


def execute_plan(
    client: "DynamoClient",
    plan: ActivationPlan,
    *,
    table: str,
    activated_by: str,
) -> tuple[str, MerchantTruthActive]:
    """Perform the planned mutation and return (verb, resulting ACTIVE)."""
    if plan.action == "noop":
        assert plan.current is not None
        return "ALREADY-ACTIVE", plan.current
    target = MerchantTruthActive(
        slug=plan.slug,
        version=plan.version,
        bundle_hash=plan.bundle_hash,
        normalized_aliases=plan.normalized_aliases,
        activated_at=_utc_now(),
        activated_by=activated_by,
        gate_status="PASS",
        prev_version=(plan.current.version if plan.action == "flip" else None),
    )
    if plan.action == "initial":
        result = client.initial_activate(target, table)
        return "ACTIVATED", result
    assert plan.current is not None
    result = client.flip_active(
        target, plan.current.version, plan.current.bundle_hash, table
    )
    return "FLIPPED", result


def render_result(verb: str, active: MerchantTruthActive) -> list[str]:
    lines = [
        "",
        f"## Result: {verb}",
        "",
        f"- ACTIVE: {active.slug} -> v{active.version} "
        f"(`{active.bundle_hash}`)",
        f"- activated_at: {active.activated_at} by {active.activated_by}",
    ]
    if active.prev_version is not None:
        lines.append(f"- prev_version: {active.prev_version}")
    if verb == "ALREADY-ACTIVE":
        lines.append(
            "- No write performed: the pointer already matched this "
            "version and bundle_hash (idempotent convergence)."
        )
    else:
        action = "INITIAL_ACTIVATE" if verb == "ACTIVATED" else "FLIP_ACTIVE"
        lines.append(
            f"- Audit: {action} audit row written in the same "
            "transaction as the pointer."
        )
    return lines


def render_conflict(
    error: MerchantTruthConflictError,
    client: "DynamoClient",
    slug: str,
) -> list[str]:
    """Explain the lost race and show what ACTIVE points at now."""
    current = client.get_active_merchant_truth(slug, consistent_read=True)
    lines = [
        "",
        f"CONFLICT: {error}",
        "",
        "Another writer changed the ACTIVE pointer between this tool's "
        "read and its conditional write (the expected {version, "
        "bundle_hash} no longer matched). Nothing was written by this "
        "run.",
    ]
    if current is None:
        lines.append(f"Current ACTIVE for {slug}: (none)")
    else:
        lines.append(
            f"Current ACTIVE for {slug}: v{current.version} "
            f"(`{short_hash(current.bundle_hash)}`), activated_at "
            f"{current.activated_at} by {current.activated_by}"
        )
    lines.append("Re-run the dry-run to review the new state before retrying.")
    return lines


def print_flip_banner(table: str, targets: list[tuple[str, int]]) -> None:
    """The pre-write banner: table + every slug/version about to move."""
    print("=" * 62)
    print("FLIP: about to write ACTIVE pointer(s)")
    print(f"  table: {table}")
    for slug, version in targets:
        print(f"  {slug} -> v{version}")
    print("=" * 62)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _run_single(
    client: "DynamoClient",
    slug: str,
    version: int,
    *,
    table: str,
    flip: bool,
    activated_by: str,
) -> int:
    try:
        plan = build_plan(client, slug, version)
    except (DiffError, ActivationError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("\n".join(render_plan(plan, table=table)))
    if not flip:
        print(
            "\nDRY-RUN: no writes performed. Re-run with --flip to "
            "perform this activation (owner gate G2)."
        )
        return 0
    if plan.action != "noop":
        print_flip_banner(table, [(slug, version)])
    try:
        verb, active = execute_plan(
            client, plan, table=table, activated_by=activated_by
        )
    except MerchantTruthConflictError as error:
        print("\n".join(render_conflict(error, client, slug)))
        return 3
    print("\n".join(render_result(verb, active)))
    return 0


def _run_all_sealed(
    client: "DynamoClient",
    *,
    table: str,
    flip: bool,
    activated_by: str,
) -> int:
    manifests = client.list_merchant_truth_manifests()
    active_records = client.list_active_merchant_truth()
    pending = build_sealed_pending(manifests, active_records)
    print(
        f"# Merchant-truth activation sweep: {len(pending)} SEALED "
        f"pending version(s) on `{table}`"
    )
    if not pending:
        print("\nNothing to do: no sealed version is pending activation.")
        return 0
    targets = [(row["slug"], row["version"]) for row in pending]
    if flip:
        print_flip_banner(table, targets)
    results: list[tuple[str, int, str]] = []
    for slug, version in targets:
        print()
        try:
            plan = build_plan(client, slug, version)
        except (DiffError, ActivationError) as error:
            print(f"ERROR: {slug} v{version}: {error}", file=sys.stderr)
            results.append((slug, version, f"ERROR: {error}"))
            continue
        except Exception as error:  # noqa: BLE001 — sweep must not abort
            # Unexpected per-merchant faults (e.g. botocore ClientError
            # throttling) are recorded and the sweep continues, so one
            # transient failure cannot skip the remaining merchants or
            # the summary table.
            print(
                f"ERROR: {slug} v{version}: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )
            results.append(
                (slug, version, f"ERROR: {type(error).__name__}: {error}")
            )
            continue
        print("\n".join(render_plan(plan, table=table)))
        if not flip:
            results.append((slug, version, "DRY-RUN"))
            continue
        try:
            verb, active = execute_plan(
                client, plan, table=table, activated_by=activated_by
            )
        except MerchantTruthConflictError as error:
            print("\n".join(render_conflict(error, client, slug)))
            results.append((slug, version, f"CONFLICT: {error}"))
            continue
        except Exception as error:  # noqa: BLE001 — sweep must not abort
            print(
                f"ERROR: {slug} v{version}: "
                f"{type(error).__name__}: {error}",
                file=sys.stderr,
            )
            results.append(
                (slug, version, f"ERROR: {type(error).__name__}: {error}")
            )
            continue
        print("\n".join(render_result(verb, active)))
        results.append((slug, version, verb))
    print("\n## Sweep summary\n")
    print("| merchant | version | result |")
    print("|---|---|---|")
    for slug, version, outcome in results:
        print(f"| {slug} | {version} | {outcome} |")
    if not flip:
        print(
            "\nDRY-RUN: no writes performed. Re-run with --flip to "
            "perform these activations (owner gate G2)."
        )
        return 0
    failed = [
        outcome
        for _, _, outcome in results
        if outcome.startswith(("ERROR", "CONFLICT"))
    ]
    return 1 if failed else 0


def main(
    argv: list[str] | None = None,
    *,
    client: "DynamoClient | None" = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--slug", help="merchant slug, e.g. costco_wholesale")
    parser.add_argument(
        "--version",
        metavar="vN",
        help="SEALED version to activate (e.g. v1 or 1)",
    )
    parser.add_argument(
        "--flip",
        action="store_true",
        help="actually perform the activation (default is DRY-RUN)",
    )
    parser.add_argument(
        "--all-sealed",
        action="store_true",
        help="one activation per SEALED-pending version, fleet-wide",
    )
    parser.add_argument(
        "--activated-by",
        default=DEFAULT_ACTIVATED_BY,
        help=f"audit identity for the flip (default: {DEFAULT_ACTIVATED_BY})",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="DynamoDB table (default: DYNAMODB_TABLE_NAME env or "
        f"{DEV_TABLE_NAME}); prod is refused unconditionally",
    )
    args = parser.parse_args(argv)

    try:
        # Prod refusal happens here — before any client construction.
        table = resolve_table(args.table)
        if args.all_sealed:
            if args.slug or args.version:
                raise ValueError("--all-sealed takes no --slug/--version")
        else:
            if not args.slug or not args.version:
                raise ValueError(
                    "--slug and --version are required (or use --all-sealed)"
                )
    except ValueError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2

    if client is None:
        from receipt_dynamo.data.dynamo_client import (  # noqa: PLC0415
            DynamoClient,
        )

        region = os.environ.get("AWS_REGION", "us-east-1")
        client = DynamoClient(table_name=table, region=region)

    if args.all_sealed:
        return _run_all_sealed(
            client,
            table=table,
            flip=args.flip,
            activated_by=args.activated_by,
        )
    try:
        version = parse_version(args.version)
    except ValueError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    return _run_single(
        client,
        args.slug,
        version,
        table=table,
        flip=args.flip,
        activated_by=args.activated_by,
    )


if __name__ == "__main__":
    raise SystemExit(main())
