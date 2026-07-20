"""Versioned merchant-truth entities and canonical hashing helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.dynamodb_utils import (
    parse_dynamodb_map,
    to_dynamodb_value,
)

COMPONENT_NAMES = frozenset(
    {
        "identity",
        "typography",
        "stylemap",
        "layout",
        "assets",
        "flags",
        "catalog_snapshot",
    }
)
MANIFEST_STATUSES = frozenset({"OPEN", "SEALED"})
PROPOSAL_STATUSES = frozenset({"OPEN", "MEASURED_IN_CANDIDATE", "EFFECTIVE"})
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON-compatible value for stable content hashing."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def hash_payload(value: Any) -> str:
    """Return the SHA-256 digest of a canonical JSON payload."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def compute_bundle_hash(component_hashes: Mapping[str, str]) -> str:
    """Hash the sorted ``component:hash`` manifest lines."""
    lines = "".join(
        f"{name}:{component_hashes[name]}\n" for name in sorted(component_hashes)
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def merchant_truth_pk(slug: str) -> str:
    """Return the partition key for a validated merchant slug."""
    _validate_slug(slug)
    return f"MERCHANT_TRUTH#{slug}"


def version_prefix(version: int) -> str:
    """Return the fixed-width sort-key prefix for a version."""
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("version must be a positive integer")
    if version > 9_999_999_999:
        raise ValueError("version exceeds the 10-digit key grammar")
    return f"TRUTH#v{version:010d}"


def _validate_slug(slug: str) -> None:
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError(f"invalid merchant slug: {slug!r}")


def _validate_hash(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")


def _item_to_python(item: dict[str, Any]) -> dict[str, Any]:
    return parse_dynamodb_map(item)


@dataclass(eq=True)
class MerchantTruthComponent(DynamoDBEntity):
    """One immutable component in a merchant-truth version."""

    slug: str
    version: int
    name: str
    payload: Any
    provenance: dict[str, Any]
    content_hash: str | None = None
    payload_s3_key: str | None = None
    payload_size: int | None = None

    def __post_init__(self) -> None:
        _validate_slug(self.slug)
        version_prefix(self.version)
        if self.name not in COMPONENT_NAMES:
            raise ValueError(f"unknown merchant-truth component: {self.name}")
        if not isinstance(self.provenance, dict) or not self.provenance:
            raise ValueError("component provenance must be a non-empty map")
        calculated_hash = hash_payload(self.payload)
        if self.content_hash is None:
            self.content_hash = calculated_hash
        _validate_hash(self.content_hash, "content_hash")
        if self.content_hash != calculated_hash:
            raise ValueError("content_hash does not match canonical payload")
        if self.payload_s3_key is not None and not self.payload_s3_key:
            raise ValueError("payload_s3_key cannot be empty")
        if self.payload_size is not None and self.payload_size < 0:
            raise ValueError("payload_size cannot be negative")

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": merchant_truth_pk(self.slug)},
            "SK": {"S": f"{version_prefix(self.version)}#C#{self.name}"},
        }

    def to_item(self) -> dict[str, Any]:
        item = {
            **self.key,
            "TYPE": {"S": "MERCHANT_TRUTH_COMPONENT"},
            "slug": {"S": self.slug},
            "version": {"N": str(self.version)},
            "component": {"S": self.name},
            "content_hash": {"S": self.content_hash},
            "provenance": to_dynamodb_value(self.provenance),
            "payload": to_dynamodb_value(self.payload),
        }
        if self.payload_s3_key is not None:
            item["payload_s3_key"] = {"S": self.payload_s3_key}
        if self.payload_size is not None:
            item["payload_size"] = {"N": str(self.payload_size)}
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "MerchantTruthComponent":
        data = _item_to_python(item)
        return cls(
            slug=data["slug"],
            version=int(data["version"]),
            name=data["component"],
            payload=data["payload"],
            provenance=data["provenance"],
            content_hash=data["content_hash"],
            payload_s3_key=data.get("payload_s3_key"),
            payload_size=data.get("payload_size"),
        )


@dataclass(eq=True)
class MerchantTruthManifest(DynamoDBEntity):
    """Manifest for an OPEN or SEALED merchant-truth version."""

    slug: str
    version: int
    component_hashes: dict[str, str]
    bundle_hash: str
    status: str
    provenance: dict[str, Any]
    mint_run_id: str
    gate_status: str = "PENDING"
    gate_results: dict[str, Any] = field(default_factory=dict)
    confirmed_proposals: list[str] = field(default_factory=list)
    sealed_at: str | None = None

    def __post_init__(self) -> None:
        _validate_slug(self.slug)
        version_prefix(self.version)
        if self.status not in MANIFEST_STATUSES:
            raise ValueError(f"invalid manifest status: {self.status}")
        if not self.mint_run_id:
            raise ValueError("mint_run_id cannot be empty")
        if set(self.component_hashes) != COMPONENT_NAMES:
            raise ValueError("manifest must declare the exact component set")
        for name, digest in self.component_hashes.items():
            _validate_hash(digest, f"component_hashes[{name}]")
        _validate_hash(self.bundle_hash, "bundle_hash")
        if self.bundle_hash != compute_bundle_hash(self.component_hashes):
            raise ValueError("bundle_hash does not match component hashes")

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": merchant_truth_pk(self.slug)},
            "SK": {"S": f"{version_prefix(self.version)}#MANIFEST"},
        }

    def to_item(self) -> dict[str, Any]:
        item = {
            **self.key,
            "TYPE": {"S": "MERCHANT_TRUTH_MANIFEST"},
            "slug": {"S": self.slug},
            "version": {"N": str(self.version)},
            "component_hashes": to_dynamodb_value(self.component_hashes),
            "component_count": {"N": str(len(self.component_hashes))},
            "bundle_hash": {"S": self.bundle_hash},
            "status": {"S": self.status},
            "gate_status": {"S": self.gate_status},
            "gate_results": to_dynamodb_value(self.gate_results),
            "provenance": to_dynamodb_value(self.provenance),
            "mint_run_id": {"S": self.mint_run_id},
            "confirmed_proposals": to_dynamodb_value(self.confirmed_proposals),
        }
        if self.sealed_at is not None:
            item["sealed_at"] = {"S": self.sealed_at}
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "MerchantTruthManifest":
        data = _item_to_python(item)
        return cls(
            slug=data["slug"],
            version=int(data["version"]),
            component_hashes=data["component_hashes"],
            bundle_hash=data["bundle_hash"],
            status=data["status"],
            provenance=data["provenance"],
            mint_run_id=data["mint_run_id"],
            gate_status=data.get("gate_status", "PENDING"),
            gate_results=data.get("gate_results", {}),
            confirmed_proposals=data.get("confirmed_proposals", []),
            sealed_at=data.get("sealed_at"),
        )


@dataclass(eq=True)
class MerchantTruthActive(DynamoDBEntity):
    """The single optimistically mutable pointer for a merchant."""

    slug: str
    version: int
    bundle_hash: str
    normalized_aliases: list[str]
    activated_at: str
    activated_by: str
    gate_status: str = "PASS"
    prev_version: int | None = None

    def __post_init__(self) -> None:
        _validate_slug(self.slug)
        version_prefix(self.version)
        _validate_hash(self.bundle_hash, "bundle_hash")
        if not self.activated_at or not self.activated_by:
            raise ValueError("activation identity and timestamp are required")
        aliases = sorted(set(self.normalized_aliases))
        if not aliases or any(not alias for alias in aliases):
            raise ValueError("normalized_aliases must be non-empty strings")
        self.normalized_aliases = aliases

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": merchant_truth_pk(self.slug)},
            "SK": {"S": "TRUTH#ACTIVE"},
        }

    def to_item(self) -> dict[str, Any]:
        item = {
            **self.key,
            "TYPE": {"S": "MERCHANT_TRUTH_ACTIVE"},
            "slug": {"S": self.slug},
            "version": {"N": str(self.version)},
            "bundle_hash": {"S": self.bundle_hash},
            "normalized_aliases": to_dynamodb_value(self.normalized_aliases),
            "activated_at": {"S": self.activated_at},
            "activated_by": {"S": self.activated_by},
            "gate_status": {"S": self.gate_status},
        }
        if self.prev_version is not None:
            item["prev_version"] = {"N": str(self.prev_version)}
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "MerchantTruthActive":
        data = _item_to_python(item)
        return cls(
            slug=data["slug"],
            version=int(data["version"]),
            bundle_hash=data["bundle_hash"],
            normalized_aliases=data["normalized_aliases"],
            activated_at=data["activated_at"],
            activated_by=data["activated_by"],
            gate_status=data.get("gate_status", "PASS"),
            prev_version=data.get("prev_version"),
        )


@dataclass(eq=True)
class MerchantTruthProposal(DynamoDBEntity):
    """An append-only observation with a conditionally mutable status."""

    slug: str
    created_at: str
    claim_slug: str
    claim: str
    receipt_refs: list[str] = field(default_factory=list)
    status: str = "OPEN"
    resolved_by_version: int | None = None
    resolution: str | None = None

    def __post_init__(self) -> None:
        _validate_slug(self.slug)
        if not self.created_at or not self.claim_slug or not self.claim:
            raise ValueError("proposal timestamp, claim slug, and claim required")
        if self.status not in PROPOSAL_STATUSES:
            raise ValueError(f"invalid proposal status: {self.status}")

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": merchant_truth_pk(self.slug)},
            "SK": {"S": f"PROPOSED#{self.created_at}#{self.claim_slug}"},
        }

    def to_item(self) -> dict[str, Any]:
        item = {
            **self.key,
            "TYPE": {"S": "MERCHANT_TRUTH_PROPOSAL"},
            "slug": {"S": self.slug},
            "created_at": {"S": self.created_at},
            "claim_slug": {"S": self.claim_slug},
            "claim": {"S": self.claim},
            "receipt_refs": to_dynamodb_value(self.receipt_refs),
            "status": {"S": self.status},
        }
        if self.resolved_by_version is not None:
            item["resolved_by_version"] = {"N": str(self.resolved_by_version)}
        if self.resolution is not None:
            item["resolution"] = {"S": self.resolution}
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "MerchantTruthProposal":
        data = _item_to_python(item)
        return cls(
            slug=data["slug"],
            created_at=data["created_at"],
            claim_slug=data["claim_slug"],
            claim=data["claim"],
            receipt_refs=data.get("receipt_refs", []),
            status=data.get("status", "OPEN"),
            resolved_by_version=data.get("resolved_by_version"),
            resolution=data.get("resolution"),
        )


@dataclass(eq=True)
class MerchantTruthAudit(DynamoDBEntity):
    """Append-only audit record for a governed truth mutation."""

    slug: str
    created_at: str
    audit_id: str
    action: str
    version: int
    bundle_hash: str
    details: dict[str, Any]

    def __post_init__(self) -> None:
        _validate_slug(self.slug)
        version_prefix(self.version)
        _validate_hash(self.bundle_hash, "bundle_hash")
        if not self.created_at or not self.audit_id or not self.action:
            raise ValueError("audit timestamp, id, and action are required")

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": merchant_truth_pk(self.slug)},
            "SK": {"S": f"AUDIT#{self.created_at}#{self.audit_id}"},
        }

    def to_item(self) -> dict[str, Any]:
        return {
            **self.key,
            "TYPE": {"S": "MERCHANT_TRUTH_AUDIT"},
            "slug": {"S": self.slug},
            "created_at": {"S": self.created_at},
            "audit_id": {"S": self.audit_id},
            "action": {"S": self.action},
            "version": {"N": str(self.version)},
            "bundle_hash": {"S": self.bundle_hash},
            "details": to_dynamodb_value(self.details),
        }
