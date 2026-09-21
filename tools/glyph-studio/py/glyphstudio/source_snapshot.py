"""Pinned gold-receipt source the closed renderer reads.

A snapshot is the words, boxes, labels, and source size that sized a
committed finale canvas, plus a content hash of the scan bytes. Re-renders
load this file instead of the live dev table. ``label_receipt`` is the id in
the committed ``final.labels.json``. ``manifest_receipt`` is the id in
``pipeline_merchants.json``. They differ when re-OCR reused a receipt id;
``geometry_receipt`` is the row whose words were actually pinned.

The closed path does not follow a later Dynamo geometry. A live payload that
disagrees with the pin is refused. Label and manifest ids alias the geometry
row only for that snapshot's merchant, so a reused receipt id cannot return
another merchant's words.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDIO = os.path.dirname(os.path.dirname(_HERE))
SNAPSHOT_DIR = os.path.join(_STUDIO, "fixtures", "source_snapshots")
CANVAS_WIDTH = 760
SNAPSHOT_VERSION = 1


def canvas_height_for_source(
    width: int, height: int, target_width: int = CANVAS_WIDTH
) -> int:
    """Height of ``width``×``height`` scaled to ``target_width``."""
    return max(1, int(round(target_width * height / float(width))))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _receipt(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    image_id = value.get("image_id")
    receipt_id = value.get("receipt_id")
    if not isinstance(image_id, str) or not image_id:
        raise ValueError(f"{field}.image_id is required")
    if isinstance(receipt_id, bool) or not isinstance(receipt_id, int):
        raise ValueError(f"{field}.receipt_id must be an int")
    return {"image_id": image_id, "receipt_id": receipt_id}


def _require_words(words: Any) -> list[dict[str, Any]]:
    if not isinstance(words, list) or not words:
        raise ValueError("words must be a non-empty list")
    for index, word in enumerate(words):
        if not isinstance(word, dict) or "text" not in word:
            raise ValueError(f"words[{index}] needs text")
        bbox = word.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValueError(f"words[{index}].bbox must be four numbers")
        labels = word.get("labels", [])
        if not isinstance(labels, list):
            raise ValueError(f"words[{index}].labels must be a list")
    return words


def validate_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    """Return ``snap`` or raise ``ValueError`` if it cannot drive a render."""
    if not isinstance(snap, dict):
        raise ValueError("snapshot must be an object")
    if snap.get("version") != SNAPSHOT_VERSION:
        raise ValueError(f"snapshot version must be {SNAPSHOT_VERSION}")
    for key in ("slug", "merchant"):
        if not isinstance(snap.get(key), str) or not snap[key]:
            raise ValueError(f"{key} is required")
    for field in ("label_receipt", "manifest_receipt", "geometry_receipt"):
        _receipt(snap.get(field), field)
    canvas = snap.get("canvas")
    source = snap.get("source_size")
    if not isinstance(canvas, dict) or not isinstance(source, dict):
        raise ValueError("canvas and source_size are required")
    if canvas.get("w") != CANVAS_WIDTH or not isinstance(canvas.get("h"), int):
        raise ValueError(f"canvas must be {{w: {CANVAS_WIDTH}, h: <int>}}")
    width, height = source.get("width"), source.get("height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ValueError("source_size width and height must be positive ints")
    expected = canvas_height_for_source(width, height)
    if canvas["h"] != expected:
        raise ValueError(
            f"canvas h {canvas['h']} is not {expected} from source "
            f"{width}x{height}; the pin must be the size that produced "
            "the committed canvas"
        )
    _require_words(snap.get("words"))
    if not isinstance(snap.get("barcodes", []), list):
        raise ValueError("barcodes must be a list")
    image_sha = snap.get("image_sha256")
    if image_sha is not None and (
        not isinstance(image_sha, str) or len(image_sha) != 64
    ):
        raise ValueError("image_sha256 must be 64 hex chars or null")
    return snap


def snapshot_path(slug: str, directory: str | None = None) -> str:
    return os.path.join(directory or SNAPSHOT_DIR, f"{slug}.json")


def load_snapshot(
    slug: str, directory: str | None = None
) -> dict[str, Any] | None:
    path = snapshot_path(slug, directory)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return validate_snapshot(json.load(fh))


def iter_snapshots(directory: str | None = None) -> list[dict[str, Any]]:
    root = directory or SNAPSHOT_DIR
    if not os.path.isdir(root):
        return []
    snaps = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(root, name), encoding="utf-8") as fh:
            snaps.append(validate_snapshot(json.load(fh)))
    return snaps


def canvas_size(snap: dict[str, Any]) -> tuple[int, int]:
    canvas = validate_snapshot(snap)["canvas"]
    return int(canvas["w"]), int(canvas["h"])


def geometry_ids(snap: dict[str, Any]) -> tuple[str, int]:
    receipt = validate_snapshot(snap)["geometry_receipt"]
    return receipt["image_id"], int(receipt["receipt_id"])


def _keys(snap: dict[str, Any]) -> set[tuple[str, int]]:
    found = set()
    for field in ("label_receipt", "manifest_receipt", "geometry_receipt"):
        receipt = snap[field]
        found.add((receipt["image_id"], int(receipt["receipt_id"])))
    return found


def snapshot_payload(snap: dict[str, Any]) -> dict[str, Any]:
    """Renderer payload shape (``_cached_payload``), copied from the pin."""
    checked = validate_snapshot(snap)
    image_id, receipt_id = geometry_ids(checked)
    source = checked["source_size"]
    return copy.deepcopy(
        {
            "merchant": checked["merchant"],
            "image_id": image_id,
            "receipt_id": receipt_id,
            "width": int(source["width"]),
            "height": int(source["height"]),
            "words": checked["words"],
            "barcodes": checked.get("barcodes") or [],
        }
    )


def resolve_pinned_payload(
    image_id: str,
    receipt_id: int,
    directory: str | None = None,
    *,
    merchant: str | None = None,
) -> dict[str, Any] | None:
    """Pinned payload when ``image_id``/``receipt_id`` is on a same-merchant pin.

    ``label_receipt`` and ``manifest_receipt`` alias ``geometry_receipt``.
    That alias overrides the live row only for the snapshot's own merchant.
    A reused id (Vons label ``#1`` now belongs to someone else) stays on the
    live payload, and a pin for the new owner does not collide with the old
    alias. A geometry id is likewise merchant-scoped when ``merchant`` is set.
    """
    wanted = (image_id, int(receipt_id))
    matches: list[dict[str, Any]] = []
    for snap in iter_snapshots(directory):
        if wanted not in _keys(snap):
            continue
        geometry = (
            snap["geometry_receipt"]["image_id"],
            int(snap["geometry_receipt"]["receipt_id"]),
        )
        alias = wanted != geometry
        if alias:
            if merchant is None or snap["merchant"] != merchant:
                continue
        elif merchant is not None and snap["merchant"] != merchant:
            continue
        matches.append(snap)
    if not matches:
        return None
    if len(matches) > 1:
        slugs = [snap["slug"] for snap in matches]
        raise ValueError(
            f"receipt {image_id}#{receipt_id} is pinned by {slugs}"
        )
    return snapshot_payload(matches[0])


def _fingerprint(doc: dict[str, Any]) -> str:
    words = []
    for word in doc.get("words") or []:
        words.append(
            {
                "text": word.get("text"),
                "bbox": word.get("bbox"),
                "labels": word.get("labels") or [],
                "line_id": word.get("line_id"),
                "word_id": word.get("word_id"),
            }
        )
    payload = {
        "width": doc.get("width"),
        "height": doc.get("height"),
        "words": words,
        "barcodes": doc.get("barcodes") or [],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def refuse_live_mismatch(
    snap: dict[str, Any], live_doc: dict[str, Any]
) -> None:
    """Raise when a live Dynamo payload is not the pinned geometry."""
    pinned = snapshot_payload(snap)
    if _fingerprint(pinned) == _fingerprint(live_doc):
        return
    slug = snap.get("slug", "?")
    raise RuntimeError(
        f"{slug}: live Dynamo geometry does not match the pinned "
        "source snapshot; refusing to follow the live read"
    )


def assert_image_bytes(snap: dict[str, Any], data: bytes) -> None:
    """Raise when scan bytes are not the pinned image."""
    expected = snap.get("image_sha256")
    if not expected:
        return
    actual = sha256_bytes(data)
    if actual != expected:
        raise RuntimeError(
            f"{snap.get('slug', '?')}: live scan sha256 {actual[:12]} "
            f"does not match pinned image {expected[:12]}"
        )


def write_snapshot(snap: dict[str, Any], directory: str | None = None) -> str:
    checked = validate_snapshot(snap)
    root = directory or SNAPSHOT_DIR
    os.makedirs(root, exist_ok=True)
    path = snapshot_path(checked["slug"], root)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(checked, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path
