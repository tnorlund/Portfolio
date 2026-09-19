"""Closed-input provenance written into ``pipeline_merchants.json``."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from typing import Any

PROVENANCE_KEYS = (
    "source_snapshot_sha256",
    "font_sha256",
    "logo_sha256",
    "final_webp_sha256",
    "exporter_commit",
    "image_type",
)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def exporter_commit(repo_root: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def card_provenance(
    *,
    payload: dict[str, Any],
    font_json_path: str | None,
    logo_path: str | None,
    final_webp_path: str | None,
    image_type: str | None,
    commit: str | None,
) -> dict[str, Any]:
    """Closed-input audit trail written into ``pipeline_merchants.json``."""
    return {
        "source_snapshot_sha256": sha256_json(payload),
        "font_sha256": (
            sha256_file(font_json_path)
            if font_json_path and os.path.isfile(font_json_path)
            else None
        ),
        "logo_sha256": (
            sha256_file(logo_path)
            if logo_path and os.path.isfile(logo_path)
            else None
        ),
        "final_webp_sha256": (
            sha256_file(final_webp_path)
            if final_webp_path and os.path.isfile(final_webp_path)
            else None
        ),
        "exporter_commit": commit,
        "image_type": image_type,
    }


def write_manifest_provenance(
    manifest_path: str, slug: str, provenance: dict[str, Any]
) -> None:
    with open(manifest_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    merchants = doc.setdefault("merchants", {})
    if slug not in merchants:
        raise KeyError(f"unknown manifest slug {slug!r}")
    merchants[slug]["provenance"] = {
        k: provenance.get(k) for k in PROVENANCE_KEYS
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
