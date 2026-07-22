"""Fail-closed resolution and caching for versioned merchant truth."""

from __future__ import annotations

import json
import re
import unicodedata
import warnings
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from receipt_dynamo.data.shared_exceptions import MerchantTruthIntegrityError
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
)


class TruthResolutionMode(str, Enum):
    """The four explicit ways a render can resolve merchant truth."""

    PINNED = "pinned"
    ONLINE_ACTIVE = "online-active"
    OFFLINE_FALLBACK = "offline-fallback"
    FIXTURE = "fixture"


class MerchantTruthReader(Protocol):
    """Read-only surface required from ``DynamoClient``."""

    def list_active_merchant_truth(self) -> list[MerchantTruthActive]: ...

    def list_merchant_truth_manifests(
        self,
    ) -> list["MerchantTruthManifest"]: ...

    def get_merchant_truth_manifest(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> "MerchantTruthManifest | None": ...

    def list_merchant_truth_components(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> list[MerchantTruthComponent]: ...

    def get_active_merchant_truth(
        self, slug: str, *, consistent_read: bool = False
    ) -> MerchantTruthActive | None: ...

    def read_merchant_truth_bundle_items(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class MerchantTruthArtifact:
    """A fully realized bundle or an explicitly incomplete degradation."""

    slug: str
    version: int
    bundle_hash: str | None
    expected_bundle_hash: str
    components: dict[str, Any]
    mode: TruthResolutionMode
    incomplete: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def gate_eligible(self) -> bool:
        """Whether this artifact may participate in a fidelity gate."""
        return (
            not self.incomplete
            and self.bundle_hash is not None
            and self.bundle_hash == self.expected_bundle_hash
        )

    def assert_gate_eligible(
        self,
        *,
        expected_version: int | None = None,
        expected_bundle_hash: str | None = None,
    ) -> None:
        """Fail if realized truth differs from the run's captured tuple."""
        if not self.gate_eligible:
            raise MerchantTruthIntegrityError(
                "incomplete or unverified merchant truth cannot pass gates"
            )
        if expected_version is not None and self.version != expected_version:
            raise MerchantTruthIntegrityError(
                "realized version does not match the expected version"
            )
        if (
            expected_bundle_hash is not None
            and self.bundle_hash != expected_bundle_hash
        ):
            raise MerchantTruthIntegrityError(
                "realized bundle hash does not match the expected hash"
            )


def normalize_merchant_alias(value: str) -> str:
    """Normalize merchant names using a stable punctuation-insensitive rule."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return " ".join(normalized.split())


class MerchantTruthAliasCollisionWarning(RuntimeWarning):
    """A fleet alias is claimed by more than one ACTIVE merchant."""


def build_fleet_alias_map(
    active_records: list[MerchantTruthActive],
) -> dict[str, str]:
    """Build the fleet alias -> slug map, degrading loudly on collisions.

    Global alias uniqueness is enforced at activation time (the accessor
    refuses to activate a bundle whose aliases collide with a different
    ACTIVE merchant). This builder is defense in depth: if a collision
    reaches the fleet anyway, one bad activation must not take down every
    consumer that builds the alias map. Each colliding alias resolves to
    a deterministic winner — the slug whose own normalized form equals
    the alias (it owns the alias as its identity), else the
    lexicographically smallest slug — and a loud
    ``MerchantTruthAliasCollisionWarning`` lists the losers.
    """
    claims: dict[str, set[str]] = {}
    for active in sorted(active_records, key=lambda item: item.slug):
        for alias in [active.slug, *active.normalized_aliases]:
            claims.setdefault(normalize_merchant_alias(alias), set()).add(
                active.slug
            )
    alias_map: dict[str, str] = {}
    for alias, slugs in claims.items():
        if len(slugs) == 1:
            (alias_map[alias],) = slugs
            continue
        winner = next(
            (
                slug
                for slug in sorted(slugs)
                if normalize_merchant_alias(slug) == alias
            ),
            min(slugs),
        )
        losers = sorted(slugs - {winner})
        warnings.warn(
            f"fleet alias collision: {alias!r} is claimed by "
            f"{sorted(slugs)}; resolving to {winner!r} (deterministic "
            f"winner), ignoring {losers}. Fix the losing activation(s) — "
            "activation-time uniqueness should have refused this.",
            MerchantTruthAliasCollisionWarning,
            stacklevel=2,
        )
        alias_map[alias] = winner
    return alias_map


class MerchantTruthLoader:
    """Resolve aliases, load complete bundles, and manage immutable caches."""

    def __init__(
        self,
        reader: MerchantTruthReader | None,
        cache_dir: Path,
        *,
        ci: bool = False,
    ) -> None:
        self._reader = reader
        self._cache_dir = cache_dir
        self._ci = ci

    def load(
        self,
        merchant_name: str,
        mode: TruthResolutionMode,
        *,
        pin_version: int | None = None,
        pin_bundle_hash: str | None = None,
        fixture_path: Path | None = None,
    ) -> MerchantTruthArtifact:
        """Resolve and verify one bundle according to an explicit mode."""
        if mode is TruthResolutionMode.FIXTURE:
            if fixture_path is None:
                raise ValueError("fixture mode requires fixture_path")
            return self._load_fixture(fixture_path)
        if mode is TruthResolutionMode.OFFLINE_FALLBACK:
            return self._load_offline(merchant_name)
        if self._reader is None:
            raise MerchantTruthIntegrityError(
                f"{mode.value} resolution requires an online reader"
            )

        alias_map, fleet_active = self._load_online_alias_map()
        slug = self._resolve_alias(merchant_name, alias_map)
        if mode is TruthResolutionMode.PINNED:
            if pin_version is None or pin_bundle_hash is None:
                raise ValueError(
                    "pinned mode requires pin_version and pin_bundle_hash"
                )
            return self._load_online_bundle(
                slug,
                pin_version,
                pin_bundle_hash,
                mode,
            )

        captured = self._reader.get_active_merchant_truth(
            slug, consistent_read=True
        )
        if captured is None:
            raise MerchantTruthIntegrityError(
                f"merchant {slug!r} has no ACTIVE pointer"
            )
        fleet_pointer = fleet_active[slug]
        if (
            fleet_pointer.version != captured.version
            or fleet_pointer.bundle_hash != captured.bundle_hash
        ):
            # GSIs are eventually consistent. The strong point read is the
            # authoritative tuple captured once for this run.
            fleet_active[slug] = captured
        self._cache_active(captured)
        return self._load_online_bundle(
            slug,
            captured.version,
            captured.bundle_hash,
            mode,
        )

    def _load_online_alias_map(
        self,
    ) -> tuple[dict[str, str], dict[str, MerchantTruthActive]]:
        assert self._reader is not None
        active_records = self._reader.list_active_merchant_truth()
        active_by_slug: dict[str, MerchantTruthActive] = {}
        for active in active_records:
            if active.slug in active_by_slug:
                raise MerchantTruthIntegrityError(
                    f"duplicate ACTIVE pointer for {active.slug}"
                )
            active_by_slug[active.slug] = active
        alias_map = build_fleet_alias_map(active_records)
        self._write_json(
            self._cache_dir / "fleet-active.json",
            {"items": [record.to_item() for record in active_records]},
        )
        return alias_map, active_by_slug

    @staticmethod
    def _resolve_alias(merchant_name: str, alias_map: dict[str, str]) -> str:
        normalized = normalize_merchant_alias(merchant_name)
        try:
            return alias_map[normalized]
        except KeyError as error:
            raise MerchantTruthIntegrityError(
                f"unknown merchant alias: {merchant_name!r}"
            ) from error

    def _load_online_bundle(
        self,
        slug: str,
        version: int,
        expected_bundle_hash: str,
        mode: TruthResolutionMode,
    ) -> MerchantTruthArtifact:
        assert self._reader is not None
        items = self._reader.read_merchant_truth_bundle_items(
            slug, version, consistent_read=True
        )
        manifest, components = self._parse_and_verify(
            items,
            slug=slug,
            version=version,
            expected_bundle_hash=expected_bundle_hash,
        )
        self._cache_bundle(manifest, components)
        return MerchantTruthArtifact(
            slug=slug,
            version=version,
            bundle_hash=manifest.bundle_hash,
            expected_bundle_hash=expected_bundle_hash,
            components={item.name: item.payload for item in components},
            mode=mode,
        )

    def _load_offline(self, merchant_name: str) -> MerchantTruthArtifact:
        fleet_path = self._cache_dir / "fleet-active.json"
        if not fleet_path.exists():
            raise MerchantTruthIntegrityError("offline fleet cache is missing")
        fleet_data = self._read_json(fleet_path)
        active_records = [
            MerchantTruthActive.from_item(item)
            for item in fleet_data.get("items", [])
        ]
        alias_map = self._alias_map_from_records(active_records)
        slug = self._resolve_alias(merchant_name, alias_map)
        active_path = self._cache_dir / slug / "active.json"
        if not active_path.exists():
            raise MerchantTruthIntegrityError(
                f"offline ACTIVE cache is missing for {slug}"
            )
        active = MerchantTruthActive.from_item(self._read_json(active_path))
        try:
            items = self._read_cached_bundle(active)
            manifest, components = self._parse_and_verify(
                items,
                slug=slug,
                version=active.version,
                expected_bundle_hash=active.bundle_hash,
            )
        except (
            KeyError,
            OSError,
            ValueError,
            MerchantTruthIntegrityError,
        ) as error:
            if self._ci:
                raise MerchantTruthIntegrityError(
                    "CI offline fallback is incomplete and fails closed"
                ) from error
            return MerchantTruthArtifact(
                slug=slug,
                version=active.version,
                bundle_hash=None,
                expected_bundle_hash=active.bundle_hash,
                components=self._best_effort_cached_payloads(active),
                mode=TruthResolutionMode.OFFLINE_FALLBACK,
                incomplete=True,
                warnings=(str(error),),
            )
        return MerchantTruthArtifact(
            slug=slug,
            version=active.version,
            bundle_hash=manifest.bundle_hash,
            expected_bundle_hash=active.bundle_hash,
            components={item.name: item.payload for item in components},
            mode=TruthResolutionMode.OFFLINE_FALLBACK,
        )

    def _load_fixture(self, fixture_path: Path) -> MerchantTruthArtifact:
        fixture = self._read_json(fixture_path)
        items = fixture.get("items")
        if not isinstance(items, list):
            raise MerchantTruthIntegrityError(
                "fixture must contain a low-level DynamoDB items list"
            )
        manifest_items = [
            item
            for item in items
            if item.get("TYPE", {}).get("S") == "MERCHANT_TRUTH_MANIFEST"
        ]
        if len(manifest_items) != 1:
            raise MerchantTruthIntegrityError(
                "fixture must contain exactly one manifest"
            )
        expected = MerchantTruthManifest.from_item(manifest_items[0])
        manifest, components = self._parse_and_verify(
            items,
            slug=expected.slug,
            version=expected.version,
            expected_bundle_hash=expected.bundle_hash,
        )
        return MerchantTruthArtifact(
            slug=manifest.slug,
            version=manifest.version,
            bundle_hash=manifest.bundle_hash,
            expected_bundle_hash=manifest.bundle_hash,
            components={item.name: item.payload for item in components},
            mode=TruthResolutionMode.FIXTURE,
        )

    @staticmethod
    def _parse_and_verify(
        items: list[dict[str, Any]],
        *,
        slug: str,
        version: int,
        expected_bundle_hash: str,
    ) -> tuple[MerchantTruthManifest, list[MerchantTruthComponent]]:
        manifests = [
            MerchantTruthManifest.from_item(item)
            for item in items
            if item.get("TYPE", {}).get("S") == "MERCHANT_TRUTH_MANIFEST"
        ]
        components = [
            MerchantTruthComponent.from_item(item)
            for item in items
            if item.get("TYPE", {}).get("S") == "MERCHANT_TRUTH_COMPONENT"
        ]
        if len(manifests) != 1:
            raise MerchantTruthIntegrityError(
                "bundle must contain exactly one manifest"
            )
        manifest = manifests[0]
        if manifest.slug != slug or manifest.version != version:
            raise MerchantTruthIntegrityError(
                "manifest key does not match the requested bundle"
            )
        if manifest.status != "SEALED" or manifest.gate_status != "PASS":
            raise MerchantTruthIntegrityError(
                "only a SEALED/PASS bundle can be resolved"
            )
        if manifest.bundle_hash != expected_bundle_hash:
            raise MerchantTruthIntegrityError(
                "manifest does not match the expected bundle hash"
            )
        by_name = {item.name: item for item in components}
        if len(by_name) != len(components) or set(by_name) != COMPONENT_NAMES:
            raise MerchantTruthIntegrityError(
                "loaded component names/count do not match the contract"
            )
        actual_hashes = {
            name: by_name[name].content_hash for name in sorted(by_name)
        }
        if actual_hashes != manifest.component_hashes:
            raise MerchantTruthIntegrityError(
                "component hashes do not match the manifest"
            )
        if compute_bundle_hash(actual_hashes) != manifest.bundle_hash:
            raise MerchantTruthIntegrityError(
                "bundle hash verification failed"
            )
        return manifest, [by_name[name] for name in sorted(by_name)]

    @staticmethod
    def _alias_map_from_records(
        active_records: list[MerchantTruthActive],
    ) -> dict[str, str]:
        # Same degradation as the online map: a collision in the cached
        # fleet warns and resolves deterministically instead of hard-
        # failing every offline consumer.
        return build_fleet_alias_map(active_records)

    def _cache_active(self, active: MerchantTruthActive) -> None:
        self._write_json(
            self._cache_dir / active.slug / "active.json", active.to_item()
        )

    def _cache_bundle(
        self,
        manifest: MerchantTruthManifest,
        components: list[MerchantTruthComponent],
    ) -> None:
        version_dir = self._version_dir(manifest.slug, manifest.version)
        self._write_json(version_dir / "manifest.json", manifest.to_item())
        for component in components:
            self._write_json(
                version_dir
                / f"{component.name}-{component.content_hash}.json",
                component.to_item(),
            )

    def _read_cached_bundle(
        self, active: MerchantTruthActive
    ) -> list[dict[str, Any]]:
        version_dir = self._version_dir(active.slug, active.version)
        manifest_item = self._read_json(version_dir / "manifest.json")
        manifest = MerchantTruthManifest.from_item(manifest_item)
        items = [manifest_item]
        for name, digest in manifest.component_hashes.items():
            items.append(
                self._read_json(version_dir / f"{name}-{digest}.json")
            )
        return items

    def _best_effort_cached_payloads(
        self, active: MerchantTruthActive
    ) -> dict[str, Any]:
        version_dir = self._version_dir(active.slug, active.version)
        payloads: dict[str, Any] = {}
        for path in version_dir.glob("*.json"):
            if path.name == "manifest.json":
                continue
            try:
                component = MerchantTruthComponent.from_item(
                    self._read_json(path)
                )
                payloads[component.name] = component.payload
            except (KeyError, OSError, ValueError):
                continue
        return payloads

    def _version_dir(self, slug: str, version: int) -> Path:
        return self._cache_dir / slug / f"v{version:010d}"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise MerchantTruthIntegrityError(
                f"expected JSON object in {path}"
            )
        return value

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        temporary.replace(path)


def verify_merchant_truth_bundle_items(
    items: list[dict[str, Any]],
    *,
    slug: str,
    version: int,
    expected_bundle_hash: str,
) -> tuple[MerchantTruthManifest, list[MerchantTruthComponent]]:
    """Public fail-closed verifier shared by loaders and promotion."""
    return MerchantTruthLoader._parse_and_verify(  # pylint: disable=protected-access
        items,
        slug=slug,
        version=version,
        expected_bundle_hash=expected_bundle_hash,
    )
