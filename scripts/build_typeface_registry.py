#!/usr/bin/env python3
"""Build the measured JSON-only runtime typeface registry.

The builder never derives a threshold from a desired acceptance result. It
consumes a frozen exact-runtime measurement report and enables a source only
when calibration genuine and impostor distributions have a strict gap and an
untouched holdout independently clears the midpoint threshold.
"""

# Script path bootstrapping is intentional for repository-local tooling.
# pylint: disable=wrong-import-position,import-error
from __future__ import annotations

import hashlib
import json
import ssl
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import certifi
import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "receipt_chroma"))
sys.path.insert(0, str(_ROOT / "synthesis_loop"))
sys.path.insert(0, str(_ROOT / "tools/glyph-studio/py"))

from extract_bitmatrix import extract
from glyphstudio.family_cluster import load_normalized_merchant
from receipt_chroma import (
    compute_typeface_registry_sha256,
    normalize_glyph,
    validate_typeface_registry,
)

_CHART_BASE = "https://www.receiptfont.com/wp-content/uploads"
_ROM_NAMES = (
    "bitArray-A2",
    "bitMatrix-B2",
    "bitMatrix-C1",
    "bitMatrix-C1-heavy",
    "bitMatrix-D1",
    "pixCrog",
)
_EXPECTED_CHART_HASHES = {
    "bitArray-A2": (
        "f27f5557f235eb1c4ff991ed6b86ef8812a51c187ae7719b16fa4c0827bd8dd5"
    ),
    "bitMatrix-B2": (
        "92bae3962d34af07a210317189138cc3864e82657f017269363cfa021820664c"
    ),
    "bitMatrix-C1": (
        "59454784f883e1f884ccb8afe481d5598523a0d827a37739ff4d6c73d38e0290"
    ),
    "bitMatrix-C1-heavy": (
        "ec0260e91b0fa1e64f1cfff0a5f6d6d32333c3171e4f9b1f51f72b6a9c68d0b4"
    ),
    "bitMatrix-D1": (
        "ac354e9531c0c7f7136606de5395ae0c5cae616c317002d9ca5125fedc9d1350"
    ),
    "pixCrog": (
        "e6e540f5338b47554ba68e65ac7f42ac4f867b54791364ec4129123f8b50551f"
    ),
}
_MERCHANT_NAMES = {
    "costco": "Costco Wholesale",
    "cvs": "CVS",
    "homedepot": "The Home Depot",
    "innout": "In-N-Out Burger",
    "sprouts": "Sprouts Farmers Market",
    "target": "Target",
    "traderjoes": "Trader Joe's",
    "vons": "Vons",
    "wildfork": "Wild Fork",
}
_MEASUREMENT_PATH = (
    _ROOT
    / "synthesis_loop/rom_font_manifests/typeface_runtime_calibration_v2.json"
)
_OUTPUT_PATH = (
    _ROOT / "receipt_upload/receipt_upload/assets/typeface_registry_v2.json"
)


def _encode(mask: np.ndarray) -> str:
    bitmap = np.asarray(mask, dtype=bool)
    if bitmap.shape != (32, 32):
        raise ValueError(f"registry glyph must be 32x32, got {bitmap.shape}")
    return "".join(
        f"{sum(int(value) << (31 - x) for x, value in enumerate(row)):08x}"
        for row in bitmap
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _directory_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for source in sorted(path.rglob("*.json")):
        digest.update(str(source.relative_to(path)).encode())
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _rom_candidates(validation: dict[str, Any]) -> dict[str, list[str]]:
    candidates: dict[str, list[str]] = {name: [] for name in _ROM_NAMES}
    for merchant, result in validation["merchants"].items():
        candidates[result["rom_winner"]].append(merchant)
    return {name: sorted(values) for name, values in candidates.items()}


def _download_rom_atlases(
    temp_dir: Path,
) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, str]]:
    atlases = {}
    hashes = {}
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    for name in _ROM_NAMES:
        url = f"{_CHART_BASE}/626436-{name}.png"
        chart = temp_dir / f"{name}.png"
        with urllib.request.urlopen(url, context=ssl_context) as response:
            chart.write_bytes(response.read())
        actual_hash = _sha256(chart)
        expected_hash = _EXPECTED_CHART_HASHES[name]
        if actual_hash != expected_hash:
            raise RuntimeError(
                f"published chart hash changed for {name}: {actual_hash}"
            )
        glyphs, _offsets, _metadata = extract(str(chart))
        atlases[name] = {
            char: normalize_glyph(mask) for char, mask in glyphs.items()
        }
        hashes[url] = actual_hash
    return atlases, hashes


def _merchant_atlases() -> (
    tuple[dict[str, dict[str, np.ndarray]], dict[str, str]]
):
    atlases = {}
    hashes = {}
    fonts_root = _ROOT / "tools/glyph-studio/fonts"
    for slug in sorted(_MERCHANT_NAMES):
        font_dir = fonts_root / slug
        if not (font_dir / "font.json").exists():
            continue
        normalized = load_normalized_merchant(
            str(font_dir),
            chars=(
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz"
                "0123456789$%&*#@?!:;,.-/()"
            ),
        )
        atlases[slug] = {
            chr(codepoint): mask for codepoint, mask in normalized.items()
        }
        relative = str(font_dir.relative_to(_ROOT))
        hashes[relative] = _directory_sha256(font_dir)
    return atlases, hashes


def _anti_copy(atlases: dict[str, dict[str, np.ndarray]]) -> dict[str, Any]:
    comparisons: list[dict[str, int | str]] = []
    failures = []
    for name in sorted(atlases):
        substantial = {
            char: mask
            for char, mask in atlases[name].items()
            if int(mask.sum()) >= 40
        }
        for other in sorted(atlases):
            if other <= name:
                continue
            identical = [
                char
                for char, mask in substantial.items()
                if char in atlases[other]
                and np.array_equal(mask, atlases[other][char])
            ]
            limit = max(4, int(0.25 * len(substantial)))
            comparisons.append(
                {
                    "left": name,
                    "right": other,
                    "identical_substantial_glyphs": len(identical),
                    "left_substantial_glyphs": len(substantial),
                    "failure_limit": limit,
                }
            )
            if len(identical) > limit:
                failures.append(
                    {"left": name, "right": other, "glyphs": identical}
                )
    if failures:
        raise RuntimeError(f"anti-copy gate failed: {failures}")
    fractions = [
        int(row["identical_substantial_glyphs"])
        / max(int(row["left_substantial_glyphs"]), 1)
        for row in comparisons
    ]
    return {
        "status": "PASS",
        "policy": "exact-glyph-equality-no-exceptions",
        "comparison_count": len(comparisons),
        "worst_identical_fraction": max(fractions, default=0.0),
    }


def build_atlas_registry() -> dict[str, Any]:
    """Build pinned atlas entries without making calibration decisions."""

    validation_path = (
        _ROOT / "synthesis_loop/rom_font_manifests/validation_results.json"
    )
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="typeface-registry-") as temp:
        rom, rom_hashes = _download_rom_atlases(Path(temp))
    merchants, merchant_hashes = _merchant_atlases()
    all_atlases = {
        **{f"ROM:{name}": atlas for name, atlas in rom.items()},
        **{f"MERCHANT:{name}": atlas for name, atlas in merchants.items()},
    }
    rom_candidates = _rom_candidates(validation)
    entries = {}
    for name, atlas in sorted(rom.items()):
        entries[f"ROM:{name}"] = {
            "kind": "rom",
            "typeface": name,
            "merchant_candidates": rom_candidates[name],
            "glyphs": {
                char: _encode(mask) for char, mask in sorted(atlas.items())
            },
        }
    for slug, atlas in sorted(merchants.items()):
        entries[f"MERCHANT:{slug}"] = {
            "kind": "merchant",
            "typeface": f"merchant:{slug}",
            "merchant_candidates": [_MERCHANT_NAMES[slug]],
            "glyphs": {
                char: _encode(mask) for char, mask in sorted(atlas.items())
            },
        }
    return {
        "entries": entries,
        "atlas_registry_sha256": _json_sha256(entries),
        "sources": {
            "validation_sha256": _sha256(validation_path),
            "charts": rom_hashes,
            "merchant_fonts": merchant_hashes,
        },
        "gates": {"anti_copy": _anti_copy(all_atlases)},
    }


def _measured_entry(
    base: dict[str, Any], evidence: dict[str, Any] | None
) -> dict[str, Any]:
    result = {
        **base,
        "enabled": False,
        "disabled_reason": "NO_INDEPENDENT_GENUINE_COHORT",
        "acceptance_threshold": None,
        "acceptance_threshold_derivation": None,
        "genuine_score_distribution": [],
        "impostor_score_distribution": [],
        "holdout_genuine_score_distribution": [],
        "holdout_impostor_score_distribution": [],
        "calibration_receipt_hashes": [],
        "holdout_receipt_hashes": [],
    }
    if evidence is None:
        return result
    result.update(
        {
            "disabled_reason": evidence["disabled_reason"],
            "genuine_score_distribution": evidence[
                "calibration_genuine_scores"
            ],
            "impostor_score_distribution": evidence[
                "calibration_impostor_scores"
            ],
            "holdout_genuine_score_distribution": evidence.get(
                "holdout_genuine_scores", []
            ),
            "holdout_impostor_score_distribution": evidence.get(
                "holdout_impostor_scores", []
            ),
            "calibration_receipt_hashes": evidence[
                "calibration_receipt_hashes"
            ],
            "holdout_receipt_hashes": evidence.get(
                "holdout_receipt_hashes", []
            ),
        }
    )
    genuine = result["genuine_score_distribution"]
    impostor = result["impostor_score_distribution"]
    holdout_genuine = result["holdout_genuine_score_distribution"]
    holdout_impostor = result["holdout_impostor_score_distribution"]
    if not genuine or not impostor:
        return result
    if max(impostor) >= min(genuine):
        return result
    threshold = (max(impostor) + min(genuine)) / 2.0
    if (
        not holdout_genuine
        or not holdout_impostor
        or min(holdout_genuine) < threshold
        or max(holdout_impostor) >= threshold
    ):
        result["disabled_reason"] = "UNTOUCHED_HOLDOUT_DID_NOT_PASS"
        return result
    result.update(
        {
            "enabled": True,
            "disabled_reason": None,
            "acceptance_threshold": threshold,
            "acceptance_threshold_derivation": (
                "midpoint-between-calibration-max-impostor-and-min-genuine"
            ),
        }
    )
    return result


def build_registry(measurement: dict[str, Any]) -> dict[str, Any]:
    """Combine pinned atlases with a frozen exact-runtime measurement."""

    if measurement.get("schema_version") != 2:
        raise ValueError("measurement schema_version must be 2")
    base = build_atlas_registry()
    if (
        measurement.get("atlas_registry_sha256")
        != base["atlas_registry_sha256"]
    ):
        raise RuntimeError("measurement does not match the pinned atlases")
    measurement_sha256 = _json_sha256(measurement)
    evidence = measurement.get("sources", {})
    entries = {
        name: _measured_entry(entry, evidence.get(name))
        for name, entry in base["entries"].items()
    }
    registry = {
        "schema_version": 2,
        "normalization": "glyphstudio-aspect-preserving-32x32",
        "score": "per-distinct-character-median-shifted-iou-max-shift-2",
        "atlas_registry_sha256": base["atlas_registry_sha256"],
        "calibration": {
            **measurement["calibration"],
            "calibration_id": f"exact-runtime-v2-{measurement_sha256[:16]}",
            "measurement_sha256": measurement_sha256,
        },
        "sources": base["sources"],
        "gates": base["gates"],
        "atlases": entries,
    }
    registry["registry_sha256"] = compute_typeface_registry_sha256(registry)
    validate_typeface_registry(registry)
    return registry


def main() -> int:
    """Write the deterministic registry asset."""

    measurement = json.loads(_MEASUREMENT_PATH.read_text(encoding="utf-8"))
    registry = build_registry(measurement)
    _OUTPUT_PATH.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    enabled = [
        name for name, entry in registry["atlases"].items() if entry["enabled"]
    ]
    print(
        f"wrote {len(registry['atlases'])} atlases; "
        f"enabled={enabled}; "
        f"anti-copy={registry['gates']['anti_copy']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
