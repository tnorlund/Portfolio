"""Contracts for alias resolution, bundle integrity, and truth modes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from receipt_dynamo.data._merchant_truth import _MerchantTruth
from receipt_dynamo.data.shared_exceptions import MerchantTruthIntegrityError
from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
)
from receipt_dynamo.merchant_truth_loader import (
    MerchantTruthLoader,
    TruthResolutionMode,
)

pytestmark = pytest.mark.unit
SLUG = "sprouts-farmers-market"
NOW = "2026-07-20T16:00:00+00:00"


def build_bundle(
    slug: str = SLUG, version: int = 1
) -> tuple[MerchantTruthActive, list[dict[str, Any]]]:
    components = [
        MerchantTruthComponent(
            slug=slug,
            version=version,
            name=name,
            payload={"name": name, "version": version},
            provenance={"source_kind": "migration"},
        )
        for name in sorted(COMPONENT_NAMES)
    ]
    hashes = {item.name: item.content_hash for item in components}
    bundle_hash = compute_bundle_hash(hashes)
    manifest = MerchantTruthManifest(
        slug=slug,
        version=version,
        component_hashes=hashes,
        bundle_hash=bundle_hash,
        status="SEALED",
        provenance={"written_by": "test"},
        mint_run_id=f"run-{version}",
        gate_status="PASS",
    )
    active = MerchantTruthActive(
        slug=slug,
        version=version,
        bundle_hash=bundle_hash,
        normalized_aliases=["sprouts", "sprouts farmers market"],
        activated_at=NOW,
        activated_by="owner",
    )
    return active, [
        manifest.to_item(),
        *[item.to_item() for item in components],
    ]


def build_unsealed_bundle(
    slug: str = SLUG, version: int = 1
) -> tuple[MerchantTruthActive, list[dict[str, Any]]]:
    """A structurally valid bundle whose manifest is OPEN / not gate-passed."""
    components = [
        MerchantTruthComponent(
            slug=slug,
            version=version,
            name=name,
            payload={"name": name, "version": version},
            provenance={"source_kind": "migration"},
        )
        for name in sorted(COMPONENT_NAMES)
    ]
    hashes = {item.name: item.content_hash for item in components}
    bundle_hash = compute_bundle_hash(hashes)
    manifest = MerchantTruthManifest(
        slug=slug,
        version=version,
        component_hashes=hashes,
        bundle_hash=bundle_hash,
        status="OPEN",
        provenance={"written_by": "test"},
        mint_run_id=f"run-{version}",
        gate_status="PENDING",
    )
    active = MerchantTruthActive(
        slug=slug,
        version=version,
        bundle_hash=bundle_hash,
        normalized_aliases=["sprouts", "sprouts farmers market"],
        activated_at=NOW,
        activated_by="owner",
    )
    return active, [
        manifest.to_item(),
        *[item.to_item() for item in components],
    ]


class FakeReader:
    def __init__(
        self,
        active_records: list[MerchantTruthActive],
        bundle_items: list[dict[str, Any]],
    ) -> None:
        self.active_records = active_records
        self.bundle_items = bundle_items
        self.fleet_calls = 0
        self.active_calls = 0
        self.bundle_calls = 0

    def list_active_merchant_truth(self) -> list[MerchantTruthActive]:
        self.fleet_calls += 1
        return self.active_records

    def get_active_merchant_truth(
        self, slug: str, *, consistent_read: bool = False
    ) -> MerchantTruthActive | None:
        del consistent_read
        self.active_calls += 1
        return next(
            (item for item in self.active_records if item.slug == slug), None
        )

    def read_merchant_truth_bundle_items(
        self,
        slug: str,
        version: int,
        *,
        consistent_read: bool = False,
    ) -> list[dict[str, Any]]:
        del slug, version, consistent_read
        self.bundle_calls += 1
        return self.bundle_items


def test_online_active_builds_alias_map_from_one_fleet_query(
    tmp_path: Path,
) -> None:
    active, items = build_bundle()
    reader = FakeReader([active], items)
    loader = MerchantTruthLoader(reader, tmp_path)

    artifact = loader.load("SPROUTS!!!", TruthResolutionMode.ONLINE_ACTIVE)

    assert artifact.slug == SLUG
    assert artifact.version == 1
    assert artifact.gate_eligible
    assert set(artifact.components) == COMPONENT_NAMES
    assert reader.fleet_calls == 1
    assert reader.active_calls == 1
    assert reader.bundle_calls == 1


def test_fleet_alias_collision_fails_before_bundle_read(
    tmp_path: Path,
) -> None:
    first, items = build_bundle()
    second = MerchantTruthActive(
        slug="other-market",
        version=1,
        bundle_hash=first.bundle_hash,
        normalized_aliases=["sprouts"],
        activated_at=NOW,
        activated_by="owner",
    )
    reader = FakeReader([first, second], items)

    with pytest.raises(MerchantTruthIntegrityError, match="ambiguous"):
        MerchantTruthLoader(reader, tmp_path).load(
            "sprouts", TruthResolutionMode.ONLINE_ACTIVE
        )

    assert reader.fleet_calls == 1
    assert reader.bundle_calls == 0


def test_pinned_mode_requires_and_verifies_exact_tuple(tmp_path: Path) -> None:
    active, items = build_bundle()
    loader = MerchantTruthLoader(FakeReader([active], items), tmp_path)

    artifact = loader.load(
        "sprouts",
        TruthResolutionMode.PINNED,
        pin_version=1,
        pin_bundle_hash=active.bundle_hash,
    )
    artifact.assert_gate_eligible(
        expected_version=1, expected_bundle_hash=active.bundle_hash
    )

    with pytest.raises(MerchantTruthIntegrityError, match="expected"):
        loader.load(
            "sprouts",
            TruthResolutionMode.PINNED,
            pin_version=1,
            pin_bundle_hash="0" * 64,
        )


def test_bundle_missing_declared_component_fails_closed(
    tmp_path: Path,
) -> None:
    active, items = build_bundle()
    incomplete_items = [
        item
        for item in items
        if item.get("component", {}).get("S") != "layout"
    ]
    loader = MerchantTruthLoader(
        FakeReader([active], incomplete_items), tmp_path
    )

    with pytest.raises(MerchantTruthIntegrityError, match="names/count"):
        loader.load("sprouts", TruthResolutionMode.ONLINE_ACTIVE)


def test_offline_fallback_uses_cache_and_marks_damage_incomplete(
    tmp_path: Path,
) -> None:
    active, items = build_bundle()
    online = MerchantTruthLoader(FakeReader([active], items), tmp_path)
    online.load("sprouts", TruthResolutionMode.ONLINE_ACTIVE)

    offline = MerchantTruthLoader(None, tmp_path)
    complete = offline.load("sprouts", TruthResolutionMode.OFFLINE_FALLBACK)
    assert complete.gate_eligible
    component_file = next(
        (tmp_path / SLUG / "v0000000001").glob("layout-*.json")
    )
    component_file.unlink()

    degraded = offline.load("sprouts", TruthResolutionMode.OFFLINE_FALLBACK)
    assert degraded.incomplete is True
    assert degraded.bundle_hash is None
    assert degraded.gate_eligible is False
    with pytest.raises(MerchantTruthIntegrityError, match="cannot pass"):
        degraded.assert_gate_eligible()


def test_ci_incomplete_offline_load_fails_closed(tmp_path: Path) -> None:
    active, items = build_bundle()
    MerchantTruthLoader(FakeReader([active], items), tmp_path).load(
        "sprouts", TruthResolutionMode.ONLINE_ACTIVE
    )
    next((tmp_path / SLUG / "v0000000001").glob("assets-*.json")).unlink()

    with pytest.raises(MerchantTruthIntegrityError, match="CI"):
        MerchantTruthLoader(None, tmp_path, ci=True).load(
            "sprouts", TruthResolutionMode.OFFLINE_FALLBACK
        )


def test_fixture_mode_never_needs_a_reader(tmp_path: Path) -> None:
    active, items = build_bundle()
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps({"items": items}), encoding="utf-8")

    artifact = MerchantTruthLoader(None, tmp_path / "cache").load(
        "ignored",
        TruthResolutionMode.FIXTURE,
        fixture_path=fixture_path,
    )

    assert artifact.mode is TruthResolutionMode.FIXTURE
    assert artifact.bundle_hash == active.bundle_hash
    assert artifact.gate_eligible


def test_fixture_mode_rejects_unsealed_manifest(tmp_path: Path) -> None:
    _, items = build_unsealed_bundle()
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps({"items": items}), encoding="utf-8")

    with pytest.raises(MerchantTruthIntegrityError, match="SEALED/PASS"):
        MerchantTruthLoader(None, tmp_path / "cache").load(
            "ignored",
            TruthResolutionMode.FIXTURE,
            fixture_path=fixture_path,
        )


def test_pinned_mode_rejects_unsealed_manifest(tmp_path: Path) -> None:
    active, items = build_unsealed_bundle()
    loader = MerchantTruthLoader(FakeReader([active], items), tmp_path)

    with pytest.raises(MerchantTruthIntegrityError, match="SEALED/PASS"):
        loader.load(
            "sprouts",
            TruthResolutionMode.PINNED,
            pin_version=1,
            pin_bundle_hash=active.bundle_hash,
        )


def test_loader_rejects_self_consistent_swapped_component(
    tmp_path: Path,
) -> None:
    # A valid SEALED bundle, then one component's item is replaced by a
    # different but internally self-consistent component (its own content_hash
    # matches its payload) that the manifest never declared. Both the
    # per-component hash check and the recomputed bundle-hash check must
    # reject it, so removing either single check still fails closed.
    active, items = build_bundle()
    swapped = MerchantTruthComponent(
        slug=SLUG,
        version=1,
        name="layout",
        payload={"name": "layout", "version": 1, "tampered": True},
        provenance={"source_kind": "migration"},
    )
    swapped_items = [
        swapped.to_item()
        if item.get("component", {}).get("S") == "layout"
        else item
        for item in items
    ]
    loader = MerchantTruthLoader(FakeReader([active], swapped_items), tmp_path)

    with pytest.raises(MerchantTruthIntegrityError, match="hash"):
        loader.load("sprouts", TruthResolutionMode.ONLINE_ACTIVE)


class PagedLowLevelClient:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.requests: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return self.pages.pop(0)


def test_bundle_reader_paginates_to_last_evaluated_key() -> None:
    active, items = build_bundle()
    del active
    page_key = {"PK": {"S": "pk"}, "SK": {"S": "sk"}}
    client = PagedLowLevelClient(
        [
            {"Items": items[:3], "LastEvaluatedKey": page_key},
            {"Items": items[3:]},
        ]
    )
    truth = _MerchantTruth()
    truth.table_name = "table"
    truth._client = client

    result = truth.read_merchant_truth_bundle_items(
        SLUG, 1, consistent_read=True
    )

    assert result == items
    assert len(client.requests) == 2
    assert "ExclusiveStartKey" not in client.requests[0]
    assert client.requests[1]["ExclusiveStartKey"] == page_key
    assert client.requests[1]["ConsistentRead"] is True
