"""Governed DynamoDB access for immutable merchant-truth bundles."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import FlattenedStandardMixin
from receipt_dynamo.data.shared_exceptions import (
    MerchantTruthConflictError,
    MerchantTruthIntegrityError,
    MerchantTruthTableMismatchError,
)
from receipt_dynamo.entities.dynamodb_utils import to_dynamodb_value
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthAudit,
    MerchantTruthComponent,
    MerchantTruthManifest,
    MerchantTruthProposal,
    compute_bundle_hash,
    merchant_truth_pk,
    version_prefix,
)

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.type_defs import TransactWriteItemTypeDef


CREATE_ONLY = "attribute_not_exists(PK) AND attribute_not_exists(SK)"
MAX_TRANSACTION_ACTIONS = 100
MAX_TRANSACTION_BYTES = 4 * 1024 * 1024

FLAGS_COMPONENT = "flags"
# Measured components carry truth; only ``flags`` carries decided config.
MEASURED_COMPONENTS = COMPONENT_NAMES - {FLAGS_COMPONENT}
WRITTEN_BY_KINDS = frozenset(
    {"measurement_pipeline", "engine_config_sync", "migration"}
)
SOURCE_KINDS = frozenset({"measurement", "engine_config", "migration"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_token(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:36]


def _is_transaction_conflict(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in {
        "ConditionalCheckFailedException",
        "TransactionCanceledException",
    }


class _MerchantTruth(FlattenedStandardMixin):
    """Accessor implementing the frozen MerchantTruth write contract."""

    def _assert_expected_table(self, expected_table_name: str) -> None:
        if not expected_table_name or self.table_name != expected_table_name:
            raise MerchantTruthTableMismatchError(
                f"refusing merchant-truth write to {self.table_name!r}; "
                f"expected exact table {expected_table_name!r}"
            )

    @staticmethod
    def _put_action(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "Put": {
                "TableName": "",
                "Item": item,
                "ConditionExpression": CREATE_ONLY,
            }
        }

    def _bound_put_action(self, item: dict[str, Any]) -> dict[str, Any]:
        action = self._put_action(item)
        action["Put"]["TableName"] = self.table_name
        return action

    @staticmethod
    def _transaction_size(actions: Sequence[dict[str, Any]]) -> int:
        return len(
            json.dumps(actions, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        )

    def _fits_atomic_mint(self, actions: Sequence[dict[str, Any]]) -> bool:
        return (
            len(actions) <= MAX_TRANSACTION_ACTIONS
            and self._transaction_size(actions) <= MAX_TRANSACTION_BYTES
        )

    def _transact_write(
        self, actions: Sequence[dict[str, Any]], token_seed: str
    ) -> None:
        self._client.transact_write_items(
            TransactItems=actions,  # type: ignore[arg-type]
            ClientRequestToken=_request_token(token_seed),
        )

    @staticmethod
    def _derive_gate_status(gate_results: dict[str, Any]) -> str:
        """Derive PASS/FAIL from gate_results, failing closed on ambiguity.

        The contract requires that no fidelity fix ships without a metric:
        seal must carry the *derived* gate status, never a hardcoded PASS.
        A passing eval is signalled explicitly by ``status == "PASS"`` or
        ``passed is True``; an explicit fail is ``status in {FAIL, FAILED}``
        or ``passed is False``. Absent or contradictory signals are ambiguous
        and fail closed rather than sealing an unverified bundle.
        """
        if not isinstance(gate_results, dict) or not gate_results:
            raise MerchantTruthIntegrityError(
                "seal requires gate_results carrying an explicit pass signal"
            )
        status = gate_results.get("status")
        passed = gate_results.get("passed")
        status_norm = status.upper() if isinstance(status, str) else None
        pass_signals = {status_norm == "PASS", passed is True}
        fail_signals = {
            status_norm in {"FAIL", "FAILED"},
            passed is False,
        }
        if True in pass_signals and True in fail_signals:
            raise MerchantTruthIntegrityError(
                "gate_results carry contradictory pass/fail signals"
            )
        if True in pass_signals:
            return "PASS"
        if True in fail_signals:
            return "FAIL"
        raise MerchantTruthIntegrityError(
            "gate_results lack an explicit pass/fail signal"
        )

    @staticmethod
    def _audit(
        slug: str,
        version: int,
        bundle_hash: str,
        action: str,
        created_at: str,
        details: dict[str, Any],
    ) -> MerchantTruthAudit:
        audit_seed = json.dumps(
            {
                "slug": slug,
                "version": version,
                "bundle_hash": bundle_hash,
                "action": action,
                "created_at": created_at,
                "details": details,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return MerchantTruthAudit(
            slug=slug,
            created_at=created_at,
            audit_id=hashlib.sha256(audit_seed.encode("utf-8")).hexdigest()[
                :26
            ],
            action=action,
            version=version,
            bundle_hash=bundle_hash,
            details=details,
        )

    @staticmethod
    def _validate_component_governance(
        component: MerchantTruthComponent,
    ) -> None:
        """Enforce written_by.kind (finding: fix 3) and required provenance
        fields (finding 6, fix 4) for one component at mint time.

        ``written_by.kind`` must fit the component: ``flags`` carries decided
        config (``engine_config_sync``), measured components carry truth
        (``measurement_pipeline``). ``migration`` is the one-shot bootstrap
        writer and is accepted for every component. Provenance must carry the
        contract fields unless a migration writer sets the explicit
        ``provenance_completeness=legacy`` escape.
        """
        provenance = component.provenance
        written_by = provenance.get("written_by")
        if not isinstance(written_by, dict):
            raise MerchantTruthIntegrityError(
                f"component {component.name} provenance must carry a "
                "written_by identity"
            )
        kind = written_by.get("kind")
        if kind not in WRITTEN_BY_KINDS:
            raise MerchantTruthIntegrityError(
                f"component {component.name} has invalid written_by.kind "
                f"{kind!r}"
            )
        if kind != "migration":
            if component.name == FLAGS_COMPONENT and (
                kind != "engine_config_sync"
            ):
                raise MerchantTruthIntegrityError(
                    "flags component accepts only engine_config_sync or "
                    f"migration, not {kind!r}"
                )
            if component.name in MEASURED_COMPONENTS and (
                kind != "measurement_pipeline"
            ):
                raise MerchantTruthIntegrityError(
                    f"measured component {component.name} accepts only "
                    f"measurement_pipeline or migration, not {kind!r}"
                )
        source_kind = provenance.get("source_kind")
        if source_kind not in SOURCE_KINDS:
            raise MerchantTruthIntegrityError(
                f"component {component.name} has invalid source_kind "
                f"{source_kind!r}"
            )
        if (
            source_kind == "migration"
            and provenance.get("provenance_completeness") == "legacy"
        ):
            return
        missing: list[str] = []
        if not (
            provenance.get("source_path") or provenance.get("source_object")
        ):
            missing.append("source_path/source_object")
        if not (
            provenance.get("source_hash") or provenance.get("git_sha")
        ):
            missing.append("source_hash/git_sha")
        if not provenance.get("pipeline"):
            missing.append("pipeline")
        if not provenance.get("pipeline_version"):
            missing.append("pipeline_version")
        if not provenance.get("measured_at"):
            missing.append("measured_at")
        if source_kind == "measurement" and not provenance.get(
            "source_receipt_keys"
        ):
            missing.append("source_receipt_keys")
        if missing:
            raise MerchantTruthIntegrityError(
                f"component {component.name} provenance missing required "
                f"fields: {', '.join(missing)}"
            )

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
    ) -> MerchantTruthManifest:
        """Create an OPEN version atomically or via resumable overflow."""
        self._assert_expected_table(expected_table_name)
        by_name = {component.name: component for component in components}
        if len(by_name) != len(components) or set(by_name) != COMPONENT_NAMES:
            raise MerchantTruthIntegrityError(
                "mint requires exactly one of every declared component"
            )
        if any(
            component.slug != slug or component.version != version
            for component in components
        ):
            raise MerchantTruthIntegrityError(
                "all components must match the mint slug and version"
            )
        for component in components:
            self._validate_component_governance(component)
        component_hashes = {
            name: by_name[name].content_hash for name in sorted(by_name)
        }
        bundle_hash = compute_bundle_hash(component_hashes)
        manifest = MerchantTruthManifest(
            slug=slug,
            version=version,
            component_hashes=component_hashes,
            bundle_hash=bundle_hash,
            status="OPEN",
            provenance=provenance,
            mint_run_id=run_id,
        )
        timestamp = created_at or _utc_now()
        audit = self._audit(
            slug,
            version,
            bundle_hash,
            "MINT",
            timestamp,
            {"run_id": run_id, "component_count": len(components)},
        )
        actions = [self._bound_put_action(manifest.to_item())]
        actions.extend(
            self._bound_put_action(by_name[name].to_item())
            for name in sorted(by_name)
        )
        actions.append(self._bound_put_action(audit.to_item()))
        if self._fits_atomic_mint(actions):
            try:
                self._transact_write(
                    actions,
                    f"mint:{slug}:{version}:{run_id}:{timestamp}",
                )
            except ClientError as error:
                if not _is_transaction_conflict(error):
                    raise
                existing = self.get_merchant_truth_manifest(
                    slug, version, consistent_read=True
                )
                if (
                    existing is None
                    or existing.mint_run_id != run_id
                    or existing.bundle_hash != bundle_hash
                ):
                    raise MerchantTruthConflictError(
                        "version already belongs to a different mint"
                    ) from error
                return existing
            return manifest
        self._mint_overflow(
            manifest,
            list(by_name.values()),
            audit,
        )
        return manifest

    def _mint_overflow(
        self,
        manifest: MerchantTruthManifest,
        components: list[MerchantTruthComponent],
        audit: MerchantTruthAudit,
    ) -> None:
        """Resume an oversized OPEN mint without allocating a new version."""
        initial_actions = [
            self._bound_put_action(manifest.to_item()),
            self._bound_put_action(audit.to_item()),
        ]
        try:
            self._transact_write(
                initial_actions,
                f"overflow-open:{manifest.slug}:{manifest.version}:"
                f"{manifest.mint_run_id}",
            )
        except ClientError as error:
            if not _is_transaction_conflict(error):
                raise
            existing_manifest = self.get_merchant_truth_manifest(
                manifest.slug, manifest.version, consistent_read=True
            )
            if (
                existing_manifest is None
                or existing_manifest.mint_run_id != manifest.mint_run_id
                or existing_manifest.bundle_hash != manifest.bundle_hash
                or existing_manifest.status != "OPEN"
            ):
                raise MerchantTruthConflictError(
                    "overflow manifest conflicts with the requested mint"
                ) from error

        existing_components = {
            component.name: component
            for component in self.list_merchant_truth_components(
                manifest.slug, manifest.version, consistent_read=True
            )
        }
        for name, existing_component in existing_components.items():
            expected_hash = manifest.component_hashes.get(name)
            if expected_hash != existing_component.content_hash:
                raise MerchantTruthIntegrityError(
                    f"overflow component {name} has a conflicting hash"
                )
        remaining = [
            component
            for component in components
            if component.name not in existing_components
        ]
        for chunk_number, chunk in enumerate(
            self._overflow_chunks(remaining), start=1
        ):
            condition_check: dict[str, Any] = {
                "ConditionCheck": {
                    "TableName": self.table_name,
                    "Key": manifest.key,
                    "ConditionExpression": (
                        "#status = :open AND mint_run_id = :run_id AND "
                        "bundle_hash = :bundle_hash"
                    ),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":open": {"S": "OPEN"},
                        ":run_id": {"S": manifest.mint_run_id},
                        ":bundle_hash": {"S": manifest.bundle_hash},
                    },
                }
            }
            chunk_actions = [condition_check]
            chunk_actions.extend(
                self._bound_put_action(component.to_item())
                for component in chunk
            )
            self._transact_write(
                chunk_actions,
                f"overflow:{manifest.slug}:{manifest.version}:"
                f"{manifest.mint_run_id}:{chunk_number}",
            )

    def _overflow_chunks(
        self, components: Sequence[MerchantTruthComponent]
    ) -> Iterable[list[MerchantTruthComponent]]:
        chunk: list[MerchantTruthComponent] = []
        for component in components:
            candidate = [*chunk, component]
            actions = [
                {"ConditionCheck": {"Key": {}}},
                *[
                    self._bound_put_action(item.to_item())
                    for item in candidate
                ],
            ]
            if chunk and (
                len(actions) > MAX_TRANSACTION_ACTIONS
                or self._transaction_size(actions) > MAX_TRANSACTION_BYTES
            ):
                yield chunk
                chunk = [component]
            else:
                chunk = candidate
        if chunk:
            yield chunk

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
    ) -> MerchantTruthManifest:
        """Verify a complete OPEN bundle and conditionally seal it."""
        self._assert_expected_table(expected_table_name)
        manifest = self.get_merchant_truth_manifest(
            slug, version, consistent_read=True
        )
        if manifest is None:
            raise MerchantTruthIntegrityError("manifest does not exist")
        if manifest.status != "OPEN":
            raise MerchantTruthConflictError("seal requires an OPEN manifest")
        components = self.list_merchant_truth_components(
            slug, version, consistent_read=True
        )
        actual = {
            component.name: component.content_hash for component in components
        }
        if actual != manifest.component_hashes:
            raise MerchantTruthIntegrityError(
                "loaded components do not match the manifest declaration"
            )
        if compute_bundle_hash(actual) != manifest.bundle_hash:
            raise MerchantTruthIntegrityError("bundle hash mismatch")
        gate_status = self._derive_gate_status(gate_results)
        if gate_status != "PASS":
            raise MerchantTruthConflictError(
                f"cannot seal a bundle whose gate did not pass: {gate_status}"
            )
        timestamp = sealed_at or _utc_now()
        audit = self._audit(
            slug,
            version,
            manifest.bundle_hash,
            "SEAL",
            timestamp,
            {"actor": actor, "gate_status": gate_status},
        )
        update = {
            "Update": {
                "TableName": self.table_name,
                "Key": manifest.key,
                "UpdateExpression": (
                    "SET #status = :sealed, sealed_at = :sealed_at, "
                    "gate_status = :gate_status, gate_results = :gate_results, "
                    "confirmed_proposals = :proposals"
                ),
                "ConditionExpression": (
                    "#status = :open AND bundle_hash = :bundle_hash"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":open": {"S": "OPEN"},
                    ":sealed": {"S": "SEALED"},
                    ":sealed_at": {"S": timestamp},
                    ":gate_status": {"S": gate_status},
                    ":gate_results": {
                        "M": {
                            key: self._to_dynamo(value)
                            for key, value in gate_results.items()
                        }
                    },
                    ":proposals": {
                        "L": [{"S": item} for item in confirmed_proposals]
                    },
                    ":bundle_hash": {"S": manifest.bundle_hash},
                },
            }
        }
        self._transact_write(
            [update, self._bound_put_action(audit.to_item())],
            f"seal:{slug}:{version}:{manifest.bundle_hash}",
        )
        manifest.status = "SEALED"
        manifest.sealed_at = timestamp
        manifest.gate_status = gate_status
        manifest.gate_results = gate_results
        manifest.confirmed_proposals = confirmed_proposals
        return manifest

    @staticmethod
    def _to_dynamo(value: Any) -> dict[str, Any]:
        return to_dynamodb_value(value)

    def initial_activate(
        self,
        active: MerchantTruthActive,
        expected_table_name: str,
    ) -> MerchantTruthActive:
        """Conditionally create the first ACTIVE pointer."""
        self._assert_expected_table(expected_table_name)
        audit = self._audit(
            active.slug,
            active.version,
            active.bundle_hash,
            "INITIAL_ACTIVATE",
            active.activated_at,
            {"activated_by": active.activated_by},
        )
        actions = [
            self._sealed_manifest_check(active),
            self._bound_put_action(active.to_item()),
            self._bound_put_action(audit.to_item()),
        ]
        try:
            self._transact_write(
                actions,
                f"initial:{active.slug}:{active.version}:"
                f"{active.bundle_hash}:{active.activated_at}:"
                f"{active.activated_by}",
            )
            self._reconcile_proposal_effectivity(active.slug, active.version)
            return active
        except ClientError as error:
            if not _is_transaction_conflict(error):
                raise
            current = self.get_active_merchant_truth(
                active.slug, consistent_read=True
            )
            if current and (
                current.version == active.version
                and current.bundle_hash == active.bundle_hash
            ):
                self._reconcile_proposal_effectivity(
                    active.slug, active.version
                )
                return current
            raise MerchantTruthConflictError(
                "another initial activation won the ACTIVE pointer"
            ) from error

    def _sealed_manifest_check(
        self, active: MerchantTruthActive
    ) -> dict[str, Any]:
        return {
            "ConditionCheck": {
                "TableName": self.table_name,
                "Key": {
                    "PK": {"S": merchant_truth_pk(active.slug)},
                    "SK": {"S": f"{version_prefix(active.version)}#MANIFEST"},
                },
                "ConditionExpression": (
                    "#status = :sealed AND gate_status = :pass AND "
                    "bundle_hash = :target_hash"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":sealed": {"S": "SEALED"},
                    ":pass": {"S": "PASS"},
                    ":target_hash": {"S": active.bundle_hash},
                },
            }
        }

    def flip_active(
        self,
        active: MerchantTruthActive,
        expected_version: int,
        expected_bundle_hash: str,
        expected_table_name: str,
    ) -> MerchantTruthActive:
        """Move ACTIVE under an exact previous version/hash condition."""
        self._assert_expected_table(expected_table_name)
        current = self.get_active_merchant_truth(
            active.slug, consistent_read=True
        )
        if current and (
            current.version == active.version
            and current.bundle_hash == active.bundle_hash
        ):
            self._reconcile_proposal_effectivity(active.slug, active.version)
            return current
        audit = self._audit(
            active.slug,
            active.version,
            active.bundle_hash,
            "FLIP_ACTIVE",
            active.activated_at,
            {
                "activated_by": active.activated_by,
                "expected_version": expected_version,
                "expected_bundle_hash": expected_bundle_hash,
            },
        )
        update = {
            "Update": {
                "TableName": self.table_name,
                "Key": active.key,
                "UpdateExpression": (
                    "SET version = :version, bundle_hash = :bundle_hash, "
                    "activated_at = :activated_at, "
                    "activated_by = :activated_by, gate_status = :pass, "
                    "prev_version = :prev_version, "
                    "normalized_aliases = :aliases"
                ),
                "ConditionExpression": (
                    "version = :expected_version AND "
                    "bundle_hash = :expected_bundle_hash"
                ),
                "ExpressionAttributeValues": {
                    ":version": {"N": str(active.version)},
                    ":bundle_hash": {"S": active.bundle_hash},
                    ":activated_at": {"S": active.activated_at},
                    ":activated_by": {"S": active.activated_by},
                    ":pass": {"S": "PASS"},
                    ":prev_version": {"N": str(expected_version)},
                    ":aliases": {
                        "L": [
                            {"S": alias} for alias in active.normalized_aliases
                        ]
                    },
                    ":expected_version": {"N": str(expected_version)},
                    ":expected_bundle_hash": {"S": expected_bundle_hash},
                },
            }
        }
        try:
            self._transact_write(
                [
                    self._sealed_manifest_check(active),
                    update,
                    self._bound_put_action(audit.to_item()),
                ],
                f"flip:{active.slug}:{active.version}:"
                f"{active.bundle_hash}:{expected_version}:"
                f"{expected_bundle_hash}:{active.activated_at}:"
                f"{active.activated_by}",
            )
            self._reconcile_proposal_effectivity(active.slug, active.version)
            return active
        except ClientError as error:
            if not _is_transaction_conflict(error):
                raise
            converged = self.get_active_merchant_truth(
                active.slug, consistent_read=True
            )
            if converged and (
                converged.version == active.version
                and converged.bundle_hash == active.bundle_hash
            ):
                self._reconcile_proposal_effectivity(
                    active.slug, active.version
                )
                return converged
            raise MerchantTruthConflictError(
                "ACTIVE no longer matches the expected prior state"
            ) from error

    def _reconcile_proposal_effectivity(
        self, slug: str, active_version: int
    ) -> None:
        """Derive proposal EFFECTIVE state from the now-ACTIVE version.

        A proposal is EFFECTIVE exactly when its ``resolved_by_version`` is
        the currently ACTIVE version (finding 7). After the pointer moves,
        candidates measured by the active version become EFFECTIVE and any
        proposal still EFFECTIVE under a no-longer-active version reverts to
        MEASURED_IN_CANDIDATE. The same rule covers rollback: flipping back to
        a prior version reverts proposals tied to the version left behind. All
        updates are idempotent conditional writes, so repeated or converged
        flips reconcile to the same state without spurious failures.
        """
        for proposal in self.list_merchant_truth_proposals(slug):
            if (
                proposal.status == "MEASURED_IN_CANDIDATE"
                and proposal.resolved_by_version == active_version
            ):
                self._transition_proposal_status(
                    proposal,
                    from_status="MEASURED_IN_CANDIDATE",
                    to_status="EFFECTIVE",
                    expected_version=active_version,
                )
            elif (
                proposal.status == "EFFECTIVE"
                and proposal.resolved_by_version != active_version
            ):
                self._transition_proposal_status(
                    proposal,
                    from_status="EFFECTIVE",
                    to_status="MEASURED_IN_CANDIDATE",
                    expected_version=None,
                )

    def _transition_proposal_status(
        self,
        proposal: MerchantTruthProposal,
        *,
        from_status: str,
        to_status: str,
        expected_version: int | None,
    ) -> None:
        names = {"#status": "status"}
        values: dict[str, Any] = {
            ":from": {"S": from_status},
            ":to": {"S": to_status},
        }
        condition = "#status = :from"
        if expected_version is not None:
            condition += " AND resolved_by_version = :version"
            values[":version"] = {"N": str(expected_version)}
        try:
            self._client.update_item(
                TableName=self.table_name,
                Key=proposal.key,
                UpdateExpression="SET #status = :to",
                ConditionExpression=condition,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as error:
            # Idempotent: a concurrent/duplicate reconcile already moved it.
            if not _is_transaction_conflict(error):
                raise

    def add_proposal(
        self,
        proposal: MerchantTruthProposal,
        expected_table_name: str,
    ) -> None:
        """Create an append-only proposal body."""
        self._assert_expected_table(expected_table_name)
        self._client.put_item(
            TableName=self.table_name,
            Item=proposal.to_item(),
            ConditionExpression=CREATE_ONLY,
        )

    def resolve_proposal(
        self,
        proposal: MerchantTruthProposal,
        version: int,
        resolution: str,
        expected_table_name: str,
    ) -> None:
        """Mark an OPEN proposal measured by a sealed candidate version."""
        self._assert_expected_table(expected_table_name)
        if proposal.status != "OPEN":
            raise MerchantTruthConflictError(
                "only an OPEN proposal can enter candidate measurement"
            )
        self._client.update_item(
            TableName=self.table_name,
            Key=proposal.key,
            UpdateExpression=(
                "SET #status = :candidate, resolved_by_version = :version, "
                "resolution = :resolution"
            ),
            ConditionExpression="#status = :open",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":open": {"S": "OPEN"},
                ":candidate": {"S": "MEASURED_IN_CANDIDATE"},
                ":version": {"N": str(version)},
                ":resolution": {"S": resolution},
            },
        )

    def get_active_merchant_truth(
        self, slug: str, *, consistent_read: bool = False
    ) -> MerchantTruthActive | None:
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": merchant_truth_pk(slug)},
                "SK": {"S": "TRUTH#ACTIVE"},
            },
            ConsistentRead=consistent_read,
        )
        item = response.get("Item")
        return MerchantTruthActive.from_item(item) if item else None

    def get_merchant_truth_manifest(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> MerchantTruthManifest | None:
        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": merchant_truth_pk(slug)},
                "SK": {"S": f"{version_prefix(version)}#MANIFEST"},
            },
            ConsistentRead=consistent_read,
        )
        item = response.get("Item")
        return MerchantTruthManifest.from_item(item) if item else None

    def list_merchant_truth_components(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> list[MerchantTruthComponent]:
        prefix = f"{version_prefix(version)}#C#"
        items = self._query_all(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": {"S": merchant_truth_pk(slug)},
                ":sk": {"S": prefix},
            },
            ConsistentRead=consistent_read,
        )
        return [MerchantTruthComponent.from_item(item) for item in items]

    def read_merchant_truth_bundle_items(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> list[dict[str, Any]]:
        """Return every paginated manifest/component item for a version."""
        return self._query_all(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": {"S": merchant_truth_pk(slug)},
                ":sk": {"S": f"{version_prefix(version)}#"},
            },
            ConsistentRead=consistent_read,
        )

    def list_active_merchant_truth(self) -> list[MerchantTruthActive]:
        items = self._query_all(
            TableName=self.table_name,
            IndexName="GSITYPE",
            KeyConditionExpression="#type = :type",
            ExpressionAttributeNames={"#type": "TYPE"},
            ExpressionAttributeValues={
                ":type": {"S": "MERCHANT_TRUTH_ACTIVE"}
            },
        )
        return [MerchantTruthActive.from_item(item) for item in items]

    def list_merchant_truth_proposals(
        self, slug: str
    ) -> list[MerchantTruthProposal]:
        items = self._query_all(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": {"S": merchant_truth_pk(slug)},
                ":sk": {"S": "PROPOSED#"},
            },
        )
        return [MerchantTruthProposal.from_item(item) for item in items]

    def _query_all(self, **params: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        exclusive_start_key = None
        while True:
            request = dict(params)
            if exclusive_start_key is not None:
                request["ExclusiveStartKey"] = exclusive_start_key
            response = self._client.query(**request)
            items.extend(response.get("Items", []))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if not exclusive_start_key:
                return items
