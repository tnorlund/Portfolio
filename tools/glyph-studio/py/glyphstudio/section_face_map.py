"""M2 completion: the (merchant, section) -> (family, face) map.

Assembles the epic's core index from two committed sources:

* **family** — which font family a merchant belongs to
  (:mod:`glyphstudio.family_cluster`, shape-normalized glyph IoU).
* **face** — the section's rendering face (size scale, weight, underline),
  read from each merchant's ``stylemap.json`` (measured from real receipts by
  stylescan). The stylemap's fine-grained sections are folded to the ten
  canonical sections via ``sections.normalize_stylescan_section`` and
  aggregated per canonical section.

This is the map the renderer consumes (M4): per (merchant, section) it knows
the family skeleton to draw from and the face transform to apply.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Optional

from glyphstudio.sections import normalize_stylescan_section

# heaviest wins on a weight tie (emphasis is the salient signal)
_WEIGHT_ORDER = {"normal": 0, "semibold": 1, "bold": 2}


@dataclass(frozen=True)
class Face:
    """A section's rendering face."""

    scale: float          # size scale vs body
    weight: str           # normal | semibold | bold
    underline: bool


def _aggregate_faces(faces: list[Face]) -> Face:
    """Combine several stylemap faces that fold to one canonical section."""
    scale = round(median(f.scale for f in faces), 3)
    # mode weight; tie -> heaviest
    counts = Counter(f.weight for f in faces)
    top = max(counts.values())
    weight = max(
        (w for w, c in counts.items() if c == top),
        key=lambda w: _WEIGHT_ORDER.get(w, 0),
    )
    return Face(scale=scale, weight=weight, underline=any(f.underline for f in faces))


def load_merchant_faces(font_dir: str) -> dict[str, Face]:
    """``stylemap.json`` -> {canonical_section: Face} for one merchant."""
    path = os.path.join(font_dir, "stylemap.json")
    with open(path, encoding="utf-8") as fh:
        sm = json.load(fh)
    by_canon: dict[str, list[Face]] = {}
    for raw, attrs in (sm.get("sections") or {}).items():
        section = normalize_stylescan_section(raw)
        if section is None:
            continue
        face = Face(
            scale=float(attrs.get("sizeScale", 1.0)),
            weight=str(attrs.get("weight", "normal")),
            underline=bool(attrs.get("underline", False)),
        )
        by_canon.setdefault(section, []).append(face)
    return {s: _aggregate_faces(fs) for s, fs in by_canon.items()}


@dataclass
class SectionFaceEntry:
    merchant: str
    section: str
    family: str
    face: Face


def build_section_face_map(
    font_dirs: dict[str, str],
    merchant_family: dict[str, str],
) -> list[SectionFaceEntry]:
    """Assemble the full (merchant, section) -> (family, face) map.

    ``merchant_family`` maps each merchant to its family id (e.g. from
    ``family_cluster.discover_families``).
    """
    out: list[SectionFaceEntry] = []
    for merchant, font_dir in sorted(font_dirs.items()):
        family = merchant_family.get(merchant, merchant)
        for section, face in sorted(load_merchant_faces(font_dir).items()):
            out.append(SectionFaceEntry(merchant, section, family, face))
    return out


def families_to_merchant_family(families: list[list[str]]) -> dict[str, str]:
    """[[m1,m2],[m3]] -> {m1: family_id, ...}; family id = its sorted members."""
    out: dict[str, str] = {}
    for fam in families:
        fid = "+".join(sorted(fam))
        for m in fam:
            out[m] = fid
    return out
