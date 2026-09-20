"""Closed-input provenance written into ``pipeline_merchants.json``."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Sequence
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


def sha256_files(paths: Sequence[str] | None) -> str | None:
    """Hash the NPZ faces actually rendered, in basename-stable order."""
    files = [p for p in (paths or ()) if p and os.path.isfile(p)]
    if not files:
        return None
    digest = hashlib.sha256()
    for path in sorted(files, key=os.path.basename):
        digest.update(os.path.basename(path).encode("utf-8"))
        digest.update(b"\0")
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def sha256_json(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def git_head_status(repo_root: str) -> tuple[str | None, bool]:
    try:
        sha = subprocess.check_output(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None, False
    try:
        dirty = bool(
            subprocess.check_output(
                ["git", "-C", repo_root, "status", "--porcelain"],
                text=True,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return sha, False
    return sha, dirty


def exporter_commit(
    repo_root: str, *, allow_dirty: bool = False
) -> str | None:
    """Git HEAD for the provenance block.

    Dirty trees refuse by default so a provenance commit cannot silently
    describe bytes that were not what HEAD compiled. ``allow_dirty`` records
    ``{sha}-dirty`` instead.
    """
    sha, dirty = git_head_status(repo_root)
    if sha is None:
        return None
    if not dirty:
        return sha
    if not allow_dirty:
        raise RuntimeError(
            f"refusing dirty HEAD {sha[:12]} for provenance; "
            "commit your changes or pass --allow-dirty"
        )
    return f"{sha}-dirty"


def card_provenance(
    *,
    payload: dict[str, Any],
    font_paths: Sequence[str] | None = None,
    logo_path: str | None,
    final_webp_path: str | None,
    image_type: str | None,
    commit: str | None,
    logo_used: bool = True,
) -> dict[str, Any]:
    """Closed-input audit trail written into ``pipeline_merchants.json``.

    ``font_sha256`` hashes the regular/heavy NPZ faces the renderer actually
    resolved (truth-bundle bitMatrix-C2 for Costco, not local font.json).
    ``logo_sha256`` is omitted when this run skipped writing logo.png.
    """
    return {
        "source_snapshot_sha256": sha256_json(payload),
        "font_sha256": sha256_files(font_paths),
        "logo_sha256": (
            sha256_file(logo_path)
            if logo_used and logo_path and os.path.isfile(logo_path)
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
