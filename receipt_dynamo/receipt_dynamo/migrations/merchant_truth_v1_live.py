"""Live mint/seal driver and dry-run parity verifier for MerchantTruth v1.

The migration's read side stays in ``merchant_truth_v1`` and remains
structurally read-only. This module owns the *write* side of the one-shot
v1 bootstrap and never talks to DynamoDB directly: every write goes
exclusively through the mutation-tested ``_MerchantTruth`` accessor surface
on ``DynamoClient`` (``mint_version`` / ``seal_version`` — conditional
creates, atomic 9-action mint). It adds three safety layers on top:

1. Table pinning: the dev table ``ReceiptsTable-dc5be22`` is the only
   implicit target; any other table must be passed explicitly, and the prod
   table ``ReceiptsTable-d7ff76a`` is refused unconditionally.
2. Blocked-merchant exclusion: any payload carrying migration blockers
   (the asset-blocked merchants per ``_summary.json``) is excluded from
   minting with an owner-visible report line, so v1 never seals an asset
   closure it cannot satisfy (#1194 review trail).
3. Dry-run <-> live parity: every minted bundle is read back and pushed
   through ``MerchantTruthLoader``'s fail-closed fixture verification, then
   byte-compared component-by-component against the dry-run payload files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthIntegrityError,
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthComponent,
    MerchantTruthManifest,
    canonical_json_bytes,
    merchant_truth_pk,
    version_prefix,
)
from receipt_dynamo.merchant_truth_loader import (
    MerchantTruthLoader,
    TruthResolutionMode,
)
from receipt_dynamo.migrations.merchant_truth_v1 import (
    DEV_TABLE_NAME,
    MerchantV1Payload,
)

PROD_TABLE_NAME = "ReceiptsTable-d7ff76a"


class MerchantTruthWriter(Protocol):
    """The exact accessor surface the live migration is allowed to use."""

    def mint_version(
        self,
        slug: str,
        version: int,
        components: Sequence[MerchantTruthComponent],
        provenance: dict[str, Any],
        run_id: str,
        expected_table_name: str,
        *,
        created_at: str | None = None,
    ) -> MerchantTruthManifest: ...

    def seal_version(
        self,
        slug: str,
        version: int,
        gate_results: dict[str, Any],
        confirmed_proposals: list[str],
        expected_table_name: str,
        *,
        sealed_at: str | None = None,
        actor: str = "merchant-truth-writer",
    ) -> MerchantTruthManifest: ...

    def read_merchant_truth_bundle_items(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> list[dict[str, Any]]: ...


def validate_live_table(table_name: str, *, explicit: bool) -> None:
    """Refuse every live target except the pinned dev table.

    The prod table is refused unconditionally. Any other non-dev table is
    allowed only when the operator passed it explicitly on the command line
    (``explicit=True``), which supports test tables without ever making a
    non-dev write reachable by default.
    """
    if table_name == PROD_TABLE_NAME:
        raise MerchantTruthTableMismatchError(
            "refusing live merchant-truth migration against prod table "
            f"{PROD_TABLE_NAME!r}; prod promotion is a separate owner-gated "
            "manual step and this refusal is unconditional"
        )
    if table_name == DEV_TABLE_NAME:
        return
    if not table_name or not explicit:
        raise MerchantTruthTableMismatchError(
            f"live migration writes only to exact dev table "
            f"{DEV_TABLE_NAME!r} by default; pass --table explicitly to "
            f"target {table_name!r}"
        )


def format_live_banner(
    table_name: str, merchant_count: int, git_sha: str
) -> str:
    """The loud pre-write banner: exact table, mint count, and code SHA."""
    bar = "=" * 72
    return "\n".join(
        [
            bar,
            "LIVE MERCHANT-TRUTH V1 MINT — DynamoDB WRITES WILL OCCUR",
            f"  table:     {table_name}",
            f"  merchants: {merchant_count} (mint + seal v1 each)",
            f"  git SHA:   {git_sha}",
            bar,
        ]
    )


def bootstrap_gate_results(
    *, git_sha: str, generated_at: str, payload_dir: Path
) -> dict[str, Any]:
    """Explicit gate_results for the one-shot bootstrap seal.

    ``seal_version`` derives gate status from an explicit signal and fails
    closed on ambiguity; this block passes via ``status``/``passed`` while
    its provenance records that no fidelity eval has run against
    truth-loaded renders yet — the passing signal is source-fidelity parity
    (dry-run payloads vs live read-back), covered by the accessor's
    ``migration`` writer-kind allowance.
    """
    return {
        "status": "PASS",
        "passed": True,
        "gate": "merchant_truth_v1_bootstrap",
        "kind": "migration-bootstrap-seal",
        "note": (
            "bootstrap seal: gate passes on dry-run<->live source parity "
            "only; no fidelity eval has run against truth-loaded renders "
            "yet"
        ),
        "evidence": {
            "dry_run_payload_dir": str(payload_dir),
            "git_sha": git_sha,
            "generated_at": generated_at,
        },
        "written_by": {
            "kind": "migration",
            "name": "merchant_truth_v1",
            "version": "1",
        },
    }


@dataclass(frozen=True)
class LiveMintResult:
    """Owner-visible outcome for one merchant in a live run."""

    merchant_name: str
    slug: str
    action: str  # "MINTED_SEALED" | "EXCLUDED"
    version: int
    bundle_hash: str | None = None
    blockers: tuple[str, ...] = ()

    @property
    def report_line(self) -> str:
        if self.action == "EXCLUDED":
            return (
                f"EXCLUDED (asset-blocked) {self.slug}: "
                f"{'; '.join(self.blockers)}"
            )
        digest = (self.bundle_hash or "")[:12]
        return f"MINTED+SEALED v{self.version} {self.slug} bundle={digest}"


def _payload_entities(
    payload: MerchantV1Payload,
) -> tuple[MerchantTruthManifest, list[MerchantTruthComponent]]:
    """Rebuild the exact dry-run entities from a payload's low-level items."""
    manifests: list[MerchantTruthManifest] = []
    components: list[MerchantTruthComponent] = []
    for item in payload.items:
        item_type = item.get("TYPE", {}).get("S")
        if item_type == "MERCHANT_TRUTH_MANIFEST":
            manifests.append(MerchantTruthManifest.from_item(item))
        elif item_type == "MERCHANT_TRUTH_COMPONENT":
            components.append(MerchantTruthComponent.from_item(item))
    if len(manifests) != 1:
        raise MerchantTruthIntegrityError(
            f"payload for {payload.slug} must carry exactly one manifest"
        )
    if {component.name for component in components} != COMPONENT_NAMES:
        raise MerchantTruthIntegrityError(
            f"payload for {payload.slug} does not carry the exact "
            "component set"
        )
    return manifests[0], components


def run_live_mint(
    client: MerchantTruthWriter,
    payloads: Sequence[MerchantV1Payload],
    *,
    table_name: str,
    gate_results: dict[str, Any],
    generated_at: str,
    explicit_table: bool = False,
) -> list[LiveMintResult]:
    """Mint + seal v1 for every unblocked merchant through the accessor.

    Blocked merchants (any non-empty ``blockers``) are excluded, never
    minted, and reported. The dry-run payload items are replayed verbatim:
    the accessor re-derives hashes, re-validates governance, and writes its
    own MINT/SEAL audit rows.
    """
    validate_live_table(table_name, explicit=explicit_table)
    results: list[LiveMintResult] = []
    for payload in sorted(payloads, key=lambda entry: entry.slug):
        if payload.blockers:
            results.append(
                LiveMintResult(
                    merchant_name=payload.merchant_name,
                    slug=payload.slug,
                    action="EXCLUDED",
                    version=1,
                    blockers=tuple(payload.blockers),
                )
            )
            continue
        manifest, components = _payload_entities(payload)
        client.mint_version(
            payload.slug,
            manifest.version,
            components,
            manifest.provenance,
            manifest.mint_run_id,
            table_name,
            created_at=generated_at,
        )
        sealed = client.seal_version(
            payload.slug,
            manifest.version,
            gate_results,
            [],
            table_name,
            sealed_at=generated_at,
            actor="merchant_truth_v1_migration",
        )
        results.append(
            LiveMintResult(
                merchant_name=payload.merchant_name,
                slug=payload.slug,
                action="MINTED_SEALED",
                version=sealed.version,
                bundle_hash=sealed.bundle_hash,
            )
        )
    return results


@dataclass(frozen=True)
class LiveVerifyResult:
    """Per-merchant dry-run <-> live parity outcome."""

    slug: str
    ok: bool
    bundle_hash: str | None = None
    mismatches: tuple[str, ...] = field(default_factory=tuple)

    @property
    def report_line(self) -> str:
        if self.ok:
            digest = (self.bundle_hash or "")[:12]
            return f"VERIFY OK {self.slug} bundle={digest}"
        return f"VERIFY MISMATCH {self.slug}: {'; '.join(self.mismatches)}"


def verify_live_against_dry_run(
    client: MerchantTruthWriter,
    payload_dir: Path,
    slugs: Sequence[str],
    *,
    work_dir: Path,
) -> list[LiveVerifyResult]:
    """Read every minted bundle back and byte-compare it to the dry run.

    Each bundle is read with strong consistency, verified through
    ``MerchantTruthLoader``'s fail-closed fixture path (SEALED/PASS status,
    component set, content hashes, bundle hash), and then every canonical
    component payload is byte-compared against the dry-run payload file.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    loader = MerchantTruthLoader(None, work_dir / "loader-cache")
    results: list[LiveVerifyResult] = []
    for slug in sorted(slugs):
        results.append(
            _verify_one_bundle(client, loader, payload_dir, slug, work_dir)
        )
    return results


def _verify_one_bundle(
    client: MerchantTruthWriter,
    loader: MerchantTruthLoader,
    payload_dir: Path,
    slug: str,
    work_dir: Path,
) -> LiveVerifyResult:
    mismatches: list[str] = []
    payload_path = payload_dir / f"{slug}.json"
    document = json.loads(payload_path.read_text(encoding="utf-8"))
    expected_manifest, expected_components = _payload_entities(
        MerchantV1Payload(
            merchant_name=document["merchant_name"],
            slug=slug,
            items=document["items"],
            blockers=document.get("migration_blockers", []),
        )
    )
    expected_bytes = {
        component.name: canonical_json_bytes(component.payload)
        for component in expected_components
    }

    items = client.read_merchant_truth_bundle_items(
        slug, expected_manifest.version, consistent_read=True
    )
    fixture_path = work_dir / f"{slug}.bundle.json"
    fixture_path.write_text(
        json.dumps({"items": items}, sort_keys=True), encoding="utf-8"
    )
    try:
        artifact = loader.load(
            slug,
            TruthResolutionMode.FIXTURE,
            fixture_path=fixture_path,
        )
    except MerchantTruthIntegrityError as error:
        return LiveVerifyResult(
            slug=slug,
            ok=False,
            mismatches=(f"loader rejected live bundle: {error}",),
        )
    if artifact.slug != slug:
        mismatches.append(
            f"loader resolved slug {artifact.slug!r}, expected {slug!r}"
        )
    if artifact.bundle_hash != expected_manifest.bundle_hash:
        mismatches.append(
            f"bundle hash {artifact.bundle_hash} != dry-run "
            f"{expected_manifest.bundle_hash}"
        )
    live_names = set(artifact.components)
    expected_names = set(expected_bytes)
    for name in sorted(expected_names - live_names):
        mismatches.append(f"component {name} missing from live bundle")
    for name in sorted(live_names - expected_names):
        mismatches.append(f"unexpected live component {name}")
    for name in sorted(expected_names & live_names):
        if (
            canonical_json_bytes(artifact.components[name])
            != expected_bytes[name]
        ):
            mismatches.append(
                f"component {name} canonical payload bytes differ"
            )
    return LiveVerifyResult(
        slug=slug,
        ok=not mismatches,
        bundle_hash=artifact.bundle_hash,
        mismatches=tuple(mismatches),
    )


@dataclass(frozen=True)
class OpenVersionCleanupResult:
    """Outcome of an owner-gated unsealed-OPEN-version cleanup."""

    slug: str
    version: int
    found_keys: tuple[str, ...]
    deleted: bool

    @property
    def report_lines(self) -> list[str]:
        verb = "DELETED" if self.deleted else "WOULD DELETE (dry run)"
        lines = [
            f"cleanup {self.slug} v{self.version}: {verb} "
            f"{len(self.found_keys)} rows (AUDIT rows are preserved)"
        ]
        lines.extend(f"  {key}" for key in self.found_keys)
        return lines


def cleanup_unsealed_open_version(
    dynamodb_client: Any,
    *,
    table_name: str,
    slug: str,
    version: int,
    explicit_table: bool = False,
    delete: bool = False,
) -> OpenVersionCleanupResult:
    """Owner-gated recovery: remove the rows of a FAILED, never-sealed mint.

    This exists for exactly one situation: a migration mint wrote its OPEN
    manifest + components but the seal failed (e.g. the G1 costco mint,
    whose legacy AttributeValue-encoded payloads lost number-form fidelity
    and can never re-verify). ``mint_version`` is create-only, so a re-mint
    cannot proceed while those rows exist; the resume protocol cannot help
    because the stored representation itself is unverifiable.

    Guardrails: table pinning identical to the live mint (prod refused
    unconditionally); refuses to touch anything unless the manifest exists
    and is ``OPEN`` (a SEALED version is immutable truth and is never
    deleted); deletion is a single conditional transaction; append-only
    ``AUDIT#`` rows are outside the version key range and are always
    preserved as the historical record of the failed attempt. Default is a
    dry run that only lists the rows.
    """
    validate_live_table(table_name, explicit=explicit_table)
    partition_key = merchant_truth_pk(slug)
    sort_prefix = f"{version_prefix(version)}#"
    items: list[dict[str, Any]] = []
    exclusive_start_key = None
    while True:
        request: dict[str, Any] = {
            "TableName": table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
            "ExpressionAttributeValues": {
                ":pk": {"S": partition_key},
                ":sk": {"S": sort_prefix},
            },
            "ConsistentRead": True,
        }
        if exclusive_start_key is not None:
            request["ExclusiveStartKey"] = exclusive_start_key
        response = dynamodb_client.query(**request)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")
        if not exclusive_start_key:
            break
    found_keys = tuple(item["SK"]["S"] for item in items)
    if not items:
        return OpenVersionCleanupResult(slug, version, (), deleted=False)
    manifest_items = [
        item
        for item in items
        if item["SK"]["S"] == f"{version_prefix(version)}#MANIFEST"
    ]
    if len(manifest_items) != 1:
        raise MerchantTruthIntegrityError(
            f"{slug} v{version} has {len(manifest_items)} manifest rows; "
            "refusing cleanup of an unrecognized state"
        )
    status = manifest_items[0].get("status", {}).get("S")
    if status != "OPEN":
        raise MerchantTruthConflictError(
            f"refusing cleanup: {slug} v{version} manifest status is "
            f"{status!r}; only a never-sealed OPEN version may be removed"
        )
    if not delete:
        return OpenVersionCleanupResult(slug, version, found_keys, False)
    actions: list[dict[str, Any]] = []
    for item in items:
        key = {"PK": item["PK"], "SK": item["SK"]}
        if item["SK"]["S"].endswith("#MANIFEST"):
            actions.append(
                {
                    "Delete": {
                        "TableName": table_name,
                        "Key": key,
                        "ConditionExpression": "#status = :open",
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {":open": {"S": "OPEN"}},
                    }
                }
            )
        else:
            actions.append(
                {
                    "Delete": {
                        "TableName": table_name,
                        "Key": key,
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                }
            )
    dynamodb_client.transact_write_items(TransactItems=actions)
    return OpenVersionCleanupResult(slug, version, found_keys, True)
