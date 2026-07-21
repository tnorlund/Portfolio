"""Golden-snapshot + unit tests for scripts/merchant_truth_diff.py (W5).

Fixture bundles are built through the real MerchantTruth entities, so every
content_hash / bundle_hash in the golden files is computed by the same
canonical-JSON hashing the live table uses. Regenerate goldens with:

    UPDATE_GOLDEN=1 pytest tests/test_merchant_truth_diff.py
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from receipt_dynamo.entities.merchant_truth import (
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
    hash_payload,
)
from scripts import merchant_truth_diff as mtd

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "merchant_truth_diff"
SLUG = "vons"
GIT_SHA_V1 = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
GIT_SHA_V2 = "feedbeef00112233445566778899aabbccddeeff"
SEALED_AT_V1 = "2026-07-21T05:00:00+00:00"
SEALED_AT_V2 = "2026-07-22T05:00:00+00:00"

# ---------------------------------------------------------------------------
# Fixture payloads (v1 mirrors the migration writer's profile-derived shape)
# ---------------------------------------------------------------------------

LEGACY_DOC: dict[str, Any] = {
    "profiles": {
        "Vons": {
            "aliases": ["VONS #2011"],
            "section_scale": {"FOOTER": 0.5},
            "typography": {
                "bitmap_font": {"regular": "vons.glyphs.npz"},
                "bitmap_thin": 0.0,
                "condense": 0.93,
                "stylemap": "vons_stylemap.json",
                "reverse_total": True,
                "face_source": "stylemap",
                "_face_source_comment": "legacy comment (discarded)",
            },
            "logo": "vons_logo.png",
            "logo_anchor": {"phrases": ["VONS"], "center": True},
            "layout_template": {
                "version": 1,
                "_comment": "migration note (discarded)",
                "measured": {
                    "receipts": 12,
                    "tool_git_sha": "aaaa11112222",
                    "tool_dirty": False,
                },
                "columns": {
                    "items": [
                        {
                            "role": "desc",
                            "anchor": "left",
                            "x": 0.007,
                            "spread": 0.0078,
                            "support": 6,
                        },
                        {
                            "role": "amount",
                            "anchor": "right",
                            "x": 0.9439,
                            "spread": 0.0096,
                            "support": 8,
                        },
                    ],
                    "payment": [
                        {
                            "role": "label",
                            "anchor": "left",
                            "x": 0.0063,
                            "spread": 0.0124,
                            "support": 8,
                        },
                        {
                            "role": "amount",
                            "anchor": "right",
                            "x": 0.9417,
                            "spread": 0.0124,
                            "support": 9,
                        },
                    ],
                },
            },
            "header": {"lines": ["VONS", "(555) 555-0134"]},
        }
    }
}

CATALOG_ITEMS_V1 = [
    {"category": "dairy", "product_text": "MILK 2%", "price": "3.99"},
    {"category": "produce", "product_text": "BANANAS", "price": "0.99"},
    {"category": "produce", "product_text": "FUJI APPLES", "price": "2.49"},
]
CATALOG_ITEMS_V2 = [
    {"category": "dairy", "product_text": "MILK 2%", "price": "4.19"},
    {"category": "produce", "product_text": "BANANAS", "price": "0.99"},
    {"category": "produce", "product_text": "ORANGES", "price": "3.49"},
]

STYLEMAP_PAYLOAD = {
    "available": True,
    "document": {
        "version": 1,
        "source": "stylescan",
        "sections": {"ITEMS": [{"face": "regular"}]},
    },
    "source": {
        "bucket_alias": "merchant-font-artifacts",
        "s3_key": "fonts/vons/stylemap-5f5f.json",
        "content_hash": "5f" * 32,
        "size": 812,
    },
}


def _v1_payloads() -> dict[str, Any]:
    profile = LEGACY_DOC["profiles"]["Vons"]
    template = copy.deepcopy(profile["layout_template"])
    template.pop("_comment")
    return {
        "identity": {
            "merchant_name": "Vons",
            "slug": SLUG,
            "aliases": ["VONS #2011"],
            "upper_slug": "VONS",
            "normalized_aliases": ["vons", "vons 2011"],
        },
        "typography": {
            "typography": {
                "bitmap_font": {"regular": "vons.glyphs.npz"},
                "bitmap_thin": 0.0,
                "condense": 0.93,
            },
            "section_scale": {"FOOTER": 0.5},
        },
        "stylemap": copy.deepcopy(STYLEMAP_PAYLOAD),
        "layout": {"available": True, "template": template},
        "assets": {
            "fonts": {
                "regular": {
                    "bucket_alias": "merchant-font-artifacts",
                    "s3_key": "fonts/vons/vons-1111.glyphs.npz",
                    "content_hash": "11" * 32,
                    "source_commit": GIT_SHA_V1,
                    "compiled_at": "2026-07-01T00:00:00+00:00",
                    "cap_h": 34.0,
                    "advance_ratio": 0.61,
                    "pitch_check": "ok",
                    "glyph_count": 96,
                    "cache_filename": "vons.glyphs.npz",
                }
            },
            "profile": {
                "logo": "vons_logo.png",
                "logo_anchor": {"phrases": ["VONS"], "center": True},
                "stylemap_filename": "vons_stylemap.json",
            },
            "logo": {
                "bucket_alias": "merchant-font-artifacts",
                "s3_key": "fonts/vons/logo-2222.png",
                "content_hash": "22" * 32,
                "size": 4096,
            },
            "missing_merchant_font": False,
        },
        "flags": {
            "typography": {"reverse_total": True, "face_source": "stylemap"},
            "header": {"lines": ["VONS", "(555) 555-0134"]},
        },
        "catalog_snapshot": {
            "items": copy.deepcopy(CATALOG_ITEMS_V1),
            "item_count": len(CATALOG_ITEMS_V1),
            "catalog_hash": hash_payload(CATALOG_ITEMS_V1),
            "as_of": "2026-07-20T00:00:00+00:00",
        },
    }


def _v2_payloads() -> dict[str, Any]:
    payloads = _v1_payloads()
    typography = payloads["typography"]["typography"]
    typography["condense"] = 0.95
    typography["ink"] = 0.82
    template = payloads["layout"]["template"]
    template["measured"] = {
        "receipts": 15,
        "tool_git_sha": "bbbb33334444",
        "tool_dirty": False,
    }
    template["columns"]["items"][1]["x"] = 0.9502
    template["columns"]["items"][1]["support"] = 11
    template["columns"]["payment"] = [template["columns"]["payment"][1]]
    template["columns"]["footer"] = [
        {
            "role": "desc",
            "anchor": "left",
            "x": 0.0171,
            "spread": 0.0186,
            "support": 8,
        }
    ]
    font = payloads["assets"]["fonts"]["regular"]
    font["s3_key"] = "fonts/vons/vons-3333.glyphs.npz"
    font["content_hash"] = "33" * 32
    font["source_commit"] = GIT_SHA_V2
    font["compiled_at"] = "2026-07-22T00:00:00+00:00"
    payloads["flags"]["typography"]["reverse_total"] = False
    payloads["catalog_snapshot"] = {
        "items": copy.deepcopy(CATALOG_ITEMS_V2),
        "item_count": len(CATALOG_ITEMS_V2),
        "catalog_hash": hash_payload(CATALOG_ITEMS_V2),
        "as_of": "2026-07-22T00:00:00+00:00",
    }
    return payloads


def _component_provenance(version: int) -> dict[str, Any]:
    if version == 1:
        return {
            "source_kind": "migration",
            "written_by": {
                "kind": "migration",
                "name": "merchant_truth_v1",
                "version": "1",
            },
            "source_path": "scripts/merchant_profiles.json",
            "git_sha": GIT_SHA_V1,
            "pipeline": "merchant_truth_v1",
            "pipeline_version": "1",
            "measured_at": None,
            "provenance_completeness": "legacy",
        }
    return {
        "source_kind": "measurement",
        "written_by": {
            "kind": "measurement_pipeline",
            "name": "glyphstudio",
            "version": "1",
        },
        "source_path": "glyphstudio/layout_template.py",
        "git_sha": GIT_SHA_V2,
        "pipeline": "glyphstudio.layout_template",
        "pipeline_version": "1",
        "measured_at": "2026-07-22T04:00:00+00:00",
    }


def _gate_results(version: int) -> dict[str, Any]:
    if version == 1:
        return {
            "status": "PASS",
            "passed": True,
            "gate": "merchant_truth_v1_bootstrap",
            "kind": "migration-bootstrap-seal",
            "note": (
                "bootstrap seal: gate passes on dry-run<->live source "
                "parity only; no fidelity eval has run against "
                "truth-loaded renders yet"
            ),
            "evidence": {
                "git_sha": GIT_SHA_V1,
                "generated_at": "2026-07-21T04:00:00+00:00",
            },
            "written_by": {
                "kind": "migration",
                "name": "merchant_truth_v1",
                "version": "1",
            },
        }
    return {
        "status": "PASS",
        "passed": True,
        "gate": "full_fidelity_eval",
        "kind": "fidelity-eval",
        "note": "metrics PASS on truth-loaded renders",
        "evidence": {
            "git_sha": GIT_SHA_V2,
            "report": "docs/reports/nightly/2026-07-22.md",
        },
        "written_by": {
            "kind": "measurement_pipeline",
            "name": "glyphstudio",
            "version": "1",
        },
    }


def build_bundle(
    version: int,
    payloads: dict[str, Any],
    *,
    slug: str = SLUG,
    status: str = "SEALED",
    sealed_at: str | None = None,
) -> tuple[MerchantTruthManifest, list[MerchantTruthComponent]]:
    components = [
        MerchantTruthComponent(
            slug=slug,
            version=version,
            name=name,
            payload=payloads[name],
            provenance=_component_provenance(version),
        )
        for name in sorted(COMPONENT_NAMES)
    ]
    hashes = {item.name: item.content_hash for item in components}
    git_sha = GIT_SHA_V1 if version == 1 else GIT_SHA_V2
    run_id = f"merchant-truth-v{version}-{slug}-{git_sha[:12]}"
    manifest = MerchantTruthManifest(
        slug=slug,
        version=version,
        component_hashes=hashes,
        bundle_hash=compute_bundle_hash(hashes),
        status=status,
        provenance={
            "written_by": (
                {
                    "kind": "migration",
                    "name": "merchant_truth_v1",
                    "version": "1",
                }
                if version == 1
                else {
                    "kind": "measurement_pipeline",
                    "name": "glyphstudio",
                    "version": "1",
                }
            ),
            "minted_at": sealed_at or SEALED_AT_V1,
            "run_id": run_id,
            "git_sha": git_sha,
            "migration_blockers": [],
        },
        mint_run_id=run_id,
        gate_status="PASS" if status == "SEALED" else "PENDING",
        gate_results=_gate_results(version) if status == "SEALED" else {},
        sealed_at=sealed_at if status == "SEALED" else None,
    )
    return manifest, components


class StubReader:
    """MerchantTruthReader stub (mirrors the loader FakeReader shape)."""

    def __init__(
        self,
        bundles: list[
            tuple[MerchantTruthManifest, list[MerchantTruthComponent]]
        ],
        active_records: list[MerchantTruthActive] | None = None,
    ) -> None:
        self.manifests = {
            (manifest.slug, manifest.version): manifest
            for manifest, _ in bundles
        }
        self.components = {
            (manifest.slug, manifest.version): components
            for manifest, components in bundles
        }
        self.active_records = active_records or []
        self.calls = 0

    def get_merchant_truth_manifest(
        self, slug: str, version: int, *, consistent_read: bool = False
    ) -> MerchantTruthManifest | None:
        del consistent_read
        self.calls += 1
        return self.manifests.get((slug, version))

    def list_merchant_truth_components(
        self, slug: str, version: int, *, consistent_read: bool = False
    ) -> list[MerchantTruthComponent]:
        del consistent_read
        self.calls += 1
        return list(self.components.get((slug, version), []))

    def list_merchant_truth_manifests(self) -> list[MerchantTruthManifest]:
        self.calls += 1
        return list(self.manifests.values())

    def list_active_merchant_truth(self) -> list[MerchantTruthActive]:
        self.calls += 1
        return list(self.active_records)

    def get_active_merchant_truth(
        self, slug: str, *, consistent_read: bool = False
    ) -> MerchantTruthActive | None:
        del consistent_read
        self.calls += 1
        return next(
            (item for item in self.active_records if item.slug == slug),
            None,
        )

    def read_merchant_truth_bundle_items(
        self, slug: str, version: int, *, consistent_read: bool = False
    ) -> list[dict[str, Any]]:
        raise AssertionError(
            "merchant_truth_diff reads manifests/components, not raw items"
        )


def make_active(
    slug: str, version: int, bundle_hash: str
) -> MerchantTruthActive:
    return MerchantTruthActive(
        slug=slug,
        version=version,
        bundle_hash=bundle_hash,
        normalized_aliases=[slug],
        activated_at="2026-07-21T12:00:00+00:00",
        activated_by="owner",
    )


def assert_matches_golden(name: str, output: str) -> None:
    path = GOLDEN_DIR / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(output, encoding="utf-8")
    assert path.exists(), (
        f"missing golden {path}; regenerate with "
        "UPDATE_GOLDEN=1 pytest tests/test_merchant_truth_diff.py"
    )
    assert output == path.read_text(encoding="utf-8"), (
        f"output drifted from golden {path.name}; review, then regenerate "
        "with UPDATE_GOLDEN=1 pytest tests/test_merchant_truth_diff.py"
    )


# ---------------------------------------------------------------------------
# Golden snapshots: the three modes + the fleet sweep
# ---------------------------------------------------------------------------


def test_mode_a_version_diff_matches_golden(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader(
        [
            build_bundle(1, _v1_payloads(), sealed_at=SEALED_AT_V1),
            build_bundle(2, _v2_payloads(), sealed_at=SEALED_AT_V2),
        ]
    )

    exit_code = mtd.main(
        ["--slug", SLUG, "--from", "v1", "--to", "v2"], reader=reader
    )

    assert exit_code == 0
    assert_matches_golden("mode_a_version_diff.md", capsys.readouterr().out)


def test_mode_b_first_activation_matches_golden(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader(
        [build_bundle(1, _v1_payloads(), sealed_at=SEALED_AT_V1)]
    )

    exit_code = mtd.main(["--slug", SLUG, "--to", "v1"], reader=reader)

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "FIRST ACTIVATION" in output
    assert_matches_golden("mode_b_first_activation.md", output)


def test_mode_c_legacy_comparison_is_byte_equivalent(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    legacy_path = tmp_path / "merchant_profiles.json"
    legacy_path.write_text(json.dumps(LEGACY_DOC), encoding="utf-8")
    reader = StubReader(
        [build_bundle(1, _v1_payloads(), sealed_at=SEALED_AT_V1)]
    )

    exit_code = mtd.main(
        ["--slug", SLUG, "--to", "v1", "--legacy", str(legacy_path)],
        reader=reader,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.count("BYTE-EQUIVALENT") == 5
    assert output.count("SKIPPED") == 2
    assert "DIFFERS" not in output
    # Stabilize the golden against tmp_path: only the basename is printed.
    assert_matches_golden("mode_c_legacy_comparison.md", output)


def test_all_sealed_review_packet_matches_golden(
    capsys: pytest.CaptureFixture[str],
) -> None:
    v1_manifest, v1_components = build_bundle(
        1, _v1_payloads(), sealed_at=SEALED_AT_V1
    )
    v2_manifest, v2_components = build_bundle(
        2, _v2_payloads(), sealed_at=SEALED_AT_V2
    )
    reader = StubReader(
        [(v1_manifest, v1_components), (v2_manifest, v2_components)],
        active_records=[make_active(SLUG, 1, v1_manifest.bundle_hash)],
    )

    exit_code = mtd.main(["--all-sealed"], reader=reader)

    assert exit_code == 0
    output = capsys.readouterr().out
    # v1 is covered by ACTIVE v1; only v2 is pending.
    assert "1 sealed version(s) awaiting" in output
    assert "UPGRADE: ACTIVE currently points at v1" in output
    assert_matches_golden("all_sealed_packet.md", output)


# ---------------------------------------------------------------------------
# Mode C: a legacy drift must render as a diff, not pass silently
# ---------------------------------------------------------------------------


def test_mode_c_reports_leaf_diff_on_legacy_drift(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    drifted = copy.deepcopy(LEGACY_DOC)
    drifted["profiles"]["Vons"]["typography"]["condense"] = 0.97
    legacy_path = tmp_path / "merchant_profiles.json"
    legacy_path.write_text(json.dumps(drifted), encoding="utf-8")
    reader = StubReader(
        [build_bundle(1, _v1_payloads(), sealed_at=SEALED_AT_V1)]
    )

    exit_code = mtd.main(
        ["--slug", SLUG, "--to", "v1", "--legacy", str(legacy_path)],
        reader=reader,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "| typography | full (profile-derived) | DIFFERS" in output
    assert "- typography.condense: 0.97 -> 0.93" in output


def test_mode_c_unknown_slug_fails_with_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    legacy_path = tmp_path / "merchant_profiles.json"
    legacy_path.write_text(json.dumps(LEGACY_DOC), encoding="utf-8")
    manifest, components = build_bundle(
        1, _v1_payloads(), slug="mystery_mart", sealed_at=SEALED_AT_V1
    )
    reader = StubReader([(manifest, components)])

    exit_code = mtd.main(
        [
            "--slug",
            "mystery_mart",
            "--to",
            "v1",
            "--legacy",
            str(legacy_path),
        ],
        reader=reader,
    )

    assert exit_code == 1
    assert "no legacy profile maps to slug" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Integrity + argument contract
# ---------------------------------------------------------------------------


def test_open_manifest_is_not_reviewable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader([build_bundle(1, _v1_payloads(), status="OPEN")])

    exit_code = mtd.main(["--slug", SLUG, "--to", "v1"], reader=reader)

    assert exit_code == 1
    assert "only SEALED versions are reviewable" in capsys.readouterr().err


def test_tampered_component_fails_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest, components = build_bundle(
        1, _v1_payloads(), sealed_at=SEALED_AT_V1
    )
    tampered_payloads = _v1_payloads()
    tampered_payloads["typography"]["typography"]["condense"] = 0.5
    tampered = [
        (
            MerchantTruthComponent(
                slug=SLUG,
                version=1,
                name=item.name,
                payload=tampered_payloads[item.name],
                provenance=item.provenance,
            )
            if item.name == "typography"
            else item
        )
        for item in components
    ]
    reader = StubReader([(manifest, tampered)])

    exit_code = mtd.main(["--slug", SLUG, "--to", "v1"], reader=reader)

    assert exit_code == 1
    assert "component hashes do not match" in capsys.readouterr().err


def test_missing_manifest_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader([])

    exit_code = mtd.main(["--slug", SLUG, "--to", "v3"], reader=reader)

    assert exit_code == 1
    assert "no manifest for vons v3" in capsys.readouterr().err


def test_prod_table_is_refused_before_any_read(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader([build_bundle(1, _v1_payloads())])

    exit_code = mtd.main(
        ["--slug", SLUG, "--to", "v1", "--table", "ReceiptsTable-d7ff76a"],
        reader=reader,
    )

    assert exit_code == 2
    assert reader.calls == 0
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err
    assert captured.out == ""


def test_prod_table_env_var_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DYNAMODB_TABLE_NAME", "ReceiptsTable-d7ff76a")
    reader = StubReader([])

    assert mtd.main(["--slug", SLUG, "--to", "v1"], reader=reader) == 2
    assert reader.calls == 0


def test_default_table_is_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DYNAMODB_TABLE_NAME", raising=False)
    assert mtd.resolve_table(None) == "ReceiptsTable-dc5be22"


@pytest.mark.parametrize(
    "argv",
    [
        ["--slug", SLUG],  # --to missing
        ["--to", "v1"],  # --slug missing
        ["--slug", SLUG, "--from", "v1", "--to", "v2", "--legacy", "x.json"],
        ["--all-sealed", "--slug", SLUG],
    ],
)
def test_invalid_argument_combinations_are_refused(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    reader = StubReader([])

    assert mtd.main(argv, reader=reader) == 2
    assert reader.calls == 0
    assert "REFUSED" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Unit: version parsing + paper-width formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("v1", 1), ("1", 1), ("V2", 2), ("v0000000042", 42)],
)
def test_parse_version_accepts_common_forms(text: str, expected: int) -> None:
    assert mtd.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "v", "v0", "0", "vv1", "v1.2", "one"])
def test_parse_version_rejects_invalid_forms(text: str) -> None:
    with pytest.raises(ValueError):
        mtd.parse_version(text)


def test_paper_width_move_formats_signed_fraction() -> None:
    assert mtd.format_paper_move(0.2479, 0.2510) == (
        "x 0.2479 -> 0.2510 (moved +0.0031 paper-width)"
    )
    assert mtd.format_paper_move(0.9439, 0.9502) == (
        "x 0.9439 -> 0.9502 (moved +0.0063 paper-width)"
    )
    assert mtd.format_paper_move(0.5, 0.4821) == (
        "x 0.5000 -> 0.4821 (moved -0.0179 paper-width)"
    )


def test_layout_diff_expresses_moves_in_paper_width_units() -> None:
    old = _v1_payloads()["layout"]
    new = _v2_payloads()["layout"]

    lines = mtd.diff_layout(old, new)

    assert (
        "- items: amount/right column x 0.9439 -> 0.9502 "
        "(moved +0.0063 paper-width)" in lines
    )
    assert any(
        line.startswith("- footer: desc/left column added at x 0.0171")
        for line in lines
    )
    assert any(
        line.startswith("- payment: label/left column removed at x 0.0063")
        for line in lines
    )
    assert "- template.measured.receipts: 12 -> 15" in lines


def test_catalog_diff_reports_added_removed_and_price_changes() -> None:
    old = _v1_payloads()["catalog_snapshot"]
    new = _v2_payloads()["catalog_snapshot"]

    lines = mtd.diff_catalog(old, new)

    assert '- price changed: dairy/MILK 2% "3.99" -> "4.19"' in lines
    assert '- item added: produce/ORANGES (price "3.49")' in lines
    assert '- item removed: produce/FUJI APPLES (price "2.49")' in lines


def test_identical_components_collapse_to_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reader = StubReader(
        [
            build_bundle(1, _v1_payloads(), sealed_at=SEALED_AT_V1),
            build_bundle(2, _v2_payloads(), sealed_at=SEALED_AT_V2),
        ]
    )

    assert (
        mtd.main(["--slug", SLUG, "--from", "v1", "--to", "v2"], reader=reader)
        == 0
    )
    output = capsys.readouterr().out
    for name in ("identity", "stylemap"):
        assert f"- {name}: identical (content_hash `" in output
    for name in (
        "typography",
        "layout",
        "assets",
        "flags",
        "catalog_snapshot",
    ):
        assert f"### {name} — CHANGED" in output
