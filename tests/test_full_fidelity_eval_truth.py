"""W8: full_fidelity_eval reads MerchantTruth bundles, never the profile JSON.

Covers the four contract points of the cutover:

1. PINNED freshness gate -- a pin that does not match the realized bundle
   fails the eval.
2. ONLINE-ACTIVE freshness gate -- the expected ACTIVE tuple is captured
   before the load; a loader that realizes a different bundle (stale cache /
   racing flip) fails the eval.
3. FIXTURE mode -- a vendored bundle resolves with no reader at all
   (CI/offline), still hash-verified, and refuses a fixture for the wrong
   merchant.
4. Metric parity -- identical inputs produce byte-identical eval output
   whether truth comes from the vendored costco v1 bundle or from the same
   data read the legacy way (merchant_profiles.json), proving the cutover
   changed the data source, not the metrics.
"""

import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (
    os.path.join(REPO, "synthesis_loop"),
    os.path.join(REPO, "scripts"),
    os.path.join(REPO, "receipt_agent"),
    os.path.join(REPO, "receipt_dynamo"),
    os.path.join(REPO, "tools", "glyph-studio", "py"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import full_fidelity_eval as ffe  # noqa: E402

from receipt_dynamo.data.shared_exceptions import (  # noqa: E402
    MerchantTruthIntegrityError,
)
from receipt_dynamo.entities.merchant_truth import (  # noqa: E402
    COMPONENT_NAMES,
    MerchantTruthActive,
    MerchantTruthComponent,
    MerchantTruthManifest,
    compute_bundle_hash,
)

COSTCO_FIXTURE = os.path.join(
    REPO, "tests", "fixtures", "merchant_truth", "costco_wholesale_v1.json"
)
SLUG = "costco_wholesale"
NOW = "2026-07-21T00:00:00+00:00"


# ---------------------------------------------------------------------------
# bundle scaffolding (mirrors receipt_dynamo's loader test helpers)
# ---------------------------------------------------------------------------
def build_bundle(version: int, marker: str = ""):
    """A SEALED/PASS bundle whose hashes vary with ``version``+``marker``."""
    components = [
        MerchantTruthComponent(
            slug=SLUG,
            version=version,
            name=name,
            payload={"name": name, "version": version, "marker": marker},
            provenance={"source_kind": "migration"},
        )
        for name in sorted(COMPONENT_NAMES)
    ]
    hashes = {item.name: item.content_hash for item in components}
    bundle_hash = compute_bundle_hash(hashes)
    manifest = MerchantTruthManifest(
        slug=SLUG,
        version=version,
        component_hashes=hashes,
        bundle_hash=bundle_hash,
        status="SEALED",
        provenance={"written_by": "test"},
        mint_run_id=f"run-{version}",
        gate_status="PASS",
    )
    active = MerchantTruthActive(
        slug=SLUG,
        version=version,
        bundle_hash=bundle_hash,
        normalized_aliases=["costco", "costco wholesale"],
        activated_at=NOW,
        activated_by="owner",
    )
    items = [manifest.to_item(), *[c.to_item() for c in components]]
    return active, bundle_hash, items


class FakeReader:
    """Read-only reader double; ``active_queue`` scripts successive
    strong reads of TRUTH#ACTIVE (capture first, loader's read second)."""

    def __init__(self, fleet, bundles, active_queue=None):
        self.fleet = fleet
        self.bundles = bundles  # {(slug, version): items}
        self.active_queue = list(active_queue) if active_queue else None

    def list_active_merchant_truth(self):
        return list(self.fleet)

    def list_merchant_truth_manifests(self):
        return []

    def get_merchant_truth_manifest(
        self, slug, version, *, consistent_read=False
    ):
        return None

    def list_merchant_truth_components(
        self, slug, version, *, consistent_read=False
    ):
        return []

    def get_active_merchant_truth(self, slug, *, consistent_read=False):
        if self.active_queue:
            return self.active_queue.pop(0)
        for active in self.fleet:
            if active.slug == slug:
                return active
        return None

    def read_merchant_truth_bundle_items(
        self, slug, version, *, consistent_read=False
    ):
        return list(self.bundles.get((slug, version), []))


# ---------------------------------------------------------------------------
# 1. PINNED mode is the freshness gate
# ---------------------------------------------------------------------------
def test_pinned_match_resolves(tmp_path):
    active, bundle_hash, items = build_bundle(1)
    reader = FakeReader([active], {(SLUG, 1): items})
    ctx = ffe.resolve_truth(
        "Costco Wholesale",
        pin_version=1,
        pin_bundle_hash=bundle_hash,
        reader=reader,
        cache_dir=tmp_path,
    )
    assert (ctx.slug, ctx.version, ctx.bundle_hash) == (SLUG, 1, bundle_hash)
    assert ctx.mode == "pinned"


def test_pinned_mismatch_fails(tmp_path):
    active, _bundle_hash, items = build_bundle(1)
    reader = FakeReader([active], {(SLUG, 1): items})
    with pytest.raises(MerchantTruthIntegrityError):
        ffe.resolve_truth(
            "Costco Wholesale",
            pin_version=1,
            pin_bundle_hash="0" * 64,
            reader=reader,
            cache_dir=tmp_path,
        )


def test_pinned_missing_version_fails(tmp_path):
    active, bundle_hash, items = build_bundle(1)
    reader = FakeReader([active], {(SLUG, 1): items})
    with pytest.raises(MerchantTruthIntegrityError):
        ffe.resolve_truth(
            "Costco Wholesale",
            pin_version=7,
            pin_bundle_hash=bundle_hash,
            reader=reader,
            cache_dir=tmp_path,
        )


def test_half_a_pin_is_rejected(tmp_path):
    active, _bundle_hash, items = build_bundle(1)
    reader = FakeReader([active], {(SLUG, 1): items})
    with pytest.raises(SystemExit):
        ffe.resolve_truth(
            "Costco Wholesale",
            pin_version=1,
            reader=reader,
            cache_dir=tmp_path,
        )


# ---------------------------------------------------------------------------
# 2. ONLINE-ACTIVE freshness gate (stale-cache detection)
# ---------------------------------------------------------------------------
def test_online_active_fresh_resolves(tmp_path):
    active, bundle_hash, items = build_bundle(1)
    reader = FakeReader([active], {(SLUG, 1): items})
    ctx = ffe.resolve_truth(
        "Costco Wholesale", reader=reader, cache_dir=tmp_path
    )
    assert (ctx.version, ctx.bundle_hash) == (1, bundle_hash)
    assert ctx.mode == "online-active"
    assert (ctx.expected_version, ctx.expected_bundle_hash) == (
        1,
        bundle_hash,
    )


def test_online_active_stale_bundle_fails(tmp_path):
    """Expected tuple captured at run start = v2; the loader then realizes
    v1 (a stale replica / racing flip). The eval must FAIL, not stamp v1."""
    active_v1, _hash_v1, items_v1 = build_bundle(1)
    active_v2, _hash_v2, items_v2 = build_bundle(2)
    reader = FakeReader(
        [active_v2],
        {(SLUG, 1): items_v1, (SLUG, 2): items_v2},
        # capture strong-reads v2; the loader's own strong read then sees v1
        active_queue=[active_v2, active_v1],
    )
    with pytest.raises(ffe.TruthFreshnessError):
        ffe.resolve_truth(
            "Costco Wholesale", reader=reader, cache_dir=tmp_path
        )


def test_unknown_merchant_fails(tmp_path):
    active, _bundle_hash, items = build_bundle(1)
    reader = FakeReader([active], {(SLUG, 1): items})
    with pytest.raises(MerchantTruthIntegrityError):
        ffe.resolve_truth(
            "No Such Merchant", reader=reader, cache_dir=tmp_path
        )


# ---------------------------------------------------------------------------
# 3. FIXTURE mode: offline, hash-verified, merchant-checked
# ---------------------------------------------------------------------------
def test_fixture_mode_resolves_offline(tmp_path):
    ctx = ffe.resolve_truth(
        "Costco Wholesale",
        fixture_path=COSTCO_FIXTURE,
        reader=None,  # no network surface at all
        cache_dir=tmp_path,
    )
    with open(COSTCO_FIXTURE, encoding="utf-8") as fh:
        items = json.load(fh)["items"]
    manifest = next(
        MerchantTruthManifest.from_item(item)
        for item in items
        if item["TYPE"]["S"] == "MERCHANT_TRUTH_MANIFEST"
    )
    assert (ctx.slug, ctx.version, ctx.bundle_hash) == (
        manifest.slug,
        manifest.version,
        manifest.bundle_hash,
    )
    assert ctx.mode == "fixture"
    layout = ctx.component("layout")
    assert layout["available"] and layout["template"]["columns"]


def test_fixture_for_wrong_merchant_fails(tmp_path):
    with pytest.raises(SystemExit):
        ffe.resolve_truth(
            "Vons",
            fixture_path=COSTCO_FIXTURE,
            reader=None,
            cache_dir=tmp_path,
        )


def test_fixture_and_pin_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        ffe.resolve_truth(
            "Costco Wholesale",
            fixture_path=COSTCO_FIXTURE,
            pin_version=1,
            pin_bundle_hash="0" * 64,
            reader=None,
            cache_dir=tmp_path,
        )


def test_tampered_fixture_fails(tmp_path):
    with open(COSTCO_FIXTURE, encoding="utf-8") as fh:
        doc = json.load(fh)
    for item in doc["items"]:
        if item["SK"]["S"].endswith("#C#layout"):
            item["payload"]["S"] = item["payload"]["S"].replace(
                "0.9439", "0.5000"
            )
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises((MerchantTruthIntegrityError, ValueError)):
        ffe.resolve_truth(
            "Costco Wholesale",
            fixture_path=str(tampered),
            reader=None,
            cache_dir=tmp_path,
        )


# ---------------------------------------------------------------------------
# 4. metric parity: legacy profile JSON vs loader bundle, identical output
# ---------------------------------------------------------------------------
class _LegacyArtifact:
    """Stub artifact whose components are built straight from the legacy
    merchant_profiles.json block, mapped exactly as migration mapped them."""

    def __init__(self, slug, components):
        self.slug = slug
        self.version = 0
        self.bundle_hash = "legacy"
        self.components = components


def _legacy_truth_context():
    with open(
        os.path.join(REPO, "scripts", "merchant_profiles.json"),
        encoding="utf-8",
    ) as fh:
        block = json.load(fh)["profiles"]["Costco Wholesale"]
    typography = block.get("typography") or {}
    template = dict(block.get("layout_template") or {})
    template.pop("_comment", None)
    components = {
        "layout": {"available": bool(template), "template": template},
        "assets": {
            "profile": {
                "logo": block.get("logo"),
                "logo_anchor": block.get("logo_anchor"),
                "stylemap_filename": typography.get("stylemap"),
            }
        },
        "typography": {
            "typography": {
                key: typography[key]
                for key in ("bitmap_font", "bitmap_thin", "condense")
                if key in typography
            },
            "section_scale": block.get("section_scale") or {},
        },
        "flags": {"compose": block.get("compose")},
    }
    return ffe.TruthContext(
        artifact=_LegacyArtifact(SLUG, components),
        expected_version=0,
        expected_bundle_hash="legacy",
        mode="legacy",
    )


def _synthetic_pair(tmp_path):
    """A small deterministic (real, synth) pair with an items amount lane
    near the costco template's items amount column (x=0.9439)."""
    from PIL import Image

    W, H = 400, 300
    real = np.full((H, W), 235, dtype=np.uint8)
    syn = np.full((H, W), 235, dtype=np.uint8)

    def word(text, l, t, r, b, labels=()):
        # renderer-format bbox: 0-1000, y-up
        return {
            "text": text,
            "labels": list(labels),
            "bbox": [
                l / W * 1000,
                (1 - b / H) * 1000,
                r / W * 1000,
                (1 - t / H) * 1000,
            ],
        }

    words = []
    lane_r = int(0.9439 * W)
    for i in range(6):
        y = 40 + i * 22  # inside the costco ITEMS band (0.09-0.55)
        words.append(word("ITEM%d" % i, 12, y, 90, y + 12))
        words.append(word("2.99", lane_r - 34, y, lane_r, y + 12))
        for img in (real, syn):
            img[y : y + 12, 12:90] = 30
            img[y : y + 12, lane_r - 34 : lane_r] = 30
    y = 200  # SUMMARY band
    words.append(word("TOTAL", 12, y, 60, y + 12))
    words.append(word("17.94", lane_r - 40, y, lane_r, y + 12))
    for img in (real, syn):
        img[y : y + 12, 12:60] = 30
        img[y : y + 12, lane_r - 40 : lane_r] = 30

    real_img = Image.fromarray(real).convert("RGB")
    syn_img = Image.fromarray(syn).convert("RGB")
    real_png = str(tmp_path / "real.png")
    syn_png = str(tmp_path / "syn.png")
    real_img.save(real_png)
    syn_img.save(syn_png)
    return real_img, syn_img, words, real_png, syn_png


def test_metric_parity_legacy_vs_loader(tmp_path):
    """Identical inputs + identical truth data => byte-identical eval
    output, whether truth flows from the vendored costco v1 bundle or from
    the legacy profile JSON. Proves the cutover is a data-source change."""
    loader_ctx = ffe.resolve_truth(
        "Costco Wholesale",
        fixture_path=COSTCO_FIXTURE,
        reader=None,
        cache_dir=tmp_path,
    )
    legacy_ctx = _legacy_truth_context()

    # the truth-derived eval inputs must agree exactly
    for section in ("items", "summary", "payment", "footer"):
        assert ffe.profile_columns(loader_ctx, section) == ffe.profile_columns(
            legacy_ctx, section
        )
    assert bool(
        (loader_ctx.component("assets").get("profile") or {}).get("logo")
    ) == bool(
        (legacy_ctx.component("assets").get("profile") or {}).get("logo")
    )
    assert bool(loader_ctx.component("flags").get("compose")) == bool(
        legacy_ctx.component("flags").get("compose")
    )
    assert ffe.atlas_hash(loader_ctx) == ffe.atlas_hash(legacy_ctx)

    real_img, syn_img, words, real_png, syn_png = _synthetic_pair(tmp_path)
    results = []
    for ctx in (legacy_ctx, loader_ctx):
        checks = ffe.evaluate_pair(
            real_img,
            syn_img,
            words,
            words,
            slug="costco",
            truth=ctx,
            real_png=real_png,
            syn_png=syn_png,
            composed=False,
            columns_source="profile",
        )
        results.append(json.dumps(checks, sort_keys=True))
    assert results[0] == results[1]
    # the profile-columns source actually engaged (not the bootstrap path)
    checks = json.loads(results[1])
    assert any(
        band["source"] == "profile"
        for band in checks["columns"]["bands"].values()
    )
