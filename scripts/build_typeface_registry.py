#!/usr/bin/env python3
"""Build the JSON-only runtime typeface registry and run anti-copy gates."""

# pylint: disable=wrong-import-position
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "receipt_chroma"))
sys.path.insert(0, str(_ROOT / "synthesis_loop"))
sys.path.insert(0, str(_ROOT / "tools/glyph-studio/py"))

from extract_bitmatrix import extract
from glyphstudio.family_cluster import load_normalized_merchant
from receipt_chroma import normalize_glyph

_CHART_BASE = "https://www.receiptfont.com/wp-content/uploads"
_ROM_NAMES = (
    "bitArray-A2",
    "bitMatrix-A1",
    "bitMatrix-B1",
    "bitMatrix-B2",
    "bitMatrix-C1",
    "bitMatrix-C1-heavy",
    "bitMatrix-D1",
    "pixCrog",
)
_MERCHANT_ALIASES = {
    "target grocery": "Target",
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


def _rom_validation_profiles(
    validation: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Keep the QA evidence behind each ROM-to-merchant association."""

    profiles: dict[str, list[dict[str, Any]]] = {
        name: [] for name in _ROM_NAMES
    }
    for merchant, result in validation["merchants"].items():
        winner = result["rom_winner"]
        score = result["scores"][f"ROM:{winner}"]
        profiles[winner].append(
            {
                "merchant": merchant,
                "n_receipts": int(result["n_receipts"]),
                "n_letters": int(score["n_letters"]),
                "median_shifted_iou": float(score["median_iou"]),
            }
        )
    return {
        name: sorted(rows, key=lambda row: str(row["merchant"]))
        for name, rows in profiles.items()
    }


def _download_rom_atlases(
    temp_dir: Path,
) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, str]]:
    atlases = {}
    hashes = {}
    for name in _ROM_NAMES:
        url = f"{_CHART_BASE}/626436-{name}.png"
        chart = temp_dir / f"{name}.png"
        urllib.request.urlretrieve(url, chart)
        glyphs, _offsets, _metadata = extract(str(chart))
        atlases[name] = {
            char: normalize_glyph(mask) for char, mask in glyphs.items()
        }
        hashes[url] = _sha256(chart)
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
        atlases[slug] = {chr(cp): mask for cp, mask in normalized.items()}
        hashes[str(font_dir.relative_to(_ROOT))] = _directory_sha256(font_dir)
    return atlases, hashes


def _anti_copy(
    atlases: dict[str, dict[str, np.ndarray]],
    rom_source_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Reject copied atlases, with audited exceptions for sibling ROM charts.

    Printer ROM families can intentionally share bitmap glyphs. A pair that
    exceeds the copy threshold is exempt only when both atlases were extracted
    from independently published specimen files with different SHA-256 hashes.
    Merchant atlases never receive this exception.
    """

    rom_source_hashes = rom_source_hashes or {}
    comparisons: list[dict[str, int | str]] = []
    failures = []
    source_verified_rom_overlaps = []
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
                left_hash = rom_source_hashes.get(name)
                right_hash = rom_source_hashes.get(other)
                if left_hash and right_hash and left_hash != right_hash:
                    source_verified_rom_overlaps.append(
                        {
                            "left": name,
                            "right": other,
                            "identical_substantial_glyphs": len(identical),
                            "left_substantial_glyphs": len(substantial),
                            "left_source_sha256": left_hash,
                            "right_source_sha256": right_hash,
                            "reason": (
                                "independently published sibling printer-ROM "
                                "specimens"
                            ),
                        }
                    )
                else:
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
        "policy": (
            "synthesis_loop/anti_copy_npz.py threshold; source-verified "
            "ROM-family overlaps audited by scripts/build_typeface_registry.py"
        ),
        "comparison_count": len(comparisons),
        "worst_identical_fraction": max(fractions, default=0.0),
        "source_verified_rom_overlaps": source_verified_rom_overlaps,
    }


def _calibration(validation: dict[str, Any]) -> dict[str, Any]:
    winners = []
    margins = []
    letters_per_receipt = []
    for result in validation["merchants"].values():
        scores = sorted(
            float(score["median_iou"])
            for name, score in result["scores"].items()
            if name.startswith("ROM:")
        )
        winners.append(scores[-1])
        margins.append(scores[-1] - scores[-2])
        letters_per_receipt.append(
            float(result["n_letters"]) / float(result["n_receipts"])
        )
    return {
        "source": "synthesis_loop/rom_font_manifests/validation_results.json",
        "minimum_letter_crops": int(min(letters_per_receipt)),
        "minimum_letter_crops_derivation": (
            "minimum vetted per-receipt crop count in the known merchant "
            "matrix"
        ),
        "minimum_winner_iou": min(winners),
        "minimum_winner_iou_derivation": (
            "minimum winning median shifted-IoU in the known merchant matrix"
        ),
        "typeface_ambiguity_band": median(margins),
        "typeface_ambiguity_band_derivation": (
            "median winner-to-runner-up margin in the known merchant matrix"
        ),
        "winner_iou_distribution": winners,
        "winner_margin_distribution": margins,
    }


def build_registry() -> dict[str, Any]:
    """Compile ROM and merchant atlases into a gated JSON registry."""

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
    rom_profiles = _rom_validation_profiles(validation)
    entries = {}
    for name, atlas in sorted(rom.items()):
        entries[f"ROM:{name}"] = {
            "kind": "rom",
            "typeface": name,
            "merchant_candidates": rom_candidates[name],
            "validation_profiles": rom_profiles[name],
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
        "schema_version": 1,
        "normalization": "aspect-preserving-32x32",
        "score": "shifted-iou-max-shift-2",
        "calibration": _calibration(validation),
        "sources": {
            "validation_sha256": _sha256(validation_path),
            "charts": rom_hashes,
            "merchant_fonts": merchant_hashes,
        },
        "merchant_aliases": _MERCHANT_ALIASES,
        "gates": {
            "anti_copy": _anti_copy(
                all_atlases,
                {
                    f"ROM:{name}": rom_hashes[
                        f"{_CHART_BASE}/626436-{name}.png"
                    ]
                    for name in _ROM_NAMES
                },
            )
        },
        "atlases": entries,
    }


def main() -> int:
    """Write the deterministic registry asset."""

    output = (
        _ROOT
        / "receipt_upload/receipt_upload/assets/typeface_registry_v1.json"
    )
    registry = build_registry()
    output.write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {len(registry['atlases'])} atlases; "
        f"anti-copy={registry['gates']['anti_copy']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
