#!/usr/bin/env python3
"""Measurement-driven v2 mint driver (Costco is the pilot invocation).

The v1 migration (``migrate_merchant_truth_v1.py``) minted a *parity* snapshot
of the legacy profile JSON. This driver mints the first **real** measurement
bundle on top of it, per contract section 7 (v3.1, schema-evolution):

    docs/architecture/MERCHANT_TRUTH_DYNAMO.md

It is merchant-generic; ``--merchant "Costco Wholesale"`` is the pilot. Per
component, following the section-7.7 measurement-mint provenance rules:

- ``layout``          MEASURED from a variant-layout payload file (W-G output,
                      ``scripts/build_variant_layout.py``): default variant plus
                      ``template.variants[]`` (section 7.2), with the clustering
                      measurement provenance.
- ``catalog_snapshot``MEASURED inline from the live ``MerchantCatalogItem``
                      partition (``{items[], item_count, catalog_hash, as_of}``,
                      the migration's ``_catalog_record`` shape). Empty partition
                      is WARNED in the dry-run, never hard-blocked (the owner runs
                      ``ingest_merchant_catalog --apply`` first).
- ``typography``      REBUILT from v1's values with the section-7.3 ``section_scale``
                      encoding (explicit-empty ``{}`` vs. absent). This is NOT a
                      carry: section 7.7's carry rule requires a payload hash match,
                      and re-encoding ``section_scale`` changes the hash, so carry is
                      structurally impossible. It mints as a fresh
                      ``measurement_pipeline`` component whose provenance records
                      ``pipeline = "v1-representation-migration"`` and the v1 source
                      hash under ``derived_from`` -- NOT the ``provenance_completeness
                      = legacy`` escape, which section 7.7 forbids on a re-measured
                      component. (Design call documented in the PR body.)
- ``identity`` / ``stylemap`` / ``assets`` / ``flags`` CARRIED forward from v1,
                      hash-preserving, reusing the SOURCE's provenance verbatim plus
                      the ``carried_forward_from`` marker (section 7.7). A carry that
                      stamps a fresh ``measured_at`` / ``source_receipt_keys`` is a
                      mint error (the carry-that-lies hole) and is refused here before
                      any write.

Seal path: mint the OPEN v2, resolve the layout-variant proposal by the payload's
verdict (section 7.2), run ``full_fidelity_eval`` against the exact v2 bundle
content with ``--write-gate-record`` (section 7.6), and seal THROUGH
``bridge_eval_to_gate_results`` (section 7.5). A ``FAIL`` raises
``GateBlockedError``: the version stays OPEN and the gate record is the work list
(a mintable-when-fixed state, not an error).

Because the loader refuses to resolve a non-SEALED bundle (chicken-and-egg: seal
needs the eval, the eval needs a resolvable bundle), the candidate is evaluated
through a **local SEALED fixture snapshot** written to a temp file and never to
DynamoDB. The gate record and the real seal both reference the same
content-derived ``bundle_hash``, so they stay consistent with the OPEN->SEALED
transition (sealing never changes component hashes).

DRY-RUN is the default: it assembles, prints the bundle summary + a
``merchant_truth_diff``-style v1->v2 preview + the empty-catalog warning, and
stages the exact live command. ``--live`` is dev-pinned with a loud banner; the
prod table is refused unconditionally (the migration's ``validate_live_table``).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
for _p in (
    os.path.join(_REPO, "receipt_dynamo"),
    os.path.dirname(__file__),  # sibling scripts (merchant_truth_diff, ...)
    _REPO,
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from receipt_dynamo.data.merchant_truth_gate_bridge import (  # noqa: E402
    GateBlockedError,
    bridge_eval_to_gate_results,
)
from receipt_dynamo.entities.merchant_truth import (  # noqa: E402
    COMPONENT_NAMES,
    MerchantTruthComponent,
    MerchantTruthManifest,
    MerchantTruthProposal,
    compute_bundle_hash,
    hash_payload,
)
from receipt_dynamo.migrations.merchant_truth_v1 import (  # noqa: E402
    DEV_TABLE_NAME,
    _catalog_record,
    slugify_merchant,
)
from receipt_dynamo.migrations.merchant_truth_v1_live import (  # noqa: E402
    PROD_TABLE_NAME,
    validate_live_table,
)

# The four components carried verbatim from v1; the other three are measured.
CARRIED_COMPONENTS = ("identity", "stylemap", "assets", "flags")
MEASURED_COMPONENTS = ("typography", "layout", "catalog_snapshot")
DEFAULT_MERCHANT = "Costco Wholesale"
DEFAULT_LAYOUT_PAYLOAD = os.path.join(
    _REPO,
    "tools",
    "glyph-studio",
    "fixtures",
    "costco_variant_layout.sample.json",
)
DEFAULT_PROPOSAL = "self-checkout-layout-variant"
# The prod-table marker: the same substring the eval's gate-write refusal uses
# (``full_fidelity_eval._GATE_PROD_MARKER``), so the prod refusal is a
# marker-substring check on EVERY path here (dry-run read and live write), not
# an exact-name match that a prod-suffixed variant could slip past.
_PROD_MARKER = PROD_TABLE_NAME.rsplit("-", 1)[-1]
DRIVER_NAME = "mint_merchant_truth_v2"
DRIVER_VERSION = "2"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class MintError(RuntimeError):
    """A pre-write assembly / governance violation caught by the driver."""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _refuse_prod(table: str) -> None:
    """Refuse the prod table on EVERY path by a marker-substring check.

    Prod promotion is a separate owner-gated step; the driver never reads or
    writes prod. This mirrors ``validate_live_table`` (exact prod name) but
    uses the same marker substring the eval's gate-write refusal does, so a
    prod-suffixed table variant is refused too -- identical semantics on the
    dry-run read path and the live write path.
    """
    if table == PROD_TABLE_NAME or _PROD_MARKER in table:
        raise SystemExit(
            f"refusing to target the prod table {table!r} (marker "
            f"{_PROD_MARKER!r}); prod is a separate owner-gated step and this "
            "refusal is unconditional on every path"
        )


def _git_sha(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_REPO)
            .decode()
            .strip()
        )
    except Exception:  # pragma: no cover - git absent
        return "unknown"


def _now(explicit: str | None) -> str:
    return explicit or datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# section 7.7 carry rule
# --------------------------------------------------------------------------


def carry_component(
    source: MerchantTruthComponent, target_version: int
) -> MerchantTruthComponent:
    """Carry one component to ``target_version``, hash-preserving.

    The payload is copied unchanged (so the content hash matches the source),
    and the provenance is the SOURCE's provenance verbatim plus a
    ``carried_forward_from`` marker naming the source version (section 7.7). No
    fresh ``measured_at`` / ``source_receipt_keys`` is asserted.
    """
    provenance = copy.deepcopy(source.provenance)
    provenance["carried_forward_from"] = f"v{source.version}"
    return MerchantTruthComponent(
        slug=source.slug,
        version=target_version,
        name=source.name,
        payload=copy.deepcopy(source.payload),
        provenance=provenance,
    )


def assert_carry_faithful(
    candidate: MerchantTruthComponent, source: MerchantTruthComponent
) -> None:
    """Refuse a carry that mutated the payload or asserted a fresh measurement.

    Section 7.7 carry rule, enforced by the driver (the data layer's
    ``_validate_component_governance`` does not check the carry marker). A
    faithful carry is exactly ``source.provenance`` plus ``carried_forward_from``
    naming a version, over a byte-identical payload. Anything else -- a payload
    edit, a fresh ``measured_at``, new ``source_receipt_keys`` -- is the
    carry-that-lies hole and raises :class:`MintError`.
    """
    if candidate.content_hash != source.content_hash:
        raise MintError(
            f"carry of {candidate.name!r} mutated the payload "
            f"({source.content_hash[:12]} -> {candidate.content_hash[:12]}); "
            "a carry must be hash-preserving (section 7.7)"
        )
    marker = candidate.provenance.get("carried_forward_from")
    expected_marker = f"v{source.version}"
    if marker != expected_marker:
        raise MintError(
            f"carry of {candidate.name!r} must name its exact source version "
            f"with carried_forward_from == {expected_marker!r}, not "
            f"{marker!r} (section 7.7)"
        )
    stripped = {
        key: value
        for key, value in candidate.provenance.items()
        if key != "carried_forward_from"
    }
    if stripped != source.provenance:
        raise MintError(
            f"carry of {candidate.name!r} does not reuse the source "
            "provenance verbatim: a carried component may not stamp a fresh "
            "measured_at / source_receipt_keys or otherwise edit provenance "
            "(section 7.7 carry-that-lies hole)"
        )


# --------------------------------------------------------------------------
# section 7.3 typography (rebuilt, not carried)
# --------------------------------------------------------------------------


def encode_section_scale(
    v1_typography_payload: dict[str, Any], *, explicit_empty: bool
) -> tuple[dict[str, Any], str]:
    """Apply the section-7.3 ``section_scale`` encoding to a v1 payload.

    Returns ``(new_payload, rule)`` where ``rule`` is one of
    ``"explicit_uniform"`` (key present ``{}``), ``"per_section"`` (key present,
    non-empty), or ``"absent_default"`` (key removed). ``explicit_empty`` comes
    from the renderer's v1 shim registry
    (``_V1_EXPLICIT_EMPTY_SECTION_SCALE``): merchants in it meant an EXPLICIT
    uniform scale, everyone else's collapsed ``{}`` meant "never measured ->
    default HEADER shrink" (absent).
    """
    payload = copy.deepcopy(v1_typography_payload)
    v1_scale = payload.get("section_scale") or {}
    if v1_scale:
        payload["section_scale"] = dict(v1_scale)
        return payload, "per_section"
    if explicit_empty:
        payload["section_scale"] = {}
        return payload, "explicit_uniform"
    payload.pop("section_scale", None)
    return payload, "absent_default"


def build_typography_component(
    slug: str,
    version: int,
    v1_typography: MerchantTruthComponent,
    *,
    explicit_empty: bool,
    corpus_source_receipt_keys: Sequence[str],
    git_sha: str,
    generated_at: str,
) -> tuple[MerchantTruthComponent, str]:
    """Rebuild the typography component with the section-7.3 encoding.

    Provenance is a fresh ``measurement_pipeline`` write (NOT the legacy
    migration escape, which section 7.7 forbids on a re-measured component):
    ``pipeline = "v1-representation-migration"``, ``measured_at`` = the mint
    time, ``source_receipt_keys`` = the measured corpus that backs this mint
    (the receipts the typography metrics derive from), and ``derived_from``
    records the v1 typography payload hash.
    """
    payload, rule = encode_section_scale(
        v1_typography.payload, explicit_empty=explicit_empty
    )
    provenance = {
        "source_kind": "measurement",
        "written_by": {
            "kind": "measurement_pipeline",
            "name": DRIVER_NAME,
            "version": DRIVER_VERSION,
        },
        "pipeline": "v1-representation-migration",
        "pipeline_version": "1",
        "git_sha": git_sha,
        "measured_at": generated_at,
        "source_object": f"merchant_truth:v{v1_typography.version}:typography",
        "source_receipt_keys": sorted(set(corpus_source_receipt_keys)),
        "derived_from": {
            "version": v1_typography.version,
            "component": "typography",
            "payload_hash": v1_typography.content_hash,
        },
        "representation_migration": {
            "contract": "section 7.3",
            "section_scale_rule": rule,
            "from_v1_shim": "_V1_EXPLICIT_EMPTY_SECTION_SCALE",
        },
    }
    component = MerchantTruthComponent(
        slug=slug,
        version=version,
        name="typography",
        payload=payload,
        provenance=provenance,
    )
    return component, rule


# --------------------------------------------------------------------------
# layout (measured from the W-G variant payload)
# --------------------------------------------------------------------------


def load_variant_payload(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    for key in ("template", "verdict", "provenance"):
        if key not in payload:
            raise MintError(
                f"variant-layout payload {path!r} missing {key!r}; expected "
                "the build_variant_layout.py output shape (template/verdict/"
                "provenance)"
            )
    return payload


def build_layout_component(
    slug: str,
    version: int,
    variant_payload: dict[str, Any],
    *,
    source_object: str,
    git_sha_fallback: str,
    generated_at_fallback: str,
) -> MerchantTruthComponent:
    """Mint the layout component from a variant-layout payload (section 7.2).

    Payload is the migration's ``{"available", "template"}`` shape; the
    template is the W-G template verbatim (default variant at the top level +
    ``template.variants[]``). Provenance is the payload's measurement block,
    augmented with the ``written_by`` identity and a ``source_object`` the
    data-layer governance requires.
    """
    template = copy.deepcopy(variant_payload["template"])
    payload = {"available": True, "template": template}

    measured = copy.deepcopy(variant_payload.get("provenance") or {})
    provenance: dict[str, Any] = {
        "source_kind": measured.get("source_kind", "measurement"),
        "written_by": {
            "kind": "measurement_pipeline",
            "name": measured.get("pipeline", "build_variant_layout"),
            "version": str(measured.get("pipeline_version", "1")),
        },
        "pipeline": measured.get("pipeline", "build_variant_layout"),
        "pipeline_version": str(measured.get("pipeline_version", "1")),
        "git_sha": measured.get("git_sha") or git_sha_fallback,
        "measured_at": measured.get("measured_at") or generated_at_fallback,
        "source_object": source_object,
        "source_receipt_keys": list(measured.get("source_receipt_keys") or []),
    }
    # Preserve the clustering evidence + the resolved proposal verdict verbatim.
    for key in (
        "excluded_receipt_keys",
        "params",
        "scan_manifest",
        "failed_receipt_keys",
    ):
        if key in measured:
            provenance[key] = measured[key]
    provenance["verdict"] = copy.deepcopy(variant_payload.get("verdict") or {})

    return MerchantTruthComponent(
        slug=slug,
        version=version,
        name="layout",
        payload=payload,
        provenance=provenance,
    )


# --------------------------------------------------------------------------
# catalog_snapshot (measured inline from the live partition)
# --------------------------------------------------------------------------


def build_catalog_snapshot(
    catalog_items: Sequence[Any], *, generated_at: str
) -> tuple[dict[str, Any], list[str]]:
    """Build the ``catalog_snapshot`` payload + the union of receipt keys.

    Mirrors the v1 migration's ``_catalog_record`` shape exactly: decode each
    entity's low-level item, strip key/index attrs, sort by
    ``(category, product_text, price)``, and hash the sorted list.
    """
    records = sorted(
        (_catalog_record(item.to_item()) for item in catalog_items),
        key=lambda record: (
            str(record.get("category", "")),
            str(record.get("product_text", "")),
            str(record.get("price", "")),
        ),
    )
    receipt_keys: set[str] = set()
    for record in records:
        receipt_keys.update(record.get("source_receipt_keys") or [])
    snapshot = {
        "items": records,
        "item_count": len(records),
        "catalog_hash": hash_payload(records),
        "as_of": generated_at,
    }
    return snapshot, sorted(receipt_keys)


def build_catalog_component(
    slug: str,
    version: int,
    catalog_items: Sequence[Any],
    *,
    git_sha: str,
    generated_at: str,
) -> tuple[MerchantTruthComponent, dict[str, Any]]:
    """Mint the catalog_snapshot component from live rows (section 7.7).

    Returns ``(component, info)``; ``info`` carries ``item_count`` and whether
    the partition was empty so the caller can warn.
    """
    snapshot, receipt_keys = build_catalog_snapshot(
        catalog_items, generated_at=generated_at
    )
    provenance = {
        "source_kind": "measurement",
        "written_by": {
            "kind": "measurement_pipeline",
            "name": "ingest_merchant_catalog",
            "version": "1",
        },
        "pipeline": "ingest_merchant_catalog",
        "pipeline_version": "1",
        "git_sha": git_sha,
        "measured_at": generated_at,
        "source_object": f"MERCHANT_CATALOG#{slug}",
        "source_receipt_keys": receipt_keys,
        "item_count": snapshot["item_count"],
        "catalog_hash": snapshot["catalog_hash"],
    }
    component = MerchantTruthComponent(
        slug=slug,
        version=version,
        name="catalog_snapshot",
        payload=snapshot,
        provenance=provenance,
    )
    info = {
        "item_count": snapshot["item_count"],
        "empty": snapshot["item_count"] == 0,
    }
    return component, info


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def _explicit_empty_registry() -> frozenset[str]:
    """The renderer's v1 shim registry, the section-7.3 source of truth."""
    try:
        from render_synthetic_receipts import (  # noqa: WPS433
            _V1_EXPLICIT_EMPTY_SECTION_SCALE,
        )

        return frozenset(_V1_EXPLICIT_EMPTY_SECTION_SCALE)
    except Exception:  # pragma: no cover - renderer deps absent
        # Vendored fallback (kept in sync with render_synthetic_receipts).
        return frozenset({"in_n_out_burger", "sprouts_farmers_market"})


def assemble_v2_components(
    slug: str,
    version: int,
    v1_components: dict[str, MerchantTruthComponent],
    variant_payload: dict[str, Any],
    catalog_items: Sequence[Any],
    *,
    explicit_empty: bool,
    layout_source_object: str,
    git_sha: str,
    generated_at: str,
) -> tuple[list[MerchantTruthComponent], dict[str, Any]]:
    """Assemble all 7 v2 components; enforce the section-7.7 carry rule.

    Returns ``(components, info)`` where ``info`` records the section-7.3
    typography rule, the layout verdict, and the catalog empty/count.
    """
    missing = COMPONENT_NAMES - set(v1_components)
    if missing:
        raise MintError(
            f"v1 bundle for {slug!r} is missing components {sorted(missing)}; "
            "cannot carry forward"
        )

    components: dict[str, MerchantTruthComponent] = {}

    # Carried, hash-preserving.
    for name in CARRIED_COMPONENTS:
        carried = carry_component(v1_components[name], version)
        assert_carry_faithful(carried, v1_components[name])
        components[name] = carried

    # Measured: layout first (its corpus feeds the typography provenance).
    layout = build_layout_component(
        slug,
        version,
        variant_payload,
        source_object=layout_source_object,
        git_sha_fallback=git_sha,
        generated_at_fallback=generated_at,
    )
    components["layout"] = layout
    corpus = layout.provenance.get("source_receipt_keys") or []

    typography, section_scale_rule = build_typography_component(
        slug,
        version,
        v1_components["typography"],
        explicit_empty=explicit_empty,
        corpus_source_receipt_keys=corpus,
        git_sha=git_sha,
        generated_at=generated_at,
    )
    components["typography"] = typography

    catalog, catalog_info = build_catalog_component(
        slug,
        version,
        catalog_items,
        git_sha=git_sha,
        generated_at=generated_at,
    )
    components["catalog_snapshot"] = catalog

    ordered = [components[name] for name in sorted(COMPONENT_NAMES)]
    info = {
        "section_scale_rule": section_scale_rule,
        "verdict": variant_payload.get("verdict") or {},
        "catalog": catalog_info,
    }
    return ordered, info


def manifest_provenance(
    slug: str,
    run_id: str,
    *,
    git_sha: str,
    generated_at: str,
    source_bundle_hash: str,
    source_version: int,
) -> dict[str, Any]:
    return {
        "written_by": {
            "kind": "measurement_pipeline",
            "name": DRIVER_NAME,
            "version": DRIVER_VERSION,
        },
        "minted_at": generated_at,
        "run_id": run_id,
        "git_sha": git_sha,
        "source_bundle": {
            "version": source_version,
            "bundle_hash": source_bundle_hash,
        },
        "measured_components": list(MEASURED_COMPONENTS),
        "carried_components": list(CARRIED_COMPONENTS),
    }


def bundle_hash_of(components: Sequence[MerchantTruthComponent]) -> str:
    return compute_bundle_hash(
        {component.name: component.content_hash for component in components}
    )


# --------------------------------------------------------------------------
# Read v1 + preview
# --------------------------------------------------------------------------


def read_v1_bundle(
    client: Any, slug: str, *, version: int | None
) -> tuple[MerchantTruthManifest, dict[str, MerchantTruthComponent]]:
    """Read the SEALED v1 (or an explicit version) to carry forward from."""
    if version is None:
        version = client.get_latest_merchant_truth_version(
            slug, sealed_only=True
        )
        if version is None:
            raise MintError(
                f"no SEALED version exists for {slug!r}; mint v1 first"
            )
    manifest = client.get_merchant_truth_manifest(
        slug, version, consistent_read=True
    )
    if manifest is None:
        raise MintError(f"no manifest for {slug!r} v{version}")
    if manifest.status != "SEALED":
        raise MintError(
            f"{slug!r} v{version} is {manifest.status}; carry-from requires a "
            "SEALED source"
        )
    components = {
        component.name: component
        for component in client.list_merchant_truth_components(
            slug, version, consistent_read=True
        )
    }
    return manifest, components


def render_preview(
    v1_manifest: MerchantTruthManifest,
    v1_components: dict[str, MerchantTruthComponent],
    v2_manifest: MerchantTruthManifest,
    v2_components: Sequence[MerchantTruthComponent],
    *,
    table: str,
) -> str:
    """A merchant_truth_diff-style v1->v2 preview (before any seal)."""
    from merchant_truth_diff import Bundle, render_version_diff  # noqa: WPS433

    old = Bundle(manifest=v1_manifest, components=dict(v1_components))
    new = Bundle(
        manifest=v2_manifest,
        components={component.name: component for component in v2_components},
    )
    return "\n".join(render_version_diff(old, new, table=table))


# --------------------------------------------------------------------------
# Proposal resolution (section 7.2 verdict)
# --------------------------------------------------------------------------


def resolve_layout_proposal(
    client: Any,
    table: str,
    slug: str,
    *,
    verdict: dict[str, Any],
    version: int,
    proposal_claim: str,
) -> str:
    """Resolve the layout-variant proposal by the clustering verdict.

    ``REFUTE`` -> ``resolve_proposal(..., "refuted")``; ``CONFIRM`` ->
    ``"confirmed"``. Idempotent: an already-resolved proposal (not OPEN) is
    skipped, and an absent proposal is reported. Returns a short status string.
    """
    status = str(verdict.get("status", "")).upper()
    resolution = {"REFUTE": "refuted", "CONFIRM": "confirmed"}.get(status)
    if resolution is None:
        return f"skipped (verdict status {status!r} is not CONFIRM/REFUTE)"

    proposals = client.list_merchant_truth_proposals(slug)
    match = next(
        (p for p in proposals if p.claim_slug == proposal_claim), None
    )
    if match is None:
        return f"no proposal {proposal_claim!r} found (nothing to resolve)"
    if match.status != "OPEN":
        return (
            f"already resolved ({match.status}); idempotent skip "
            f"[{proposal_claim}]"
        )
    client.resolve_proposal(match, version, resolution, table)
    refs = verdict.get("clusters") or []
    ref_count = sum(len(cluster.get("receipt_refs") or []) for cluster in refs)
    return (
        f"{resolution} (resolved_by_version=v{version}, "
        f"{ref_count} cluster receipt_refs recorded in the verdict)"
    )


# --------------------------------------------------------------------------
# Eval + gate + seal (section 7.5 / 7.6)
# --------------------------------------------------------------------------


def _fixture_items(
    components: Sequence[MerchantTruthComponent], *, slug: str, version: int
) -> list[dict[str, Any]]:
    """Low-level items for a SEALED eval fixture (never written to DynamoDB)."""
    component_hashes = {c.name: c.content_hash for c in components}
    manifest = MerchantTruthManifest(
        slug=slug,
        version=version,
        component_hashes=component_hashes,
        bundle_hash=compute_bundle_hash(component_hashes),
        status="SEALED",
        provenance={"written_by": {"kind": "measurement_pipeline"}},
        mint_run_id=f"eval-fixture-{slug}-v{version}",
        gate_status="PASS",
        gate_results={"status": "PASS", "passed": True},
    )
    return [manifest.to_item(), *[c.to_item() for c in components]]


def default_eval_runner(
    *,
    components: Sequence[MerchantTruthComponent],
    slug: str,
    version: int,
    merchant: str,
    image_id: str,
    receipt_id: int,
    table: str,
    out_root: str,
    allow_dirty: bool,
) -> dict[str, Any]:
    """Run ``full_fidelity_eval`` against the candidate bundle content.

    Writes the candidate to a temp SEALED fixture (chicken-and-egg: the loader
    only resolves SEALED bundles), shells out to the eval with
    ``--write-gate-record`` so the eval persists one MERCHANT_TRUTH_GATE record
    (dev-pinned, prod refused inside the eval), then parses and returns this
    run's ``checks``.
    """
    with tempfile.TemporaryDirectory() as work:
        fixture_path = os.path.join(work, f"{slug}_v{version}.fixture.json")
        with open(fixture_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "items": _fixture_items(
                        components, slug=slug, version=version
                    )
                },
                handle,
            )
        eval_out = os.path.join(work, "eval")
        cmd = [
            sys.executable,
            os.path.join(_REPO, "synthesis_loop", "full_fidelity_eval.py"),
            "run",
            merchant,
            image_id,
            str(receipt_id),
            slug,
            "--out-root",
            eval_out,
            "--truth-fixture",
            fixture_path,
            "--write-gate-record",
        ]
        if allow_dirty:
            cmd.append("--allow-dirty")
        env = dict(os.environ)
        env["DYNAMODB_TABLE_NAME"] = table
        proc = subprocess.run(cmd, env=env, cwd=_REPO)
        checks_path = os.path.join(
            eval_out, f"{slug}_eval", f"{slug}.checks.json"
        )
        if not os.path.exists(checks_path):
            raise MintError(
                f"eval produced no checks at {checks_path!r} "
                f"(exit {proc.returncode}); cannot gate the seal"
            )
        with open(checks_path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
        return doc.get("checks", doc)


def eval_gate_and_seal(
    client: Any,
    table: str,
    slug: str,
    version: int,
    components: Sequence[MerchantTruthComponent],
    *,
    eval_runner: Callable[..., dict[str, Any]],
    confirmed_proposals: Sequence[str],
    generated_at: str,
    actor: str = "mint_merchant_truth_v2",
    eval_kwargs: dict[str, Any] | None = None,
) -> tuple[MerchantTruthManifest | None, dict[str, Any]]:
    """Eval -> bridge -> seal. Returns ``(sealed_manifest | None, checks)``.

    On a ``FAIL`` overall the bridge raises :class:`GateBlockedError`; the seal
    is not attempted (version stays OPEN) and the derived gate_results (the work
    list) are printed. Returns ``(None, checks)`` in that case.
    """
    checks = eval_runner(
        components=components,
        slug=slug,
        version=version,
        table=table,
        **(eval_kwargs or {}),
    )
    try:
        gate_results = bridge_eval_to_gate_results(checks)
    except GateBlockedError as blocked:
        print(_format_gate_worklist(slug, version, blocked.gate_results))
        return None, checks
    sealed = client.seal_version(
        slug,
        version,
        gate_results,
        list(confirmed_proposals),
        table,
        sealed_at=generated_at,
        actor=actor,
    )
    overall = gate_results.get("overall")
    gap_count = len(gate_results.get("gaps") or [])
    print(
        f"SEALED {slug} v{version} (overall={overall}, gaps={gap_count}); "
        "flip to ACTIVE stays owner-gated."
    )
    return sealed, checks


def _format_gate_worklist(
    slug: str, version: int, gate_results: dict[str, Any]
) -> str:
    lines = [
        "",
        "=" * 72,
        f"GATE FAILED: {slug} v{version} stays OPEN (mintable-when-fixed).",
        "The MERCHANT_TRUTH_GATE record is the work list:",
    ]
    for gap in gate_results.get("gaps") or []:
        lines.append(
            f"  - {gap.get('metric')}: {gap.get('verdict')} "
            f"{json.dumps(gap.get('detail', {}), sort_keys=True)}"
        )
    lines.append("=" * 72)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Dry-run / live orchestration
# --------------------------------------------------------------------------


def _bundle_summary(
    slug: str,
    version: int,
    components: Sequence[MerchantTruthComponent],
    info: dict[str, Any],
    *,
    bundle_hash: str,
) -> str:
    lines = [
        "=" * 72,
        f"ASSEMBLED CANDIDATE: {slug} v{version}",
        f"  bundle_hash: {bundle_hash}",
        f"  section_scale rule (7.3): {info['section_scale_rule']}",
        f"  layout verdict (7.2): {info['verdict'].get('status', 'n/a')} "
        f"-- {info['verdict'].get('proposal', 'n/a')}",
        f"  catalog items: {info['catalog']['item_count']}"
        + ("  [EMPTY]" if info["catalog"]["empty"] else ""),
        "  components:",
    ]
    for component in components:
        prov = component.provenance
        carried = prov.get("carried_forward_from")
        kind = (prov.get("written_by") or {}).get("kind")
        tag = f"carried<-{carried}" if carried else f"measured({kind})"
        lines.append(
            f"    - {component.name:16s} {component.content_hash[:12]}  {tag}"
        )
    lines.append("=" * 72)
    return "\n".join(lines)


def _empty_catalog_warning(slug: str) -> str:
    return "\n".join(
        [
            "!" * 72,
            f"WARNING: the MerchantCatalogItem partition for {slug!r} is EMPTY.",
            "  catalog_snapshot will inline zero items. A LIVE mint will then be",
            "  refused by measurement provenance governance (an empty",
            "  source_kind=measurement carries no source_receipt_keys).",
            "  Populate it first:",
            f"    PORTFOLIO_ENV=dev python scripts/ingest_merchant_catalog.py "
            f"--merchant '<name>' --apply",
            "!" * 72,
        ]
    )


def _staged_live_command(args, slug: str) -> str:
    parts = [
        "PORTFOLIO_ENV=dev python scripts/mint_merchant_truth_v2.py",
        f"--merchant '{args.merchant}'",
        f"--layout-payload {args.layout_payload}",
        f"--eval-image-id <IMAGE_ID> --eval-receipt-id <RECEIPT_ID>",
        "--live",
    ]
    if args.slug:
        parts.append(f"--slug {slug}")
    return "  " + " \\\n    ".join(parts)


def run(args, *, client_factory=None, eval_runner=None) -> int:
    from receipt_dynamo.data.dynamo_client import DynamoClient

    slug = args.slug or slugify_merchant(args.merchant)
    table = args.table_name or DEV_TABLE_NAME
    git_sha = _git_sha(args.git_sha)
    generated_at = _now(args.generated_at)
    run_id = f"merchant-truth-v2-{slug}-{git_sha[:12]}"

    # Prod is refused identically on every path (dry-run read + live write).
    _refuse_prod(table)
    if args.live:
        validate_live_table(table, explicit=args.table_name is not None)

    make_client = client_factory or (lambda: DynamoClient(table))
    client = make_client()

    # --- read v1, allocate version -------------------------------------
    v1_manifest, v1_components = read_v1_bundle(
        client, slug, version=args.from_version
    )
    version = client.next_mint_version(slug)

    variant_payload = load_variant_payload(args.layout_payload)
    catalog_items = client.list_merchant_catalog_items(args.merchant)
    explicit_empty = slug in _explicit_empty_registry()

    components, info = assemble_v2_components(
        slug,
        version,
        v1_components,
        variant_payload,
        catalog_items,
        explicit_empty=explicit_empty,
        layout_source_object=(
            os.path.relpath(args.layout_payload, _REPO)
            if args.layout_payload.startswith(_REPO)
            else args.layout_payload
        ),
        git_sha=git_sha,
        generated_at=generated_at,
    )
    bundle_hash = bundle_hash_of(components)

    # --- preview (both modes) ------------------------------------------
    candidate_manifest = MerchantTruthManifest(
        slug=slug,
        version=version,
        component_hashes={c.name: c.content_hash for c in components},
        bundle_hash=bundle_hash,
        status="OPEN",
        provenance=manifest_provenance(
            slug,
            run_id,
            git_sha=git_sha,
            generated_at=generated_at,
            source_bundle_hash=v1_manifest.bundle_hash,
            source_version=v1_manifest.version,
        ),
        mint_run_id=run_id,
    )
    print(
        _bundle_summary(
            slug, version, components, info, bundle_hash=bundle_hash
        )
    )
    print()
    print(
        render_preview(
            v1_manifest,
            v1_components,
            candidate_manifest,
            components,
            table=table,
        )
    )
    if info["catalog"]["empty"]:
        print()
        print(_empty_catalog_warning(slug))

    if not args.live:
        print()
        print("DRY RUN — no DynamoDB writes. Stage the live mint with:")
        print(_staged_live_command(args, slug))
        return 0

    # --- LIVE ceremony -------------------------------------------------
    if args.eval_image_id is None or args.eval_receipt_id is None:
        raise SystemExit(
            "--live requires --eval-image-id and --eval-receipt-id "
            "(the receipt the fidelity eval renders against)"
        )
    print()
    print(_v2_banner(table, slug, version, git_sha))

    existing = client.get_merchant_truth_manifest(
        slug, version, consistent_read=True
    )
    if existing is not None and existing.status == "SEALED":
        print(f"SKIP: {slug} v{version} already SEALED (idempotent).")
        return 0

    minted = client.mint_version(
        slug,
        version,
        components,
        candidate_manifest.provenance,
        run_id,
        table,
        created_at=generated_at,
    )
    print(f"MINTED OPEN {slug} v{version} bundle_hash={minted.bundle_hash}")

    proposal_status = resolve_layout_proposal(
        client,
        table,
        slug,
        verdict=info["verdict"],
        version=version,
        proposal_claim=args.proposal,
    )
    print(f"PROPOSAL {args.proposal}: {proposal_status}")

    runner = eval_runner or default_eval_runner
    confirmed = (
        [args.proposal]
        if str(info["verdict"].get("status", "")).upper() == "CONFIRM"
        else []
    )
    sealed, _checks = eval_gate_and_seal(
        client,
        table,
        slug,
        version,
        components,
        eval_runner=runner,
        confirmed_proposals=confirmed,
        generated_at=generated_at,
        eval_kwargs={
            "merchant": args.merchant,
            "image_id": args.eval_image_id,
            "receipt_id": args.eval_receipt_id,
            "out_root": args.eval_out_root,
            "allow_dirty": args.allow_dirty,
        },
    )
    if sealed is None:
        return 3  # documented mintable-when-fixed state; not a crash
    return 0


def _v2_banner(table: str, slug: str, version: int, git_sha: str) -> str:
    bar = "=" * 72
    return "\n".join(
        [
            bar,
            "LIVE MERCHANT-TRUTH V2 MINT — DynamoDB WRITES WILL OCCUR",
            f"  table:     {table}",
            f"  merchant:  {slug} (mint OPEN v{version} + eval-gated seal)",
            f"  git SHA:   {git_sha}",
            bar,
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merchant", default=DEFAULT_MERCHANT)
    parser.add_argument(
        "--slug", default=None, help="override the slugified merchant name"
    )
    parser.add_argument(
        "--layout-payload",
        default=DEFAULT_LAYOUT_PAYLOAD,
        help="variant-layout payload JSON (build_variant_layout.py output)",
    )
    parser.add_argument(
        "--from-version",
        type=int,
        default=None,
        help="explicit v1 source version (default: latest SEALED)",
    )
    parser.add_argument(
        "--proposal",
        default=DEFAULT_PROPOSAL,
        help="layout-variant proposal claim_slug to resolve by the verdict",
    )
    parser.add_argument(
        "--table",
        "--table-name",
        dest="table_name",
        default=None,
        help=(
            f"DynamoDB table (default dev {DEV_TABLE_NAME!r}); the prod table "
            "is refused unconditionally"
        ),
    )
    parser.add_argument("--git-sha", default=None)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--eval-image-id", default=None)
    parser.add_argument("--eval-receipt-id", type=int, default=None)
    parser.add_argument("--eval-out-root", default=".out")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--live",
        action="store_true",
        help="perform the mint+seal ceremony (default: dry-run, no writes)",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
