"""Cluster-before-pool layout variants (W-G, Costco v2 pilot).

``build_layout_template`` pools column/section/separator evidence across ALL
of a merchant's scans, which averages away real format families (Costco
register vs self-checkout). This module clusters the per-receipt stylescan
records FIRST, then pools per cluster:

1. ``receipt_signature``   -- one scan -> a layout signature: the canonical
   section sequence + first-appearance position fractions, the raw stylescan
   section markers present (``self_checkout`` survives here even though it
   canonicalizes to ``storefront``), and the per-section column lanes from
   the columnscan rider.
2. ``signature_distance``  -- weighted distance over signature space
   (sequence edit distance / marker Jaccard / matched-lane column geometry /
   section position fractions).
3. ``cluster_signatures``  -- deterministic average-linkage agglomerative
   clustering with a fixed distance threshold. There is NO randomness
   anywhere (the "seed" is the algorithm itself): receipts are processed in
   sorted-receipt-key order and merge ties break on the lowest cluster
   index, so a shuffled input yields byte-identical output.
4. ``build_variant_payload`` -- pools each cluster through the EXISTING
   ``build_layout_template`` machinery and emits a v3.1-shaped layout
   payload: dominant cluster as the top-level default (``version: 1`` --
   passes the unchanged ``validate_layout_template``), other clusters as
   ``template.variants[]`` with ``{variant_id, classifier_hint, columns,
   sections, separators, support, source_receipt_keys}``, plus a
   CONFIRM/REFUTE verdict block for the layout-variant proposal.

Scan-dir convention (artifacts are NOT committed; ``tools/glyph-studio/
.gitignore`` ignores ``.out/``):

    tools/glyph-studio/.out/stylescan/<merchant_slug>/
        <image_id>_<receipt_id>.json   -- one stylescan.measure record
        manifest.json                  -- sha256 per record + provenance
    tools/glyph-studio/.out/variant_layout/
        <merchant_slug>_variant_layout.json  -- the emitted payload

Driven by ``scripts/build_variant_layout.py``.
"""

from __future__ import annotations

from statistics import mean, median

try:
    from .layout_template import (
        _line_y_frac,
        build_layout_template,
    )
    from .sections import normalize_stylescan_section
    from .styleagg import receipt_section_columns
except ImportError:  # bare-script invocation
    from layout_template import _line_y_frac, build_layout_template
    from sections import normalize_stylescan_section
    from styleagg import receipt_section_columns

# --- signature space --------------------------------------------------------

# Raw stylescan section names that describe CONTENT of a single row, not the
# receipt's format family -- excluded from the marker set.
_NON_MARKER_RAW = frozenset({"item", "other", "separator", "barcode_caption"})

# Distance component weights (sum to 1). Column geometry dominates: two
# format families print the same sections at different lanes long before
# they reorder them.
WEIGHTS = {
    "sequence": 0.30,
    "markers": 0.20,
    "columns": 0.35,
    "positions": 0.15,
}
# An x-delta of this fraction of paper width makes two same-role lanes
# "fully different" (cost 1.0); deltas scale linearly below it.
COLUMN_X_SCALE = 0.25
# Two receipts merge (average linkage) while their cluster distance is at or
# under this. Calibrated so content jitter (item-count differences, small
# lane wobble) stays a single cluster while a lane shift of ~0.05+ paper
# width plus marker/sequence deltas separates.
DEFAULT_THRESHOLD = 0.18
# A cluster needs at least this many receipts to be emitted as a variant
# (pooling's own support floor is 2; a singleton pools to nothing).
MIN_VARIANT_SUPPORT = 2
# A pooled variant must retain these canonical sections to be a usable
# layout template; a cluster whose pooling drops them (e.g. every item row
# reclassified by a content rule) is a measurement artifact, recorded as
# DEGENERATE in the verdict rather than emitted as a variant.
REQUIRED_VARIANT_SECTIONS = ("items",)
# A classifier-hint marker must be common to every receipt of one cluster
# and present in at most this fraction of the other cluster's receipts --
# markers merely missing from a couple of the other side's receipts are
# noise, not classifiers.
MARKER_DISTINCTIVE_MAX_OTHER_FRAC = 0.2


def receipt_key(image_id: str, receipt_id: int | str) -> str:
    """Canonical receipt reference: matches the Dynamo key vocabulary."""
    return f"IMAGE#{image_id}#RECEIPT#{int(receipt_id):05d}"


def receipt_signature(scan: dict) -> dict:
    """One stylescan record -> layout signature (see module docstring)."""
    lines = scan.get("lines") or []
    n = max(1, len(lines))
    sequence: list[str] = []
    positions: dict[str, float] = {}
    markers: set[str] = set()
    for i, line in enumerate(lines):
        raw = line.get("section")
        canonical = line.get(
            "section_canonical"
        ) or normalize_stylescan_section(raw)
        if raw and raw not in _NON_MARKER_RAW:
            markers.add(raw)
        if canonical and canonical not in positions:
            sequence.append(canonical)
            positions[canonical] = _line_y_frac(line, scan, i, n)
    return {
        "sequence": sequence,
        "positions": positions,
        "markers": frozenset(markers),
        "columns": receipt_section_columns(lines),
    }


# --- distance ---------------------------------------------------------------


def _levenshtein(a: list[str], b: list[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (ca != cb),
                )
            )
        prev = cur
    return prev[-1]


def _sequence_dist(a: list[str], b: list[str]) -> float:
    denom = max(len(a), len(b), 1)
    return _levenshtein(a, b) / denom


def _marker_dist(a: frozenset, b: frozenset) -> float:
    union = a | b
    if not union:
        return 0.0
    return 1.0 - len(a & b) / len(union)


def _lane_xs(cols: list[dict], role: str) -> list[float]:
    return sorted(c["x"] for c in cols if c["role"] == role)


def _columns_dist(
    ca: dict[str, list[dict]], cb: dict[str, list[dict]]
) -> float:
    """Matched-lane geometry distance over every (section, role) slot.

    Same-role lanes match greedily by nearest x (cost ``|dx|/COLUMN_X_SCALE``
    capped at 1); a lane with no counterpart costs 1. Averaged over slots so
    receipts with many sections aren't penalized for having more evidence.
    """
    slots = sorted(
        {(sec, c["role"]) for sec, cols in ca.items() for c in cols}
        | {(sec, c["role"]) for sec, cols in cb.items() for c in cols}
    )
    if not slots:
        return 0.0
    cost = 0.0
    count = 0
    for sec, role in slots:
        xs_a = _lane_xs(ca.get(sec, []), role)
        xs_b = _lane_xs(cb.get(sec, []), role)
        remaining = list(xs_b)
        for x in xs_a:
            if remaining:
                nearest = min(remaining, key=lambda v: abs(v - x))
                remaining.remove(nearest)
                cost += min(abs(nearest - x) / COLUMN_X_SCALE, 1.0)
            else:
                cost += 1.0
            count += 1
        cost += len(remaining)
        count += len(remaining)
    return cost / max(count, 1)


def _positions_dist(pa: dict[str, float], pb: dict[str, float]) -> float:
    shared = sorted(set(pa) & set(pb))
    if not shared:
        return 1.0
    return min(mean(abs(pa[s] - pb[s]) for s in shared) / 0.5, 1.0)


def signature_distance(a: dict, b: dict) -> float:
    return (
        WEIGHTS["sequence"] * _sequence_dist(a["sequence"], b["sequence"])
        + WEIGHTS["markers"] * _marker_dist(a["markers"], b["markers"])
        + WEIGHTS["columns"] * _columns_dist(a["columns"], b["columns"])
        + WEIGHTS["positions"]
        * _positions_dist(a["positions"], b["positions"])
    )


# --- clustering -------------------------------------------------------------


def cluster_signatures(
    signatures: list[dict], *, threshold: float = DEFAULT_THRESHOLD
) -> list[list[int]]:
    """Average-linkage agglomerative clustering, fully deterministic.

    Returns clusters as sorted index lists, ordered by (-size, first index).
    Merge order: always the pair with the smallest average distance; exact
    ties break on the lowest (i, j) cluster indices.
    """
    n = len(signatures)
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = signature_distance(signatures[i], signatures[j])
            dist[i][j] = dist[j][i] = d
    clusters: list[list[int]] = [[i] for i in range(n)]
    while len(clusters) > 1:
        best: tuple[float, int, int] | None = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = mean(dist[a][b] for a in clusters[i] for b in clusters[j])
                if d <= threshold and (best is None or d < best[0] - 1e-12):
                    best = (d, i, j)
        if best is None:
            break
        _, i, j = best
        clusters[i] = sorted(clusters[i] + clusters[j])
        del clusters[j]
    clusters = [sorted(c) for c in clusters]
    clusters.sort(key=lambda c: (-len(c), c[0]))
    return clusters


# --- payload ----------------------------------------------------------------


def _common_markers(signatures: list[dict], members: list[int]) -> frozenset:
    common: frozenset | None = None
    for idx in members:
        m = signatures[idx]["markers"]
        common = m if common is None else common & m
    return common or frozenset()


def _section_names(template: dict) -> list[str]:
    return [s["name"] for s in template.get("sections", [])]


def _marker_prevalence(
    signatures: list[dict], members: list[int]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for idx in members:
        for marker in signatures[idx]["markers"]:
            counts[marker] = counts.get(marker, 0) + 1
    return dict(sorted(counts.items()))


def _distinctive_markers(
    signatures: list[dict],
    members: list[int],
    others: list[int],
    *,
    max_other_frac: float = MARKER_DISTINCTIVE_MAX_OTHER_FRAC,
) -> list[str]:
    """Markers common to EVERY ``members`` receipt and present in at most
    ``max_other_frac`` of ``others`` -- crisp per-receipt classifiers."""
    common = _common_markers(signatures, members)
    other_counts = _marker_prevalence(signatures, others)
    limit = max_other_frac * max(len(others), 1)
    return sorted(m for m in common if other_counts.get(m, 0) <= limit)


def _missing_variant_sections(template: dict) -> list[str]:
    """REQUIRED_VARIANT_SECTIONS a pooled cluster template failed to keep
    (either absent from ``sections`` or without any column lane)."""
    names = set(_section_names(template))
    columns = template.get("columns") or {}
    return [
        sec
        for sec in REQUIRED_VARIANT_SECTIONS
        if sec not in names or not columns.get(sec)
    ]


def _classifier_hint(
    signatures: list[dict],
    members: list[int],
    default_members: list[int],
    template: dict,
    default_template: dict,
) -> dict:
    """Machine-usable features separating a variant from the default.

    ``markers_any``/``markers_absent`` are raw stylescan section names common
    to every receipt of one cluster and rare (<= MARKER_DISTINCTIVE_MAX_
    OTHER_FRAC) in the other -- a variant-aware reader (W-H) can classify a
    receipt by probing its lines against the same stylescan rules.
    """
    return {
        "markers_any": _distinctive_markers(
            signatures, members, default_members
        ),
        "markers_absent": _distinctive_markers(
            signatures, default_members, members
        ),
        "sections_added": sorted(
            set(_section_names(template))
            - set(_section_names(default_template))
        ),
        "sections_missing": sorted(
            set(_section_names(default_template))
            - set(_section_names(template))
        ),
    }


def _variant_id(hint: dict, ordinal: int) -> str:
    for marker in hint.get("markers_any", []):
        return marker
    return f"variant-{ordinal}"


def _column_shift_features(
    variant_id: str, template: dict, default_template: dict
) -> list[str]:
    """Human-readable lane shifts between a variant and the default."""
    out: list[str] = []
    for sec, cols in sorted((template.get("columns") or {}).items()):
        base = default_template.get("columns", {}).get(sec, [])
        for col in cols:
            xs = _lane_xs(base, col["role"])
            if not xs:
                continue
            nearest = min(xs, key=lambda v: abs(v - col["x"]))
            delta = col["x"] - nearest
            if abs(delta) >= 0.03:
                out.append(
                    f"{variant_id}: {sec}.{col['role']} lane at "
                    f"x={col['x']:.3f} vs default x={nearest:.3f} "
                    f"(shift {delta:+.3f})"
                )
    return out


def _strip_template(template: dict) -> dict:
    """The pooled measurement of one cluster, without the top-level wrapper
    keys (version/_comment/measured) that only the default carries."""
    return {
        "columns": template["columns"],
        "sections": template["sections"],
        "separators": template["separators"],
    }


def build_variant_payload(
    scans_by_key: list[tuple[str, dict]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_variant_support: int = MIN_VARIANT_SUPPORT,
    proposal: str = "self-checkout-layout-variant",
    excluded_receipt_keys: list[dict] | None = None,
    measured_at: str | None = None,
) -> dict:
    """Cluster scans, pool per cluster, emit template + verdict + provenance.

    ``scans_by_key`` pairs each stylescan record with its canonical receipt
    key; input order is irrelevant (sorted internally). ``excluded_receipt_
    keys`` records receipts deliberately left out of the corpus (the PHOTO
    exclusion) as ``{"receipt_key", "reason"}`` dicts.
    """
    ordered = sorted(scans_by_key, key=lambda kv: kv[0])
    keys = [k for k, _ in ordered]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate receipt keys in scans_by_key")
    scans = [s for _, s in ordered]
    signatures = [receipt_signature(s) for s in scans]
    clusters = cluster_signatures(signatures, threshold=threshold)

    kept = [c for c in clusters if len(c) >= min_variant_support]
    outliers = [c for c in clusters if len(c) < min_variant_support]
    fallback_pooled_all = False
    if not kept:
        # No stable cluster at all: fall back to pooling the whole corpus as
        # one cluster (the plan's REFUTE fallback -- nothing blocks the mint).
        kept = [sorted(i for c in clusters for i in c)]
        outliers = []
        fallback_pooled_all = True

    templates = [
        build_layout_template([scans[i] for i in members]) for members in kept
    ]
    default_members, default_template = kept[0], templates[0]

    variants = []
    variant_members: list[list[int]] = []
    degenerate: list[dict] = []
    distinguishing: list[str] = []
    for members, tpl in zip(kept[1:], templates[1:]):
        missing = _missing_variant_sections(tpl)
        if missing:
            # The cluster is real signature-space structure, but its pooled
            # template is unusable as a layout (a receipt layout without an
            # items block is a classification artifact, not a format).
            degenerate.append(
                {
                    "receipt_refs": [keys[i] for i in members],
                    "reason": (
                        "pooled template missing required section(s): "
                        + ", ".join(missing)
                    ),
                    "markers_common": sorted(
                        _common_markers(signatures, members)
                    ),
                }
            )
            continue
        hint = _classifier_hint(
            signatures, members, default_members, tpl, default_template
        )
        vid = _variant_id(hint, len(variants) + 1)
        variants.append(
            {
                "variant_id": vid,
                "classifier_hint": hint,
                **_strip_template(tpl),
                "support": len(members),
                "source_receipt_keys": [keys[i] for i in members],
            }
        )
        variant_members.append(members)
        for feat_key, label in (
            ("markers_any", "prints"),
            ("markers_absent", "omits"),
            ("sections_added", "adds section"),
            ("sections_missing", "drops section"),
        ):
            for name in hint[feat_key]:
                distinguishing.append(f"{vid}: {label} {name!r}")
        distinguishing.extend(
            _column_shift_features(vid, tpl, default_template)
        )

    template = dict(default_template)
    template["_comment"] = (
        "Measured layout, cluster-before-pool (W-G): receipts clustered by "
        "layout signature, columns/sections/separators pooled per cluster "
        "via glyphstudio.variant_cluster. Top level = dominant cluster; "
        "other clusters in `variants`."
    )
    template["variant_id"] = "default"
    template["support"] = len(default_members)
    template["source_receipt_keys"] = [keys[i] for i in default_members]
    template["variants"] = variants
    template["measured"] = {
        **default_template["measured"],
        "receipts": len(default_members),
        "receipts_total": len(scans),
        "clustering": {
            "algorithm": "average-linkage-agglomerative",
            "deterministic": True,
            "threshold": threshold,
            "weights": WEIGHTS,
            "column_x_scale": COLUMN_X_SCALE,
            "min_variant_support": min_variant_support,
            "required_variant_sections": list(REQUIRED_VARIANT_SECTIONS),
            "clusters": 1 + len(variants),
            "degenerate_clusters": len(degenerate),
            "outliers": sum(len(c) for c in outliers),
        },
    }

    confirmed = bool(variants)
    if fallback_pooled_all:
        reason = (
            "no cluster reached min support "
            f"{min_variant_support}; pooled all receipts as one template"
        )
    elif confirmed:
        reason = (
            f"{1 + len(variants)} stable clusters separate at threshold "
            f"{threshold}; distinguishing features listed"
        )
    else:
        reason = (
            "single stable cluster at threshold "
            f"{threshold}; no second usable layout format found"
        )
        if degenerate:
            reason += (
                f" ({len(degenerate)} degenerate cluster(s) demoted: "
                "pooled template unusable as a layout)"
            )
    verdict = {
        "proposal": proposal,
        "status": "CONFIRM" if confirmed else "REFUTE",
        "reason": reason,
        "clusters": [
            {
                "variant_id": (
                    "default"
                    if ordinal == 0
                    else variants[ordinal - 1]["variant_id"]
                ),
                "support": len(members),
                "receipt_refs": [keys[i] for i in members],
                "markers_common": sorted(_common_markers(signatures, members)),
                "marker_prevalence": _marker_prevalence(signatures, members),
            }
            for ordinal, members in enumerate(
                [default_members, *variant_members]
            )
        ],
        "degenerate_clusters": degenerate,
        "outliers": [
            {"receipt_refs": [keys[i] for i in members]}
            for members in outliers
        ],
        "distinguishing_features": distinguishing,
    }

    provenance = {
        "source_kind": "measurement",
        "pipeline": "build_variant_layout",
        "pipeline_version": "1",
        "git_sha": default_template["measured"].get("tool_git_sha"),
        "measured_at": measured_at,
        "source_receipt_keys": keys,
        "excluded_receipt_keys": excluded_receipt_keys or [],
        "params": {
            "threshold": threshold,
            "weights": WEIGHTS,
            "column_x_scale": COLUMN_X_SCALE,
            "min_variant_support": min_variant_support,
        },
    }
    return {"template": template, "verdict": verdict, "provenance": provenance}


def pairwise_distance_report(
    scans_by_key: list[tuple[str, dict]],
) -> list[tuple[str, str, float]]:
    """(key_a, key_b, distance) for every pair -- threshold calibration aid."""
    ordered = sorted(scans_by_key, key=lambda kv: kv[0])
    sigs = [receipt_signature(s) for _, s in ordered]
    out = []
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            out.append(
                (
                    ordered[i][0],
                    ordered[j][0],
                    round(signature_distance(sigs[i], sigs[j]), 4),
                )
            )
    return out


def median_link_summary(
    report: list[tuple[str, str, float]],
) -> dict[str, float]:
    """Distance distribution summary for the CLI printout."""
    ds = [d for _, _, d in report]
    if not ds:
        return {}
    ds_sorted = sorted(ds)
    return {
        "min": ds_sorted[0],
        "median": round(median(ds_sorted), 4),
        "max": ds_sorted[-1],
    }
