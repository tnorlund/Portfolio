"""Deterministic, blind typography packet manifests.

Manifests are immutable publications: their filename is derived from crop
identity and bytes, and an existing publication is never changed in place.
Only the ``MANIFEST`` pointer may move to a newly minted manifest.

Judge inputs are blind projections.  Merchant priors, expected typography
answers, and probe verdicts are excluded, and the projection is recursively
checked before it leaves this module.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

MANIFEST_VERSION = "tp-1"
JUDGE_INPUT_SCHEMA_VERSION = "tj-in-1"
JUDGE_VERDICT_SCHEMA_VERSION = "tj-out-1"

FORBIDDEN_JUDGE_KEYS = frozenset(
    {
        "merchant",
        "merchant_name",
        "slug",
        "section",
        "section_type",
        "attribution",
        "attr_dev",
        "attr_dev_rel",
        "typeface",
        "tier",
        "underline",
        "reverse_video",
        "slant_deg",
        "contamination",
        "contaminated",
    }
)


def canonical_json(obj: Any) -> str:
    """Return the byte-level JSON representation used by every manifest."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def packet_id(image_id: str, receipt_id: int, line_index: int) -> str:
    return f"{image_id}_{receipt_id}_L{line_index:03d}"


def _packet_sort_key(packet: dict) -> tuple[str, int, int]:
    return (
        str(packet["image_id"]),
        int(packet["receipt_id"]),
        int(packet["line_index"]),
    )


def _identity_record(packet: dict) -> dict:
    return {
        "image_id": packet["image_id"],
        "receipt_id": packet["receipt_id"],
        "line_index": packet["line_index"],
        "line_ids": packet["line_ids"],
        "line_sha256": packet["crops"]["line"]["sha256"],
        "letter_sha256s": [
            crop["sha256"]
            for crop in sorted(
                packet["crops"]["letters"], key=lambda crop: crop["seq"]
            )
        ],
    }


def _content_hash(packets: list[dict]) -> str:
    identities = [_identity_record(packet) for packet in packets]
    payload = canonical_json(identities).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def build_manifest(packets: list[dict], params: dict) -> dict:
    """Build a deterministic manifest without consulting wall-clock state."""
    ordered = sorted(packets, key=_packet_sort_key)
    return {
        "manifest_version": MANIFEST_VERSION,
        "params": params,
        "content_hash": _content_hash(ordered),
        "n_packets": len(ordered),
        "packets": ordered,
    }


def manifest_filename(manifest: dict) -> str:
    return f"manifest_{manifest['content_hash'][:16]}.json"


def _write_immutable(path: str, payload: bytes) -> None:
    if os.path.exists(path):
        with open(path, "rb") as fh:
            existing = fh.read()
        if existing != payload:
            raise RuntimeError(f"immutable publication differs: {path}")
        return
    with open(path, "xb") as fh:
        fh.write(payload)


def write_manifest(manifest: dict, out_dir: str) -> str:
    """Publish one immutable manifest and update its mutable text pointer."""
    os.makedirs(out_dir, exist_ok=True)
    filename = manifest_filename(manifest)
    path = os.path.join(out_dir, filename)
    _write_immutable(path, (canonical_json(manifest) + "\n").encode("ascii"))
    with open(os.path.join(out_dir, "MANIFEST"), "w", encoding="ascii") as fh:
        fh.write(filename + "\n")
    return path


def _forbidden_keys(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in FORBIDDEN_JUDGE_KEYS:
                found.add(key)
            found.update(_forbidden_keys(value))
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            found.update(_forbidden_keys(value))
    return found


def judge_input(packet: dict) -> dict:
    """Project a manifest packet into the merchant-blind judge schema."""
    line = packet["crops"]["line"]
    letters = sorted(packet["crops"]["letters"], key=lambda crop: crop["seq"])
    result = {
        "schema_version": JUDGE_INPUT_SCHEMA_VERSION,
        "packet_id": packet["packet_id"],
        "line_text": packet["text"],
        "line_crop": {
            key: line[key] for key in ("path", "sha256", "width", "height")
        },
        "letter_crops": [
            {
                key: crop[key]
                for key in ("seq", "char", "path", "sha256", "width", "height")
            }
            for crop in letters
        ],
        "context": {
            "cap_px": packet["features"]["cap_px"],
            "stroke_med": packet["features"]["stroke_med"],
            "receipt_body_cap_px": packet["receipt_context"]["body_cap_px"],
            "receipt_body_stroke_px": packet["receipt_context"][
                "body_stroke_px"
            ],
        },
    }
    forbidden = _forbidden_keys(result)
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise ValueError(f"blind judge input contains forbidden keys: {names}")
    return result


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_manifest(manifest: dict, base_dir: str) -> list[str]:
    """Return crop-integrity and content-hash problems for a manifest tree."""
    problems: list[str] = []
    packets = manifest.get("packets")
    if not isinstance(packets, list):
        return ["manifest packets is not a list"]

    if manifest.get("n_packets") != len(packets):
        problems.append(
            f"n_packets {manifest.get('n_packets')} != "
            f"len(packets) {len(packets)}"
        )
    pids = [p.get("packet_id") for p in packets if isinstance(p, dict)]
    dupes = sorted({pid for pid in pids if pids.count(pid) > 1})
    if dupes:
        problems.append(f"duplicate packet_ids: {', '.join(map(str, dupes))}")

    base = os.path.abspath(base_dir)
    for packet in packets:
        pid = packet.get("packet_id", "<unknown>")
        letters = (
            packet.get("crops", {}).get("letters", [])
            if isinstance(packet.get("crops"), dict)
            else []
        )
        seqs = [c.get("seq") for c in letters if isinstance(c, dict)]
        if len(seqs) != len(set(seqs)):
            problems.append(f"{pid}: duplicate letter seq values")
        try:
            crops = [packet["crops"]["line"], *packet["crops"]["letters"]]
        except (KeyError, TypeError):
            problems.append(f"{pid}: malformed crops record")
            continue
        for crop in crops:
            relpath = crop.get("path")
            if not isinstance(relpath, str):
                problems.append(f"{pid}: crop path is missing")
                continue
            path = os.path.abspath(os.path.join(base, relpath))
            if os.path.commonpath((base, path)) != base:
                problems.append(
                    f"{pid}: crop escapes base directory: {relpath}"
                )
                continue
            if not os.path.isfile(path):
                problems.append(f"{pid}: missing crop: {relpath}")
                continue
            actual = _sha256_file(path)
            expected = crop.get("sha256")
            if actual != expected:
                problems.append(
                    f"{pid}: sha256 mismatch for {relpath}: "
                    f"expected {expected}, got {actual}"
                )
    try:
        expected_content = _content_hash(sorted(packets, key=_packet_sort_key))
    except (KeyError, TypeError, ValueError) as exc:
        problems.append(f"cannot recompute content_hash: {exc}")
    else:
        if manifest.get("content_hash") != expected_content:
            problems.append(
                "content_hash mismatch: expected "
                f"{manifest.get('content_hash')}, got {expected_content}"
            )
    return problems
