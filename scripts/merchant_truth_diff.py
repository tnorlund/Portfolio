#!/usr/bin/env python3
"""Flip-review artifact for versioned merchant truth (W5, #1188 P4).

Three read-only modes over SEALED merchant-truth bundles, plus a fleet
sweep:

  Mode A (version diff):
      merchant_truth_diff.py --slug vons --from v1 --to v2
      Semantic per-component diff of two sealed versions. Components with
      identical content_hash collapse to one line. Layout diffs express
      column moves in paper-width units (the layout payload's column ``x``
      values are paper-width fractions); typography/flags/stylemap diff at
      leaf level (dotted paths, old -> new); assets diff per artifact
      (hash + pointer); catalog snapshots diff items added/removed/
      price-changed. Provenance deltas (pipeline, git SHA, measured_at)
      are shown per changed component.

  Mode B (first activation review):
      merchant_truth_diff.py --slug costco_wholesale --to v1
      An activation decision document: component hashes + payload sizes,
      key measured values, gate status + gate provenance, bundle_hash,
      and what the flip will do. This is what the owner reads before the
      FIRST ACTIVE flip (owner gate G2).

  Mode C (legacy comparison):
      merchant_truth_diff.py --slug costco_wholesale --to v1 \
          --legacy scripts/merchant_profiles.json
      Diff the minted bundle against the legacy profile source per the
      migration crosswalk. G1 bundles were minted from that file, so the
      expected result is byte-equivalence on every profile-derived
      component; any deviation renders as a Mode-A style diff.

  --all-sealed:
      Emit a Mode B summary for every SEALED-but-not-ACTIVE version in
      the table — the owner's G2 review packet in one command.

Read-only always: this tool holds no writer surface and never flips.
Env/table convention matches synthesis_loop/fleet_status.py: default dev
table ReceiptsTable-dc5be22 via DYNAMODB_TABLE_NAME; the prod table
(ReceiptsTable-d7ff76a) is refused unconditionally.

Exit codes: 0 rendered; 1 data/integrity error; 2 refused configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (os.path.join(REPO, "receipt_dynamo"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from receipt_dynamo.entities.merchant_truth import (  # noqa: E402
    COMPONENT_NAMES,
    canonical_json_bytes,
    compute_bundle_hash,
    hash_payload,
)
from receipt_dynamo.merchant_truth_loader import (  # noqa: E402
    normalize_merchant_alias,
)
from receipt_dynamo.migrations.merchant_truth_v1 import (  # noqa: E402
    DECIDED_TYPOGRAPHY_FIELDS,
    MEASURED_TYPOGRAPHY_FIELDS,
    slugify_merchant,
)

if TYPE_CHECKING:
    from receipt_dynamo.entities.merchant_truth import (
        MerchantTruthActive,
        MerchantTruthComponent,
        MerchantTruthManifest,
    )
    from receipt_dynamo.merchant_truth_loader import MerchantTruthReader

DEV_TABLE_NAME = "ReceiptsTable-dc5be22"
PROD_TABLE_NAME = "ReceiptsTable-d7ff76a"
PROD_TABLE_MARKER = "d7ff76a"
SHORT_HASH_LEN = 12

# Fixed review order: identity first, decided flags and catalog last.
COMPONENT_ORDER = (
    "identity",
    "typography",
    "stylemap",
    "layout",
    "assets",
    "flags",
    "catalog_snapshot",
)
assert set(COMPONENT_ORDER) == COMPONENT_NAMES

# Flag groups copied verbatim into C#flags by the v1 migration writer.
PROFILE_FLAG_GROUPS = (
    "header",
    "graphics",
    "compose",
    "logo_subtitle",
    "logo_reserve_subtitle",
)
# Profile keys copied into the assets component's "profile" sub-object.
PROFILE_ASSET_KEYS = ("logo", "logo_anchor")


class DiffError(Exception):
    """A data or integrity problem that must stop the review render."""


def resolve_table(cli_table: str | None) -> str:
    """Resolve the target table and refuse prod explicitly."""
    table = (
        cli_table or os.environ.get("DYNAMODB_TABLE_NAME") or DEV_TABLE_NAME
    )
    if table == PROD_TABLE_NAME or PROD_TABLE_MARKER in table:
        raise ValueError(
            f"merchant_truth_diff never reads prod; refusing table "
            f"{table!r} (prod = {PROD_TABLE_NAME!r})"
        )
    return table


def parse_version(text: str) -> int:
    """Accept ``v1``, ``1``, or the padded ``v0000000001`` key form."""
    match = re.fullmatch(r"v?(\d+)", text.strip().lower())
    if match is None:
        raise ValueError(f"invalid version {text!r}; expected e.g. v1")
    version = int(match.group(1))
    if version < 1:
        raise ValueError(f"invalid version {text!r}; versions start at v1")
    return version


def short_hash(digest: str) -> str:
    return digest[:SHORT_HASH_LEN]


def format_value(value: Any) -> str:
    """Render one leaf value compactly and deterministically."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def format_paper_x(value: float) -> str:
    """Render a column x position (a paper-width fraction) at 4 decimals."""
    return f"{value:.4f}"


def format_paper_move(old_x: float, new_x: float) -> str:
    """Render a column move in paper-width units.

    Layout column ``x`` values are fractions of the paper width, so the
    signed delta IS the move expressed in paper-width units.
    """
    delta = new_x - old_x
    return (
        f"x {format_paper_x(old_x)} -> {format_paper_x(new_x)} "
        f"(moved {delta:+.4f} paper-width)"
    )


def flatten_leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten to dotted leaf paths; list elements use ``[i]``."""
    leaves: dict[str, Any] = {}
    if isinstance(value, dict):
        if not value:
            leaves[prefix or "(root)"] = {}
            return leaves
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            leaves.update(flatten_leaves(value[key], path))
        return leaves
    if isinstance(value, list):
        if not value:
            leaves[prefix or "(root)"] = []
            return leaves
        for index, item in enumerate(value):
            leaves.update(flatten_leaves(item, f"{prefix}[{index}]"))
        return leaves
    leaves[prefix or "(root)"] = value
    return leaves


def diff_leaves(old: Any, new: Any, *, prefix: str = "") -> list[str]:
    """Leaf-level value diff: added / removed / changed dotted paths."""
    old_leaves = flatten_leaves(old, prefix)
    new_leaves = flatten_leaves(new, prefix)
    lines: list[str] = []
    for path in sorted(set(old_leaves) | set(new_leaves)):
        if path not in new_leaves:
            lines.append(
                f"- {path} (removed): {format_value(old_leaves[path])}"
            )
        elif path not in old_leaves:
            lines.append(f"- {path} (added): {format_value(new_leaves[path])}")
        elif old_leaves[path] != new_leaves[path]:
            lines.append(
                f"- {path}: {format_value(old_leaves[path])} -> "
                f"{format_value(new_leaves[path])}"
            )
    return lines


# --------------------------------------------------------------------------
# Bundle loading + integrity
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Bundle:
    """A verified SEALED version: manifest plus its component payloads."""

    manifest: "MerchantTruthManifest"
    components: dict[str, "MerchantTruthComponent"]

    def payload(self, name: str) -> Any:
        return self.components[name].payload

    def payload_size(self, name: str) -> int:
        return len(canonical_json_bytes(self.components[name].payload))


def load_bundle(
    reader: "MerchantTruthReader", slug: str, version: int
) -> Bundle:
    """Read one version and fail closed on any integrity mismatch."""
    manifest = reader.get_merchant_truth_manifest(slug, version)
    if manifest is None:
        raise DiffError(f"no manifest for {slug} v{version}")
    if manifest.status != "SEALED":
        raise DiffError(
            f"{slug} v{version} is {manifest.status}; only SEALED "
            "versions are reviewable"
        )
    components = {
        item.name: item
        for item in reader.list_merchant_truth_components(slug, version)
    }
    if set(components) != COMPONENT_NAMES:
        raise DiffError(
            f"{slug} v{version} components do not match the contract: "
            f"loaded {sorted(components)}"
        )
    actual_hashes = {
        name: components[name].content_hash for name in sorted(components)
    }
    if actual_hashes != manifest.component_hashes:
        raise DiffError(
            f"{slug} v{version} component hashes do not match the manifest"
        )
    if compute_bundle_hash(actual_hashes) != manifest.bundle_hash:
        raise DiffError(f"{slug} v{version} bundle hash verification failed")
    return Bundle(manifest=manifest, components=components)


# --------------------------------------------------------------------------
# Mode A: semantic per-component version diff
# --------------------------------------------------------------------------


def _column_key(column: dict[str, Any]) -> tuple[str, str]:
    return (str(column.get("role")), str(column.get("anchor")))


def _describe_column(column: dict[str, Any]) -> str:
    extras = ", ".join(
        f"{key} {format_value(column[key])}"
        for key in sorted(column)
        if key not in {"role", "anchor", "x"}
    )
    suffix = f" ({extras})" if extras else ""
    return f"at x {format_paper_x(float(column.get('x', 0.0)))}{suffix}"


def diff_layout(old_payload: Any, new_payload: Any) -> list[str]:
    """Layout diff: column moves in paper-width units + leaf changes."""
    lines: list[str] = []
    old_payload = old_payload if isinstance(old_payload, dict) else {}
    new_payload = new_payload if isinstance(new_payload, dict) else {}
    if old_payload.get("available") != new_payload.get("available"):
        lines.append(
            f"- available: {format_value(old_payload.get('available'))} -> "
            f"{format_value(new_payload.get('available'))}"
        )
    old_template = old_payload.get("template") or {}
    new_template = new_payload.get("template") or {}
    old_columns = old_template.get("columns") or {}
    new_columns = new_template.get("columns") or {}
    for section in sorted(set(old_columns) | set(new_columns)):
        lines.extend(
            _diff_section_columns(
                section,
                old_columns.get(section) or [],
                new_columns.get(section) or [],
            )
        )
    # §7.2 variants[] descend semantically (added/removed variants + per-variant
    # column moves in paper-width units); everything else diffs as leaves. Both
    # `columns` and `variants` are pulled out of the leaf diff so they are not
    # double-reported. A variant-blind template (no `variants`) yields no
    # variant lines, so v1 bundles diff byte-identically to before.
    lines.extend(
        _diff_variants(
            old_template.get("variants") or [],
            new_template.get("variants") or [],
        )
    )
    old_rest = {
        k: v
        for k, v in old_template.items()
        if k not in {"columns", "variants"}
    }
    new_rest = {
        k: v
        for k, v in new_template.items()
        if k not in {"columns", "variants"}
    }
    lines.extend(diff_leaves(old_rest, new_rest, prefix="template"))
    return lines


def _variants_by_id(
    variants: list[Any],
) -> dict[str, dict[str, Any]]:
    """Index variant entries by ``variant_id`` (skip malformed entries)."""
    keyed: dict[str, dict[str, Any]] = {}
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict):
            continue
        vid = str(variant.get("variant_id", f"variant[{index}]"))
        keyed[vid] = variant
    return keyed


def _diff_variants(
    old_variants: list[Any], new_variants: list[Any]
) -> list[str]:
    """Per-variant sub-diff over ``template.variants[]`` (§7.2).

    Variants are paired by ``variant_id``: an id only on one side is an
    added/removed variant; a shared id descends into per-section column moves
    (paper-width units) plus a leaf diff of the variant's other keys
    (``classifier_hint`` / ``support`` / ``source_receipt_keys`` / sections /
    separators).
    """
    lines: list[str] = []
    old_keyed = _variants_by_id(old_variants)
    new_keyed = _variants_by_id(new_variants)
    for vid in sorted(set(old_keyed) | set(new_keyed)):
        prefix = f"variant[{vid}]"
        if vid not in new_keyed:
            old_variant = old_keyed[vid]
            lines.append(
                f"- {prefix} removed "
                f"(support {format_value(old_variant.get('support'))})"
            )
            continue
        if vid not in old_keyed:
            new_variant = new_keyed[vid]
            lines.append(
                f"- {prefix} added "
                f"(support {format_value(new_variant.get('support'))})"
            )
            continue
        old_variant, new_variant = old_keyed[vid], new_keyed[vid]
        old_columns = old_variant.get("columns") or {}
        new_columns = new_variant.get("columns") or {}
        for section in sorted(set(old_columns) | set(new_columns)):
            lines.extend(
                _diff_section_columns(
                    f"{prefix} {section}",
                    old_columns.get(section) or [],
                    new_columns.get(section) or [],
                )
            )
        old_rest = {k: v for k, v in old_variant.items() if k != "columns"}
        new_rest = {k: v for k, v in new_variant.items() if k != "columns"}
        lines.extend(diff_leaves(old_rest, new_rest, prefix=prefix))
    return lines


def _diff_section_columns(
    section: str,
    old_cols: list[dict[str, Any]],
    new_cols: list[dict[str, Any]],
) -> list[str]:
    """Pair columns by (role, anchor) in x order; report moves/add/remove."""
    lines: list[str] = []
    keys = sorted({_column_key(col) for col in [*old_cols, *new_cols]})
    for key in keys:
        role, anchor = key
        olds = sorted(
            (c for c in old_cols if _column_key(c) == key),
            key=lambda c: float(c.get("x", 0.0)),
        )
        news = sorted(
            (c for c in new_cols if _column_key(c) == key),
            key=lambda c: float(c.get("x", 0.0)),
        )
        label = f"{section}: {role}/{anchor} column"
        for old_col, new_col in zip(olds, news):
            old_x = float(old_col.get("x", 0.0))
            new_x = float(new_col.get("x", 0.0))
            if old_x != new_x:
                lines.append(f"- {label} {format_paper_move(old_x, new_x)}")
            for field_name in sorted(
                (set(old_col) | set(new_col)) - {"x", "role", "anchor"}
            ):
                if old_col.get(field_name) != new_col.get(field_name):
                    lines.append(
                        f"- {label} {field_name}: "
                        f"{format_value(old_col.get(field_name))} -> "
                        f"{format_value(new_col.get(field_name))}"
                    )
        for new_col in news[len(olds) :]:
            lines.append(f"- {label} added {_describe_column(new_col)}")
        for old_col in olds[len(news) :]:
            lines.append(f"- {label} removed {_describe_column(old_col)}")
    return lines


def _artifact_fields(artifact: Any) -> dict[str, Any]:
    return artifact if isinstance(artifact, dict) else {"value": artifact}


def _diff_artifact(name: str, old: Any, new: Any) -> list[str]:
    """Per-artifact diff: hash + pointer first, then remaining fields."""
    lines: list[str] = []
    old_fields = _artifact_fields(old)
    new_fields = _artifact_fields(new)
    if old is None and new is not None:
        lines.append(f"- {name} added: {format_value(new)}")
        return lines
    if old is not None and new is None:
        lines.append(f"- {name} removed: {format_value(old)}")
        return lines
    for field_name, label in (
        ("content_hash", "hash"),
        ("s3_key", "pointer"),
    ):
        if old_fields.get(field_name) != new_fields.get(field_name):
            lines.append(
                f"- {name} {label}: "
                f"{format_value(old_fields.get(field_name))} -> "
                f"{format_value(new_fields.get(field_name))}"
            )
    rest_old = {
        k: v
        for k, v in old_fields.items()
        if k not in {"content_hash", "s3_key"}
    }
    rest_new = {
        k: v
        for k, v in new_fields.items()
        if k not in {"content_hash", "s3_key"}
    }
    lines.extend(diff_leaves(rest_old, rest_new, prefix=name))
    return lines


def diff_assets(old_payload: Any, new_payload: Any) -> list[str]:
    """Asset diff: per font face, logo, and the profile sub-object."""
    lines: list[str] = []
    old_payload = old_payload if isinstance(old_payload, dict) else {}
    new_payload = new_payload if isinstance(new_payload, dict) else {}
    old_fonts = old_payload.get("fonts") or {}
    new_fonts = new_payload.get("fonts") or {}
    for face in sorted(set(old_fonts) | set(new_fonts)):
        lines.extend(
            _diff_artifact(
                f"font[{face}]", old_fonts.get(face), new_fonts.get(face)
            )
        )
    lines.extend(
        _diff_artifact(
            "logo", old_payload.get("logo"), new_payload.get("logo")
        )
    )
    lines.extend(
        diff_leaves(
            old_payload.get("profile") or {},
            new_payload.get("profile") or {},
            prefix="profile",
        )
    )
    if old_payload.get("missing_merchant_font") != new_payload.get(
        "missing_merchant_font"
    ):
        lines.append(
            "- missing_merchant_font: "
            f"{format_value(old_payload.get('missing_merchant_font'))} -> "
            f"{format_value(new_payload.get('missing_merchant_font'))}"
        )
    return lines


def _catalog_items_by_key(
    payload: Any,
) -> dict[tuple[str, str, int], dict[str, Any]]:
    items = (payload or {}).get("items") or []
    keyed: dict[tuple[str, str, int], dict[str, Any]] = {}
    occurrence: dict[tuple[str, str], int] = {}
    for item in items:
        base = (
            str(item.get("category", "")),
            str(item.get("product_text", "")),
        )
        index = occurrence.get(base, 0)
        occurrence[base] = index + 1
        keyed[(*base, index)] = item
    return keyed


def _catalog_label(key: tuple[str, str, int]) -> str:
    category, product_text, index = key
    suffix = f" #{index + 1}" if index else ""
    prefix = f"{category}/" if category else ""
    return f"{prefix}{product_text}{suffix}"


def diff_catalog(old_payload: Any, new_payload: Any) -> list[str]:
    """Catalog diff: items added/removed/price-changed + summary fields."""
    lines: list[str] = []
    old_items = _catalog_items_by_key(old_payload)
    new_items = _catalog_items_by_key(new_payload)
    for key in sorted(set(old_items) | set(new_items)):
        label = _catalog_label(key)
        if key not in new_items:
            lines.append(
                f"- item removed: {label} "
                f"(price {format_value(old_items[key].get('price'))})"
            )
            continue
        if key not in old_items:
            lines.append(
                f"- item added: {label} "
                f"(price {format_value(new_items[key].get('price'))})"
            )
            continue
        old_item, new_item = old_items[key], new_items[key]
        if old_item.get("price") != new_item.get("price"):
            lines.append(
                f"- price changed: {label} "
                f"{format_value(old_item.get('price'))} -> "
                f"{format_value(new_item.get('price'))}"
            )
        other = diff_leaves(
            {k: v for k, v in old_item.items() if k != "price"},
            {k: v for k, v in new_item.items() if k != "price"},
            prefix=f"item[{label}]",
        )
        lines.extend(other)
    for field_name in ("item_count", "catalog_hash", "as_of"):
        old_value = (old_payload or {}).get(field_name)
        new_value = (new_payload or {}).get(field_name)
        if old_value != new_value:
            lines.append(
                f"- {field_name}: {format_value(old_value)} -> "
                f"{format_value(new_value)}"
            )
    return lines


def diff_component_payload(name: str, old: Any, new: Any) -> list[str]:
    """Dispatch to the semantic differ for one component."""
    if name == "layout":
        return diff_layout(old, new)
    if name == "assets":
        return diff_assets(old, new)
    if name == "catalog_snapshot":
        return diff_catalog(old, new)
    # identity, typography, stylemap, flags: leaf-level value diff.
    return diff_leaves(old, new)


def provenance_delta_lines(
    old_prov: dict[str, Any], new_prov: dict[str, Any]
) -> list[str]:
    """Pipeline / git SHA / measured_at deltas for a changed component."""
    lines: list[str] = []
    old_pipeline = (
        f"{old_prov.get('pipeline')}@{old_prov.get('pipeline_version')}"
    )
    new_pipeline = (
        f"{new_prov.get('pipeline')}@{new_prov.get('pipeline_version')}"
    )
    for label, old_value, new_value in (
        ("pipeline", old_pipeline, new_pipeline),
        ("git_sha", old_prov.get("git_sha"), new_prov.get("git_sha")),
        (
            "measured_at",
            old_prov.get("measured_at"),
            new_prov.get("measured_at"),
        ),
    ):
        if old_value != new_value:
            lines.append(
                f"  provenance {label}: {format_value(old_value)} -> "
                f"{format_value(new_value)}"
            )
        else:
            lines.append(
                f"  provenance {label}: {format_value(new_value)} "
                "(unchanged)"
            )
    return lines


def render_version_diff(old: Bundle, new: Bundle, *, table: str) -> list[str]:
    """Mode A: the reviewed semantic diff between two sealed versions."""
    slug = new.manifest.slug
    lines = [
        f"# Merchant-truth diff: {slug} "
        f"v{old.manifest.version} -> v{new.manifest.version}",
        "",
        f"Table: `{table}`",
        "",
        f"- bundle_hash: `{old.manifest.bundle_hash}` ->",
        f"  `{new.manifest.bundle_hash}`",
        f"- gate: {old.manifest.gate_status} -> {new.manifest.gate_status}"
        f" | sealed_at: {old.manifest.sealed_at} -> "
        f"{new.manifest.sealed_at}",
    ]
    identical = [
        name
        for name in COMPONENT_ORDER
        if old.components[name].content_hash
        == new.components[name].content_hash
    ]
    changed = [name for name in COMPONENT_ORDER if name not in set(identical)]
    lines += ["", "## Components", ""]
    for name in identical:
        lines.append(
            f"- {name}: identical "
            f"(content_hash `{short_hash(old.components[name].content_hash)}`)"
        )
    for name in changed:
        old_component = old.components[name]
        new_component = new.components[name]
        lines += [
            "",
            f"### {name} — CHANGED "
            f"(`{short_hash(old_component.content_hash)}` -> "
            f"`{short_hash(new_component.content_hash)}`)",
            "",
        ]
        body = diff_component_payload(
            name, old_component.payload, new_component.payload
        )
        if body:
            lines.extend(body)
        else:
            # Hash changed but leaf diff is empty: only possible when
            # canonicalization-invisible details moved; still show it.
            lines.append(
                "- (payload leaf diff is empty despite a hash change)"
            )
        lines.extend(
            provenance_delta_lines(
                old_component.provenance, new_component.provenance
            )
        )
    if not changed:
        lines += ["", "No component content changed between the versions."]
    return lines


# --------------------------------------------------------------------------
# Mode B: first-activation summary (the G2 decision document)
# --------------------------------------------------------------------------


def _numeric_leaf_lines(payload: Any) -> list[str]:
    return [
        f"- {path}: {format_value(value)}"
        for path, value in sorted(flatten_leaves(payload).items())
    ]


def _layout_summary_lines(payload: Any) -> list[str]:
    payload = payload if isinstance(payload, dict) else {}
    lines = [f"- available: {format_value(payload.get('available'))}"]
    template = payload.get("template") or {}
    measured = template.get("measured") or {}
    if measured:
        lines.append(
            f"- measured: {format_value(measured.get('receipts'))} receipts"
            f" (tool {measured.get('tool_git_sha')}, "
            f"dirty {format_value(measured.get('tool_dirty'))})"
        )
    columns = template.get("columns") or {}
    if columns:
        per_section = ", ".join(
            f"{section}={len(columns[section])}" for section in sorted(columns)
        )
        lines.append(f"- columns per section: {per_section}")
    return lines


def _artifact_summary(name: str, artifact: Any) -> str:
    if not isinstance(artifact, dict):
        return f"- {name}: {format_value(artifact)}"
    pointer = artifact.get("s3_key") or artifact.get("local_path")
    digest = artifact.get("content_hash")
    digest_text = f"`{short_hash(digest)}`" if digest else "(no hash)"
    return f"- {name}: {pointer} hash {digest_text}"


def _assets_summary_lines(payload: Any) -> list[str]:
    payload = payload if isinstance(payload, dict) else {}
    lines: list[str] = []
    fonts = payload.get("fonts") or {}
    for face in sorted(fonts):
        lines.append(_artifact_summary(f"font[{face}]", fonts[face]))
    logo = payload.get("logo")
    if logo is not None:
        lines.append(_artifact_summary("logo", logo))
    else:
        lines.append("- logo: none published")
    profile = payload.get("profile") or {}
    if profile:
        lines.append(
            "- profile assets: "
            + ", ".join(
                f"{key}={format_value(profile[key])}"
                for key in sorted(profile)
            )
        )
    if payload.get("missing_merchant_font"):
        lines.append("- WARNING: missing MerchantFont row for this merchant")
    return lines


def _stylemap_summary_lines(payload: Any) -> list[str]:
    payload = payload if isinstance(payload, dict) else {}
    lines = [f"- available: {format_value(payload.get('available'))}"]
    source = payload.get("source")
    if source:
        lines.append(_artifact_summary("source", source))
    document = payload.get("document")
    if isinstance(document, dict):
        sections = document.get("sections")
        if isinstance(sections, (dict, list)):
            lines.append(f"- document sections: {len(sections)}")
    return lines


def _catalog_summary_lines(payload: Any) -> list[str]:
    payload = payload if isinstance(payload, dict) else {}
    catalog_hash = payload.get("catalog_hash") or ""
    return [
        f"- items: {format_value(payload.get('item_count'))} "
        f"(catalog_hash `{short_hash(catalog_hash)}`, "
        f"as_of {payload.get('as_of')})"
    ]


def _identity_summary_lines(payload: Any) -> list[str]:
    payload = payload if isinstance(payload, dict) else {}
    aliases = payload.get("normalized_aliases") or []
    return [
        f"- merchant_name: {format_value(payload.get('merchant_name'))}",
        f"- slug: {payload.get('slug')} "
        f"(upper {payload.get('upper_slug')})",
        f"- normalized aliases: {len(aliases)}",
    ]


def _flags_summary_lines(payload: Any) -> list[str]:
    payload = payload if isinstance(payload, dict) else {}
    leaves = flatten_leaves(payload)
    groups = ", ".join(sorted(payload)) if payload else "(none)"
    return [
        f"- {len(leaves)} decided leaves across groups: {groups}",
        "- flags are git-sourced engine config "
        "(truth = measured; flags = decided)",
    ]


def _gate_lines(manifest: "MerchantTruthManifest") -> list[str]:
    gate = manifest.gate_results or {}
    lines = [
        f"- gate_status: {manifest.gate_status}"
        + (
            f" — {gate.get('gate')} ({gate.get('kind')})"
            if gate.get("gate")
            else ""
        )
    ]
    if gate.get("note"):
        lines.append(f"  - note: {gate['note']}")
    evidence = gate.get("evidence") or {}
    if evidence:
        detail = ", ".join(
            f"{key}={format_value(evidence[key])}" for key in sorted(evidence)
        )
        lines.append(f"  - evidence: {detail}")
    written_by = gate.get("written_by") or {}
    if written_by:
        lines.append(
            f"  - gate written_by: {written_by.get('kind')}/"
            f"{written_by.get('name')}@{written_by.get('version')}"
        )
    return lines


def render_activation_summary(
    bundle: Bundle,
    active: "MerchantTruthActive | None",
    *,
    table: str,
) -> list[str]:
    """Mode B: the decision document read before an ACTIVE flip."""
    manifest = bundle.manifest
    slug = manifest.slug
    lines = [
        f"# Merchant-truth activation review: {slug} v{manifest.version}",
        "",
        f"Table: `{table}`",
        "",
    ]
    if active is None:
        lines.append(
            "FIRST ACTIVATION: no ACTIVE pointer exists for this merchant. "
            "Flipping creates the initial pointer (conditional Put; "
            "owner gate G2)."
        )
    elif active.version >= manifest.version:
        lines.append(
            f"NOTE: ACTIVE already points at v{active.version} "
            f"(`{short_hash(active.bundle_hash)}`); this version is not "
            "pending activation."
        )
    else:
        lines.append(
            f"UPGRADE: ACTIVE currently points at v{active.version} "
            f"(`{short_hash(active.bundle_hash)}`); flipping moves it to "
            f"v{manifest.version}."
        )
    provenance = manifest.provenance or {}
    written_by = provenance.get("written_by") or {}
    lines += [
        "",
        "## Decision summary",
        "",
        f"- bundle_hash: `{manifest.bundle_hash}`",
        f"- manifest: {manifest.status}, sealed_at {manifest.sealed_at}",
        *_gate_lines(manifest),
        f"- mint: run `{manifest.mint_run_id}`, written_by "
        f"{written_by.get('kind')}/{written_by.get('name')}"
        f"@{written_by.get('version')}, git_sha "
        f"{format_value(provenance.get('git_sha'))}",
    ]
    blockers = provenance.get("migration_blockers") or []
    if blockers:
        lines.append(f"- migration blockers at mint: {format_value(blockers)}")
    lines += [
        "",
        "## Components",
        "",
        "| component | content_hash | payload bytes |",
        "|---|---|---|",
    ]
    for name in COMPONENT_ORDER:
        component = bundle.components[name]
        lines.append(
            f"| {name} | `{short_hash(component.content_hash)}` "
            f"| {bundle.payload_size(name)} |"
        )
    lines += ["", "## Key measured values", ""]
    sections: list[tuple[str, list[str]]] = [
        ("identity", _identity_summary_lines(bundle.payload("identity"))),
        ("typography", _numeric_leaf_lines(bundle.payload("typography"))),
        ("layout", _layout_summary_lines(bundle.payload("layout"))),
        ("assets", _assets_summary_lines(bundle.payload("assets"))),
        ("stylemap", _stylemap_summary_lines(bundle.payload("stylemap"))),
        (
            "catalog_snapshot",
            _catalog_summary_lines(bundle.payload("catalog_snapshot")),
        ),
        ("flags", _flags_summary_lines(bundle.payload("flags"))),
    ]
    for title, body in sections:
        lines.append(f"### {title}")
        lines.append("")
        lines.extend(body)
        lines.append("")
    lines += [
        "## Flip",
        "",
        "This tool never flips. The owner runs the flip "
        "(DynamoClient.initial_activate for a first activation, "
        "flip_active thereafter) after reading this review — owner "
        "gate G2.",
    ]
    return lines


# --------------------------------------------------------------------------
# Mode C: legacy profile comparison (crosswalk sanity)
# --------------------------------------------------------------------------

LEGACY_SKIP_REASONS = {
    "stylemap": "sourced from MerchantFont/S3 stylemap object, "
    "not merchant_profiles.json",
    "catalog_snapshot": "sourced from the MERCHANT_CATALOG partition, "
    "not merchant_profiles.json",
}


def _select_keys(
    source: dict[str, Any], names: Iterable[str]
) -> dict[str, Any]:
    allowed = set(names)
    return {key: value for key, value in source.items() if key in allowed}


def build_legacy_expectations(
    document: dict[str, Any], slug: str
) -> tuple[str, dict[str, Any]]:
    """Rebuild the profile-derived component payloads for one merchant.

    Mirrors the v1 migration writer's profile-derived construction
    (``merchant_truth_v1._build_merchant_payload``) using the same
    imported measured/decided field sets, so byte-equivalence here means
    the sealed bundle faithfully carries the legacy source per the
    crosswalk.
    """
    profiles = document.get("profiles")
    if not isinstance(profiles, dict):
        raise DiffError("legacy document must contain a profiles map")
    merchant_name = next(
        (name for name in profiles if slugify_merchant(name) == slug), None
    )
    if merchant_name is None:
        raise DiffError(f"no legacy profile maps to slug {slug!r}")
    profile = profiles[merchant_name]
    aliases = [merchant_name, *profile.get("aliases", [])]
    identity = {
        "merchant_name": merchant_name,
        "slug": slug,
        "aliases": profile.get("aliases", []),
        "upper_slug": slug.upper(),
        "normalized_aliases": sorted(
            {normalize_merchant_alias(alias) for alias in [slug, *aliases]}
        ),
    }
    typography_source = profile.get("typography", {})
    typography = {
        "typography": _select_keys(
            typography_source, MEASURED_TYPOGRAPHY_FIELDS
        ),
        "section_scale": profile.get("section_scale", {}),
    }
    flags: dict[str, Any] = {
        "typography": _select_keys(
            typography_source, DECIDED_TYPOGRAPHY_FIELDS
        )
    }
    for key in PROFILE_FLAG_GROUPS:
        if key in profile:
            flags[key] = profile[key]
    layout_template = profile.get("layout_template")
    if isinstance(layout_template, dict):
        layout_template = {
            key: value
            for key, value in layout_template.items()
            if key != "_comment"
        }
    layout = {
        "available": layout_template is not None,
        "template": layout_template,
    }
    assets_profile = {
        key: profile[key] for key in PROFILE_ASSET_KEYS if key in profile
    }
    if "stylemap" in typography_source:
        assets_profile["stylemap_filename"] = typography_source["stylemap"]
    return merchant_name, {
        "identity": identity,
        "typography": typography,
        "flags": flags,
        "layout": layout,
        "assets.profile": assets_profile,
    }


def render_legacy_comparison(
    bundle: Bundle, legacy_path: str, *, table: str
) -> list[str]:
    """Mode C: byte-equivalence report vs the legacy profile source."""
    with open(legacy_path, encoding="utf-8") as handle:
        document = json.load(handle)
    slug = bundle.manifest.slug
    merchant_name, expectations = build_legacy_expectations(document, slug)
    lines = [
        f"# Legacy comparison: {slug} v{bundle.manifest.version} "
        f"vs {os.path.basename(legacy_path)}",
        "",
        f"Table: `{table}` | Legacy profile: {format_value(merchant_name)}",
        "",
        "| component | crosswalk coverage | result |",
        "|---|---|---|",
    ]
    diff_sections: list[tuple[str, list[str]]] = []
    for name in COMPONENT_ORDER:
        if name in LEGACY_SKIP_REASONS:
            lines.append(
                f"| {name} | none — {LEGACY_SKIP_REASONS[name]} | SKIPPED |"
            )
            continue
        if name == "assets":
            expected = expectations["assets.profile"]
            actual = (bundle.payload("assets") or {}).get("profile") or {}
            coverage = "partial: profile sub-object only"
            label = "assets.profile"
        else:
            expected = expectations[name]
            actual = bundle.payload(name)
            coverage = "full (profile-derived)"
            label = name
        if canonical_json_bytes(expected) == canonical_json_bytes(actual):
            lines.append(
                f"| {name} | {coverage} | BYTE-EQUIVALENT "
                f"(`{short_hash(hash_payload(actual))}`) |"
            )
        else:
            lines.append(f"| {name} | {coverage} | DIFFERS (see below) |")
            if name == "assets":
                body = diff_leaves(expected, actual)
            else:
                body = diff_component_payload(name, expected, actual)
            diff_sections.append((label, body))
    if diff_sections:
        for label, body in diff_sections:
            lines += [
                "",
                f"## {label} — legacy -> stored",
                "",
                *(body or ["- (no leaf-level differences found)"]),
            ]
    else:
        lines += [
            "",
            "All profile-derived content is byte-equivalent to the sealed "
            "bundle (expected for G1 v1 bundles minted from this file).",
        ]
    return lines


# --------------------------------------------------------------------------
# --all-sealed: the G2 review packet
# --------------------------------------------------------------------------


def render_all_sealed(
    reader: "MerchantTruthReader", *, table: str
) -> list[str]:
    """Mode B summaries for every SEALED-but-not-ACTIVE version."""
    manifests = reader.list_merchant_truth_manifests()
    active_by_slug = {
        record.slug: record for record in reader.list_active_merchant_truth()
    }
    pending = sorted(
        (
            manifest
            for manifest in manifests
            if manifest.status == "SEALED"
            and manifest.version
            > getattr(active_by_slug.get(manifest.slug), "version", 0)
        ),
        key=lambda manifest: (manifest.slug, manifest.version),
    )
    lines = [
        "# Merchant-truth G2 review packet: SEALED pending activation",
        "",
        f"Table: `{table}` | {len(pending)} sealed version(s) awaiting an "
        "ACTIVE flip",
    ]
    for manifest in pending:
        bundle = load_bundle(reader, manifest.slug, manifest.version)
        lines += [
            "",
            "---",
            "",
            *render_activation_summary(
                bundle, active_by_slug.get(manifest.slug), table=table
            ),
        ]
    if not pending:
        lines += ["", "No sealed version is waiting on an ACTIVE flip."]
    return lines


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    *,
    reader: "MerchantTruthReader | None" = None,
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--slug", help="merchant slug, e.g. costco_wholesale")
    parser.add_argument(
        "--from",
        dest="from_version",
        metavar="vM",
        help="older sealed version for Mode A (e.g. v1)",
    )
    parser.add_argument(
        "--to",
        dest="to_version",
        metavar="vN",
        help="sealed version under review (e.g. v1)",
    )
    parser.add_argument(
        "--legacy",
        metavar="PATH",
        help="Mode C: legacy merchant_profiles.json to compare against",
    )
    parser.add_argument(
        "--all-sealed",
        action="store_true",
        help="emit Mode B summaries for every SEALED-but-not-ACTIVE version",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="DynamoDB table (default: DYNAMODB_TABLE_NAME env or "
        f"{DEV_TABLE_NAME}); prod is refused unconditionally",
    )
    args = parser.parse_args(argv)

    try:
        table = resolve_table(args.table)
        if args.all_sealed:
            if (
                args.slug
                or args.to_version
                or args.from_version
                or args.legacy
            ):
                raise ValueError(
                    "--all-sealed takes no --slug/--from/--to/--legacy"
                )
        else:
            if not args.slug or not args.to_version:
                raise ValueError(
                    "--slug and --to are required (or use --all-sealed)"
                )
            if args.from_version and args.legacy:
                raise ValueError(
                    "--from and --legacy are mutually exclusive modes"
                )
    except ValueError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2

    if reader is None:
        from receipt_dynamo.data.dynamo_client import (  # noqa: PLC0415
            DynamoClient,
        )

        region = os.environ.get("AWS_REGION", "us-east-1")
        reader = DynamoClient(table_name=table, region=region)

    try:
        if args.all_sealed:
            lines = render_all_sealed(reader, table=table)
        else:
            to_version = parse_version(args.to_version)
            bundle = load_bundle(reader, args.slug, to_version)
            if args.from_version:
                from_version = parse_version(args.from_version)
                old_bundle = load_bundle(reader, args.slug, from_version)
                lines = render_version_diff(old_bundle, bundle, table=table)
            elif args.legacy:
                lines = render_legacy_comparison(
                    bundle, args.legacy, table=table
                )
            else:
                active = reader.get_active_merchant_truth(args.slug)
                lines = render_activation_summary(bundle, active, table=table)
    except (DiffError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
