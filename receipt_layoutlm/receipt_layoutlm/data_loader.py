import hashlib
import importlib
import json
import math
import os
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import CORE_LABELS, ValidationStatus

SYNTHESIS_OPERATION_FAMILIES = (
    "hard_negative",
    "add_line_item",
    "remove_line_item",
    "replace_field",
    "compose_online_catalog",
)
SYNTHETIC_CANDIDATE_COLLECTION_KEYS = (
    "synthetic_receipt_candidates",
    "synthetic_training_examples",
    "candidates",
    "examples",
)
SYNTHETIC_CANDIDATE_CONTAINER_KEYS = (
    *SYNTHETIC_CANDIDATE_COLLECTION_KEYS,
    "line_item_patterns",
)


@dataclass
class SplitMetadata:
    """Metadata about the train/validation split for reproducibility tracking."""

    random_seed: int
    num_train_receipts: int
    num_val_receipts: int
    num_train_lines: int
    num_val_lines: int
    # Hash of sorted receipt keys for verification without storing full list
    train_receipts_hash: str
    val_receipts_hash: str
    # O:entity ratio metrics
    o_entity_ratio_before_downsample: float
    o_entity_ratio_after_downsample: float
    o_entity_ratio_val: float
    # Downsampling stats
    o_only_lines_total: int
    o_only_lines_kept: int
    o_only_lines_dropped: int
    entity_lines_total: int
    # Target ratio used
    target_o_entity_ratio: float
    # v3 image cache directory (set when model_version == "v3")
    image_cache_dir: Optional[str] = None
    # Full sorted list of validation receipt keys ("image_id#receipt_id").
    # Persisted so downstream tooling (e.g. per-epoch checkpoint evaluation) can
    # reproduce the exact held-out set even after the labeled data drifts, which
    # the hash alone cannot guarantee.
    val_receipt_keys: Optional[List[str]] = None
    # Sliding-window config used to build training examples. Persisted so
    # per-epoch evaluation windows inference identically to training (a
    # different window/stride shifts the F1 curve by inference, not quality).
    window_size: Optional[int] = None
    window_stride: Optional[int] = None
    # Train-only synthetic examples appended from confusion/heatmap recipes.
    synthetic_train_examples: int = 0
    synthetic_source: Optional[str] = None
    synthetic_candidates_seen: int = 0
    synthetic_candidates_accepted: int = 0
    synthetic_candidates_rejected: int = 0
    synthetic_rejection_reasons: Dict[str, int] = field(default_factory=dict)
    synthetic_accepted_operation_counts: Dict[str, int] = field(default_factory=dict)
    synthetic_accepted_operation_coverage: Dict[str, Any] = field(
        default_factory=dict
    )
    synthetic_accepted_category_counts: Dict[str, int] = field(default_factory=dict)
    synthetic_accepted_field_replacement_counts: Dict[str, int] = field(
        default_factory=dict
    )
    synthetic_accepted_structure_similarity: Dict[str, Any] = field(
        default_factory=dict
    )
    synthetic_accepted_structure_components: Dict[str, Any] = field(
        default_factory=dict
    )
    synthetic_accepted_candidate_quality: Dict[str, Any] = field(
        default_factory=dict
    )
    synthetic_accepted_candidate_quality_components: Dict[str, Any] = field(
        default_factory=dict
    )
    synthetic_accepted_real_baseline_comparison: Dict[str, Any] = field(
        default_factory=dict
    )
    synthetic_accepted_mix_balance: Dict[str, Any] = field(default_factory=dict)
    synthetic_accepted_grounded_count: int = 0
    synthetic_accepted_arithmetic_count: int = 0


@dataclass
class SyntheticTrainingLoad:
    """Accepted synthetic rows plus bounded audit stats for rejected rows."""

    examples: List[dict[str, Any]] = field(default_factory=list)
    accepted_rows: List[dict[str, Any]] = field(default_factory=list)
    rejected_rows: List[dict[str, Any]] = field(default_factory=list)
    candidates_seen: int = 0
    candidates_accepted: int = 0
    candidates_rejected: int = 0
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    accepted_operation_counts: Dict[str, int] = field(default_factory=dict)
    accepted_operation_coverage: Dict[str, Any] = field(default_factory=dict)
    accepted_category_counts: Dict[str, int] = field(default_factory=dict)
    accepted_field_replacement_counts: Dict[str, int] = field(default_factory=dict)
    accepted_structure_similarity: Dict[str, Any] = field(default_factory=dict)
    accepted_structure_components: Dict[str, Any] = field(default_factory=dict)
    accepted_candidate_quality: Dict[str, Any] = field(default_factory=dict)
    accepted_candidate_quality_components: Dict[str, Any] = field(
        default_factory=dict
    )
    accepted_real_baseline_comparison: Dict[str, Any] = field(default_factory=dict)
    accepted_mix_balance: Dict[str, Any] = field(default_factory=dict)
    accepted_grounded_count: int = 0
    accepted_arithmetic_count: int = 0


@dataclass
class LineExample:
    image_id: str
    receipt_id: int
    line_id: int
    tokens: List[str]
    bboxes: List[List[int]]
    ner_tags: List[str]
    receipt_key: str


@dataclass
class WordInfo:
    """Intermediate representation of a word with its metadata."""

    word_id: int
    text: str
    bbox: List[int]  # [x0, y0, x1, y1] normalized
    label: str  # Merged label for BIO tagging (not BIO prefix)
    original_label: str  # Original label before merging (for grouping)
    line_id: int
    image_id: str
    receipt_id: int


def _build_receipt_window_examples(
    words: List[WordInfo],
    receipt_key: str,
    window_size: int = 200,
    stride: int = 150,
) -> List[LineExample]:
    """Build sliding-window examples spanning a whole receipt with per-token BIO tags.

    Replaces per-spatial-block examples (which yield 1-5-word single-entity-type
    windows the model can't recognize at inference) with realistic per-receipt
    sequences containing entities interleaved with "O" tokens — matching the
    distribution the Swift worker actually feeds at inference.

    BIO logic: walk words in reading order. A word starts a new entity (B-)
    when its merged label is non-O AND differs from the previous word's
    original_label OR the previous word was unlabeled. Same-label runs in
    reading order continue with I-.

    Args:
        words: All words in a receipt (any order).
        receipt_key: Receipt identifier for the example.
        window_size: Maximum words per example.
        stride: Step between window starts (window_size - stride = overlap).

    Returns:
        One LineExample per window (or just one if the receipt fits).

    Raises:
        ValueError: if ``window_size`` or ``stride`` is not a positive int.
    """
    if window_size <= 0:
        raise ValueError(f"window_size must be positive, got {window_size}")
    if stride <= 0:
        raise ValueError(f"stride must be positive, got {stride}")
    if not words:
        return []

    # Reading order: top-to-bottom, left-to-right
    sorted_words = sorted(words, key=lambda w: (w.bbox[1], w.bbox[0]))

    # Assign BIO tags for the full receipt sequence
    full_tags: List[str] = []
    prev_orig = "O"
    for w in sorted_words:
        if w.label == "O":
            full_tags.append("O")
            prev_orig = "O"
        elif w.original_label == prev_orig and prev_orig != "O":
            full_tags.append("I-" + w.label)
        else:
            full_tags.append("B-" + w.label)
            prev_orig = w.original_label

    full_tokens = [w.text for w in sorted_words]
    full_bboxes = [w.bbox for w in sorted_words]
    n = len(sorted_words)
    first = sorted_words[0]

    if n <= window_size:
        return [
            LineExample(
                image_id=first.image_id,
                receipt_id=first.receipt_id,
                line_id=first.line_id,
                tokens=full_tokens,
                bboxes=full_bboxes,
                ner_tags=full_tags,
                receipt_key=receipt_key,
            )
        ]

    examples: List[LineExample] = []
    start = 0
    while start < n:
        end = min(start + window_size, n)
        # If a window starts mid-entity, promote the leading I- to B-
        win_tags = list(full_tags[start:end])
        if win_tags and win_tags[0].startswith("I-"):
            win_tags[0] = "B-" + win_tags[0][2:]
        examples.append(
            LineExample(
                image_id=first.image_id,
                receipt_id=first.receipt_id,
                line_id=first.line_id,
                tokens=full_tokens[start:end],
                bboxes=full_bboxes[start:end],
                ner_tags=win_tags,
                receipt_key=receipt_key,
            )
        )
        if end >= n:
            break
        start += stride
    return examples


def _box_from_word(word) -> Tuple[float, float, float, float]:
    xs = [
        word.top_left["x"],
        word.top_right["x"],
        word.bottom_left["x"],
        word.bottom_right["x"],
    ]
    ys = [
        word.top_left["y"],
        word.top_right["y"],
        word.bottom_left["y"],
        word.bottom_right["y"],
    ]
    return min(xs), min(ys), max(xs), max(ys)


def _normalize_box_from_extents(
    x0: float, y0: float, x1: float, y1: float, max_x: float, max_y: float
) -> List[int]:
    # Normalize coordinates to 0..1000 based on per-image maxima when available.
    def _scale(v: float, denom: float) -> int:
        if denom and denom > 1.0:
            val = int(round((v / denom) * 1000))
        else:
            # If values already look normalized (<= 1.0), scale directly
            val = int(round(v * 1000))
        # Clamp to [0, 1000]
        if val < 0:
            return 0
        if val > 1000:
            return 1000
        return val

    nx0 = _scale(x0, max_x)
    ny0 = _scale(y0, max_y)
    nx1 = _scale(x1, max_x)
    ny1 = _scale(y1, max_y)
    # Ensure proper ordering after rounding/clamping
    nx0, nx1 = sorted((nx0, nx1))
    ny0, ny1 = sorted((ny0, ny1))
    return [nx0, ny0, nx1, ny1]


_CORE_SET = set(CORE_LABELS.keys())


def _build_merge_lookup(
    label_merges: Optional[Dict[str, List[str]]],
) -> Dict[str, str]:
    """Build reverse lookup: source_label -> target_label.

    Args:
        label_merges: Dict mapping target labels to lists of source labels.
            E.g., {"AMOUNT": ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]}

    Returns:
        Dict mapping each source label to its target.
            E.g., {"LINE_TOTAL": "AMOUNT", "SUBTOTAL": "AMOUNT", ...}
    """
    lookup: Dict[str, str] = {}
    if not label_merges:
        return lookup

    for target, sources in label_merges.items():
        target_upper = target.upper()
        for source in sources:
            lookup[source.upper()] = target_upper

    return lookup


def _normalize_word_label(
    raw: str,
    allowed: Optional[set[str]] = None,
    merge_lookup: Optional[Dict[str, str]] = None,
) -> str:
    """Normalize a raw label, applying merges and filtering.

    Args:
        raw: Raw label string from DynamoDB.
        allowed: Optional set of allowed labels. Others map to "O".
        merge_lookup: Dict mapping source labels to target labels.

    Returns:
        Normalized label string.
    """
    lab = (raw or "").upper()
    if lab == "O":
        return "O"

    # Apply merge lookup if provided
    if merge_lookup and lab in merge_lookup:
        lab = merge_lookup[lab]

    # Filter to allowed labels
    if allowed is not None and lab not in allowed:
        return "O"

    # Allow core labels plus any target labels from merges
    valid_labels = _CORE_SET.copy()
    if merge_lookup:
        valid_labels.update(merge_lookup.values())

    return lab if lab in valid_labels else "O"


def _raw_label(
    labels: List[str],
    allowed: Optional[set[str]] = None,
    merge_lookup: Optional[Dict[str, str]] = None,
) -> str:
    """Pick first label if present; else 'O'. Normalize to valid set or 'O'."""
    if not labels:
        return "O"
    return _normalize_word_label(labels[0], allowed, merge_lookup)


def _get_original_and_merged_labels(
    labels: List[str],
    allowed: Optional[set[str]] = None,
    merge_lookup: Optional[Dict[str, str]] = None,
) -> Tuple[str, str]:
    """Get both original (pre-merge) and merged labels for a word.

    Args:
        labels: List of label strings from DynamoDB.
        allowed: Optional set of allowed labels. Others map to "O".
        merge_lookup: Dict mapping source labels to target labels.

    Returns:
        Tuple of (original_label, merged_label).
        - original_label: Label before merging (for spatial grouping)
        - merged_label: Label after merging (for BIO tagging)
    """
    if not labels:
        return "O", "O"

    raw = (labels[0] or "").upper()
    if raw == "O":
        return "O", "O"

    # Get original label (before merge, but after allowed filtering)
    original = raw

    # Apply merge lookup to get merged label
    merged = raw
    if merge_lookup and raw in merge_lookup:
        merged = merge_lookup[raw]

    # Filter to allowed labels - apply to merged label
    if allowed is not None and merged not in allowed:
        return "O", "O"

    # Validate against core labels + merge targets
    valid_labels = _CORE_SET.copy()
    if merge_lookup:
        valid_labels.update(merge_lookup.values())

    if merged not in valid_labels:
        return "O", "O"

    # If original was merged, keep original for grouping
    # If original was filtered out, both are O
    return original, merged


@dataclass
class MergeInfo:
    """Information about label merging applied during dataset loading."""

    label_merges: Optional[Dict[str, List[str]]]
    merge_lookup: Dict[str, str]
    resulting_labels: List[str]


def download_receipt_images(
    dynamo: DynamoClient,
    receipt_keys: set[tuple[str, int]],
    cache_dir: str = "/tmp/receipt_images",
) -> str:
    """Download warped receipt images from S3 for v3 training.

    Uses Receipt.raw_s3_key (the perspective-corrected crop) rather than
    Image.raw_s3_key (the original photo), since bounding boxes in the
    training data are in the warped receipt's coordinate space.
    """
    import logging

    import boto3

    logger = logging.getLogger(__name__)
    os.makedirs(cache_dir, exist_ok=True)
    s3 = boto3.client("s3")
    downloaded = 0
    skipped = 0
    for image_id, receipt_id in receipt_keys:
        local_path = os.path.join(cache_dir, f"{image_id}_{receipt_id}.png")
        if os.path.exists(local_path):
            skipped += 1
            continue
        try:
            receipt = dynamo.get_receipt(image_id, receipt_id)
            if receipt and receipt.raw_s3_bucket and receipt.raw_s3_key:
                s3.download_file(receipt.raw_s3_bucket, receipt.raw_s3_key, local_path)
                downloaded += 1
            else:
                logger.warning(
                    "Receipt %s/%d has no S3 location, will use placeholder",
                    image_id,
                    receipt_id,
                )
        except Exception as e:
            logger.warning(
                "Failed to download receipt image %s/%d: %s",
                image_id,
                receipt_id,
                e,
            )
    logger.info(
        "Receipt images: %d downloaded, %d cached, %d total",
        downloaded,
        skipped,
        len(receipt_keys),
    )
    return cache_dir


def _load_fixed_val_keys() -> Optional[set]:
    """Load a PINNED canonical val set from ``LAYOUTLM_VAL_KEYS_S3``.

    The env var points at a JSON file (``{"val_receipt_keys": [...]}`` or a bare
    list) of ``"<image_id>#<receipt_id:05d>"`` keys. When set, every run holds
    out exactly these receipts (those present in its data) and trains on the
    rest — making runs directly comparable instead of each drawing its own
    random split. Returns None when unset (fall back to the seeded split).
    """
    uri = os.getenv("LAYOUTLM_VAL_KEYS_S3")
    if not uri:
        return None
    import json
    from urllib.parse import urlparse

    boto3 = importlib.import_module("boto3")
    p = urlparse(uri)
    obj = boto3.client("s3").get_object(Bucket=p.netloc, Key=p.path.lstrip("/"))
    data = json.loads(obj["Body"].read().decode("utf-8"))
    keys = data.get("val_receipt_keys") if isinstance(data, dict) else data
    return set(keys) if keys else None


def _load_receipt_allowlist() -> Optional[set]:
    """Load a curated receipt allowlist from ``LAYOUTLM_RECEIPT_ALLOWLIST_S3``.

    When set, training/eval is restricted to ONLY these receipts (used for the
    scoped line-item model, which trains on a hand-curated, arithmetic-consistent
    subset rather than the full corpus). JSON: ``{"receipt_keys": [...]}`` or a
    bare list of ``"<image_id>#<receipt_id:05d>"`` keys. Returns None when unset.
    """
    uri = os.getenv("LAYOUTLM_RECEIPT_ALLOWLIST_S3")
    if not uri:
        return None
    import json
    from urllib.parse import urlparse

    boto3 = importlib.import_module("boto3")
    p = urlparse(uri)
    obj = boto3.client("s3").get_object(Bucket=p.netloc, Key=p.path.lstrip("/"))
    data = json.loads(obj["Body"].read().decode("utf-8"))
    keys = data.get("receipt_keys") if isinstance(data, dict) else data
    # When the env var IS set we always return a set (possibly empty), so an
    # empty/failed curation filters to nothing instead of silently falling back
    # to the full corpus. None is reserved for "env var unset".
    return set(keys or [])


def _read_json_source(source: str) -> Any:
    """Read JSON from a local path, local directory, S3 object, or S3 prefix."""
    if source.startswith("s3://"):
        boto3 = importlib.import_module("boto3")
        parsed = urlparse(source)
        client = boto3.client("s3")
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        if key.endswith("/") or not os.path.splitext(key)[1]:
            payloads = []
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=key):
                for obj in sorted(
                    page.get("Contents", []),
                    key=lambda item: item.get("Key", ""),
                ):
                    obj_key = obj.get("Key", "")
                    if not obj_key.endswith(".json"):
                        continue
                    payloads.append(_read_s3_json_object(client, bucket, obj_key))
            return payloads
        return _read_s3_json_object(client, bucket, key)

    path = os.path.abspath(source)
    if os.path.isdir(path):
        payloads = []
        for name in sorted(os.listdir(path)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(path, name), encoding="utf-8") as handle:
                payloads.append(json.load(handle))
        return payloads

    with open(source, encoding="utf-8") as handle:
        return json.load(handle)


def _read_s3_json_object(client: Any, bucket: str, key: str) -> Any:
    obj = client.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read().decode("utf-8"))


def _artifact_synthesis_readiness(
    payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
    profile = payload.get("merchant_receipt_parameterization")
    if not isinstance(profile, dict):
        return None
    readiness = profile.get("synthesis_readiness")
    if not isinstance(readiness, dict):
        return None
    status = str(readiness.get("status") or "").strip()
    if not status:
        return None
    return {
        "status": status,
        "score": readiness.get("score"),
        "blockers": list(readiness.get("blockers") or [])[:10],
    }


def _with_artifact_context(
    rows: List[dict],
    payload: dict[str, Any],
) -> List[dict]:
    readiness = _artifact_synthesis_readiness(payload)
    merchant_name = payload.get("merchant_name")
    if not readiness and not merchant_name:
        return rows

    enriched: List[dict] = []
    for row in rows:
        next_row = dict(row)
        metadata = row.get("metadata")
        next_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if readiness:
            next_metadata.setdefault("artifact_synthesis_readiness", readiness)
        if next_metadata:
            next_row["metadata"] = next_metadata
        if merchant_name and not next_row.get("merchant_name"):
            next_row["merchant_name"] = merchant_name
        enriched.append(next_row)
    return enriched


def _candidate_rows(payload: Any) -> List[dict]:
    """Extract candidate rows from supported synthetic artifact shapes."""
    if isinstance(payload, list):
        rows: List[dict] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            if any(key in item for key in SYNTHETIC_CANDIDATE_CONTAINER_KEYS):
                rows.extend(_candidate_rows(item))
            else:
                rows.append(item)
        return rows
    if not isinstance(payload, dict):
        return []
    for key in SYNTHETIC_CANDIDATE_COLLECTION_KEYS:
        rows = payload.get(key)
        if isinstance(rows, list):
            candidate_rows = [row for row in rows if isinstance(row, dict)]
            if key == "synthetic_receipt_candidates":
                return _with_artifact_context(candidate_rows, payload)
            return candidate_rows
    patterns = payload.get("line_item_patterns")
    if isinstance(patterns, dict):
        return _candidate_rows(patterns)
    return []


def _embedded_synthesis_quality_report(
    payload: Any,
) -> Optional[dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    for key in ("synthesis_quality_report", "quality_report"):
        report = payload.get(key)
        if isinstance(report, dict):
            return report
    return None


def _synthetic_bundle_readiness_failure(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None

    report = _embedded_synthesis_quality_report(payload)
    if isinstance(report, dict):
        report_training_ready = report.get("training_ready")
        if report_training_ready is False:
            return "bundle_training_not_ready"
        if report_training_ready is True:
            return None
        if report.get("ready") is False:
            return "bundle_not_ready"
    payload_training_ready = payload.get("training_ready")
    if payload_training_ready is False:
        return "bundle_training_not_ready"
    if payload_training_ready is True:
        return None
    if payload.get("ready") is False:
        return "bundle_not_ready"
    return None


def _candidate_rows_with_bundle_readiness(
    payload: Any,
) -> Tuple[List[dict[str, Any]], List[dict[str, Any]], Dict[str, int]]:
    """Extract rows while enforcing embedded bundle-level training holds."""
    if isinstance(payload, list):
        rows: List[dict[str, Any]] = []
        rejected_rows: List[dict[str, Any]] = []
        rejection_reasons: Dict[str, int] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            if any(key in item for key in SYNTHETIC_CANDIDATE_CONTAINER_KEYS):
                item_rows, item_rejected_rows, item_reasons = (
                    _candidate_rows_with_bundle_readiness(item)
                )
                rows.extend(item_rows)
                rejected_rows.extend(item_rejected_rows)
                for reason, count in item_reasons.items():
                    rejection_reasons[reason] = (
                        rejection_reasons.get(reason, 0) + count
                    )
            else:
                rows.append(item)
        return rows, rejected_rows, rejection_reasons

    if not isinstance(payload, dict):
        return [], [], {}

    rows = _candidate_rows(payload)
    reason = _synthetic_bundle_readiness_failure(payload)
    if not reason:
        return rows, [], {}

    return (
        [],
        [
            _synthetic_rejection_record(row, idx=idx, reason=reason)
            for idx, row in enumerate(rows)
        ],
        {reason: len(rows)} if rows else {},
    )


def _valid_box(box: Any) -> bool:
    """Return True when a bbox is a non-degenerate four-int LayoutLM box.

    Beyond shape and the 0-1000 coordinate range, the box must be properly
    ordered: ``x0 < x1`` and ``y0 < y1``. Inverted or zero-area boxes such as
    ``[124, 729, 170, 723]`` (``y0 > y1``) are corrupted geometry. The
    generator's own integrity checker rejects them, but this is the loader's
    defense-in-depth: a degenerate box must never reach training even if a
    row's declared ``layout_integrity`` is missing, wrong, or authored outside
    the synthesis pipeline.
    """
    if not (
        isinstance(box, list)
        and len(box) == 4
        and all(isinstance(coord, int) and 0 <= coord <= 1000 for coord in box)
    ):
        return False
    x0, y0, x1, y1 = box
    return x0 < x1 and y0 < y1


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_ratio(numerator: Any, denominator: Any) -> Optional[float]:
    top = _safe_int(numerator)
    bottom = _safe_int(denominator)
    if bottom is None:
        return None
    if bottom <= 0:
        return 0.0
    return round((top or 0) / bottom, 3)


def _synthetic_quality_gate_enabled() -> bool:
    return os.getenv("LAYOUTLM_SYNTHETIC_QUALITY_GATE", "1") not in {
        "0",
        "false",
        "False",
    }


def _synthetic_structure_threshold() -> float:
    value = _safe_float(os.getenv("LAYOUTLM_SYNTHETIC_MIN_STRUCTURE_SIMILARITY"))
    if value is None:
        return 0.60
    return max(0.0, min(1.0, value))


def _synthetic_max_examples_per_merchant() -> int:
    value = _safe_int(os.getenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT"))
    return max(1, value if value is not None else 5)


def _synthetic_max_examples_per_merchant_operation() -> int:
    value = _safe_int(os.getenv("LAYOUTLM_SYNTHETIC_MAX_PER_MERCHANT_OPERATION"))
    return max(1, value if value is not None else 2)


def _synthetic_row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _synthetic_merchant_key(row: dict[str, Any]) -> str:
    metadata = _synthetic_row_metadata(row)
    merchant = row.get("merchant_name")
    if not merchant:
        profile = metadata.get("profile")
        if isinstance(profile, dict):
            merchant = profile.get("merchant_name")
    return str(merchant or "unknown").strip().lower() or "unknown"


def _synthetic_merchant_name(row: dict[str, Any]) -> str:
    metadata = _synthetic_row_metadata(row)
    merchant = row.get("merchant_name")
    if not merchant:
        profile = metadata.get("profile")
        if isinstance(profile, dict):
            merchant = profile.get("merchant_name")
    return str(merchant or "unknown").strip() or "unknown"


def _synthetic_operation(row: dict[str, Any]) -> str:
    metadata = _synthetic_row_metadata(row)
    return str(metadata.get("operation") or row.get("operation") or "unknown")


def _count_synthetic_values(values: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _normalized_entropy(counts: Dict[str, int]) -> Optional[float]:
    positive = [count for count in counts.values() if count > 0]
    total = sum(positive)
    if total <= 0:
        return None
    if len(positive) <= 1:
        return 0.0
    entropy = -sum((count / total) * math.log(count / total) for count in positive)
    return round(entropy / math.log(len(positive)), 3)


def _top_count_share(counts: Dict[str, int]) -> tuple[str | None, int, float | None]:
    positive = {
        str(key): count
        for key, count in counts.items()
        if isinstance(count, int) and count > 0
    }
    total = sum(positive.values())
    if total <= 0:
        return None, 0, None
    key, count = max(positive.items(), key=lambda item: (item[1], item[0]))
    return key, count, round(count / total, 3)


def _synthetic_balance_risk(
    *,
    accepted_count: int,
    top_merchant_share: float | None,
    top_operation_share: float | None,
    merchant_count: int,
    operation_count: int,
) -> tuple[str, list[str]]:
    if accepted_count <= 0:
        return "none", ["no_accepted_synthetic_examples"]
    if accepted_count < 3:
        return "low", ["too_few_examples_for_balance_assessment"]

    level = "low"
    reasons: list[str] = []
    if merchant_count <= 1:
        level = "high"
        reasons.append("single_merchant_accepted")
    elif top_merchant_share is not None and top_merchant_share >= 0.80:
        level = "high"
        reasons.append("top_merchant_share_ge_80pct")
    elif top_merchant_share is not None and top_merchant_share >= 0.67:
        level = "medium"
        reasons.append("top_merchant_share_ge_67pct")

    if operation_count <= 1:
        if level != "high":
            level = "medium"
        reasons.append("single_operation_accepted")
    elif top_operation_share is not None and top_operation_share >= 0.80:
        if level != "high":
            level = "medium"
        reasons.append("top_operation_share_ge_80pct")
    elif top_operation_share is not None and top_operation_share >= 0.67:
        if level == "low":
            level = "medium"
        reasons.append("top_operation_share_ge_67pct")

    return level, reasons


def _synthetic_mix_balance(rows: List[dict[str, Any]]) -> Dict[str, Any]:
    merchant_counts = _count_synthetic_values(
        [_synthetic_merchant_name(row) for row in rows]
    )
    operation_counts = _count_synthetic_values(
        [_synthetic_operation(row) for row in rows]
    )
    accepted_count = len(rows)
    top_merchant, top_merchant_count, top_merchant_share = _top_count_share(
        merchant_counts
    )
    top_operation, top_operation_count, top_operation_share = _top_count_share(
        operation_counts
    )
    risk_level, risk_reasons = _synthetic_balance_risk(
        accepted_count=accepted_count,
        top_merchant_share=top_merchant_share,
        top_operation_share=top_operation_share,
        merchant_count=len(merchant_counts),
        operation_count=len(operation_counts),
    )
    return {
        "accepted_count": accepted_count,
        "merchant_count": len(merchant_counts),
        "operation_count": len(operation_counts),
        "top_merchant": top_merchant,
        "top_merchant_count": top_merchant_count,
        "top_merchant_share": top_merchant_share,
        "top_operation": top_operation,
        "top_operation_count": top_operation_count,
        "top_operation_share": top_operation_share,
        "merchant_entropy": _normalized_entropy(merchant_counts),
        "operation_entropy": _normalized_entropy(operation_counts),
        "risk_level": risk_level,
        "risk_reasons": risk_reasons,
    }


def _synthetic_field_replacement_label(row: dict[str, Any]) -> Optional[str]:
    replacement = _synthetic_row_metadata(row).get("field_replacement")
    if not isinstance(replacement, dict):
        return None
    label = str(replacement.get("label") or "").strip().upper()
    return label or None


def _merchant_synthesis_contract_rows(payload: Any) -> List[dict[str, Any]]:
    if isinstance(payload, list):
        rows: List[dict[str, Any]] = []
        for item in payload:
            rows.extend(_merchant_synthesis_contract_rows(item))
        return rows
    if not isinstance(payload, dict):
        return []

    rows = [
        row
        for row in payload.get("merchant_synthesis_contracts") or []
        if isinstance(row, dict)
    ]
    for key in ("bundle", "line_item_patterns"):
        nested = payload.get(key)
        if isinstance(nested, (dict, list)):
            rows.extend(_merchant_synthesis_contract_rows(nested))
    return rows


def _merchant_synthesis_contracts_by_merchant(
    payload: Any,
) -> dict[str, dict[str, Any]]:
    contracts: dict[str, dict[str, Any]] = {}
    for contract in _merchant_synthesis_contract_rows(payload):
        merchant = str(contract.get("merchant_name") or "").strip().lower()
        if merchant:
            contracts[merchant] = contract
    return contracts


def _contract_operation_contract(
    contract: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    operation_contracts = contract.get("operation_contracts")
    if not isinstance(operation_contracts, dict):
        return {}
    operation_contract = operation_contracts.get(operation)
    return operation_contract if isinstance(operation_contract, dict) else {}


def _contract_operation_ready(
    contract: dict[str, Any],
    operation: str,
) -> bool:
    supported_operations = {
        str(value).strip()
        for value in contract.get("supported_operations") or []
        if str(value).strip()
    }
    operation_contract = _contract_operation_contract(contract, operation)
    if operation_contract:
        ready = operation_contract.get("ready")
        if ready is True:
            return True
        if ready is False:
            return False
    return operation in supported_operations


def _synthetic_accepted_operation_coverage(
    rows: List[dict[str, Any]],
    merchant_contracts: Optional[dict[str, dict[str, Any]]],
) -> Dict[str, Any]:
    """Summarize whether accepted rows cover contract-ready operations."""
    if not merchant_contracts:
        return {}

    merchant_names: Dict[str, str] = {}
    accepted_counts: Dict[tuple[str, str], int] = {}
    for row in rows:
        merchant_key = _synthetic_merchant_key(row)
        operation = _synthetic_operation(row).strip()
        if not operation:
            continue
        merchant_names.setdefault(merchant_key, _synthetic_merchant_name(row))
        key = (merchant_key, operation)
        accepted_counts[key] = accepted_counts.get(key, 0) + 1

    for merchant_key, contract in merchant_contracts.items():
        merchant_name = str(contract.get("merchant_name") or merchant_key).strip()
        merchant_names[merchant_key] = merchant_name or merchant_key

    operations: Dict[str, Any] = {}
    uncovered_ready_operations: List[str] = []
    ready_operation_count = 0
    accepted_operation_count = 0
    accepted_ready_operation_count = 0

    def merchant_label(merchant_key: str) -> str:
        return merchant_names.get(merchant_key) or merchant_key

    for operation in SYNTHESIS_OPERATION_FAMILIES:
        ready_keys = [
            merchant_key
            for merchant_key, contract in merchant_contracts.items()
            if str(contract.get("status") or "").strip().lower() == "ready"
            and _contract_operation_ready(contract, operation)
        ]
        accepted_keys = [
            merchant_key
            for merchant_key, accepted_operation in accepted_counts
            if accepted_operation == operation
            and accepted_counts[(merchant_key, accepted_operation)] > 0
        ]
        ready_key_set = set(ready_keys)
        accepted_key_set = set(accepted_keys)
        ready_accepted_keys = sorted(
            ready_key_set.intersection(accepted_key_set),
            key=merchant_label,
        )
        uncovered_ready_keys = sorted(
            ready_key_set.difference(accepted_key_set),
            key=merchant_label,
        )
        accepted_keys = sorted(set(accepted_keys), key=merchant_label)
        accepted_count = sum(
            count
            for (merchant_key, accepted_operation), count in accepted_counts.items()
            if accepted_operation == operation and count > 0
        )

        if ready_keys:
            ready_operation_count += 1
        if accepted_count:
            accepted_operation_count += 1
        if ready_keys and ready_accepted_keys:
            accepted_ready_operation_count += 1
        if ready_keys and not ready_accepted_keys:
            uncovered_ready_operations.append(operation)

        operations[operation] = {
            "ready_merchant_count": len(ready_keys),
            "accepted_merchant_count": len(accepted_keys),
            "accepted_ready_merchant_count": len(ready_accepted_keys),
            "accepted_count": accepted_count,
            "ready_acceptance_share": (
                _safe_ratio(len(ready_accepted_keys), len(ready_keys))
                if ready_keys
                else None
            ),
            "ready_merchants": [merchant_label(key) for key in ready_keys[:8]],
            "accepted_merchants": [merchant_label(key) for key in accepted_keys[:8]],
            "uncovered_ready_merchants": [
                merchant_label(key) for key in uncovered_ready_keys[:8]
            ],
        }

    return {
        "operation_count": len(SYNTHESIS_OPERATION_FAMILIES),
        "ready_operation_count": ready_operation_count,
        "accepted_operation_count": accepted_operation_count,
        "accepted_ready_operation_count": accepted_ready_operation_count,
        "accepted_ready_operation_share": _safe_ratio(
            accepted_ready_operation_count,
            ready_operation_count,
        ),
        "uncovered_ready_operations": uncovered_ready_operations,
        "operations": operations,
        "recommendations": (
            ["cover_ready_operations_before_training"]
            if uncovered_ready_operations
            else []
        ),
    }


def _contract_field_for_label(
    fields: Any,
    label: str,
) -> Optional[dict[str, Any]]:
    if not isinstance(fields, dict):
        return None
    for key, field in fields.items():
        if not isinstance(field, dict):
            continue
        if str(key).strip().upper() == label:
            return field
        if str(field.get("label") or "").strip().upper() == label:
            return field
    return None


def _synthetic_contract_quality_failure(
    row: dict[str, Any],
    *,
    merchant_contracts: Optional[dict[str, dict[str, Any]]] = None,
) -> Optional[str]:
    """Return a contract-level rejection reason when contracts are present."""
    if not merchant_contracts:
        return None

    contract = merchant_contracts.get(_synthetic_merchant_key(row))
    if not contract:
        return "missing_merchant_synthesis_contract"

    status = str(contract.get("status") or "").strip().lower()
    if status != "ready":
        return "merchant_contract_not_ready"

    operation = _synthetic_operation(row).strip()
    if not _contract_operation_ready(contract, operation):
        return "operation_not_supported_by_contract"

    if operation != "replace_field":
        return None

    metadata = _synthetic_row_metadata(row)
    replacement = metadata.get("field_replacement")
    label = (
        str(replacement.get("label") or "").strip().upper()
        if isinstance(replacement, dict)
        else ""
    )
    operation_contract = _contract_operation_contract(contract, operation)
    field_contract = _contract_field_for_label(
        operation_contract.get("fields"),
        label,
    )
    if not label or not field_contract:
        return "replace_field_not_contract_safe"
    if field_contract.get("safe_to_mutate") is not True:
        return "replace_field_not_contract_safe"
    if field_contract.get("stable_geometry") is False:
        return "replace_field_not_contract_safe"

    stable_format = field_contract.get("stable_format")
    replacement_format = (
        replacement.get("format") if isinstance(replacement, dict) else None
    )
    if stable_format and replacement_format and stable_format != replacement_format:
        return "replace_field_contract_format_mismatch"

    # A value-scrub field only changes digits in place (mask/length/box
    # preserved), so its format is self-evident from a single observation; the
    # multiple-observed-values rule that DATE/TIME need to prove a stable format
    # does not apply.
    if stable_format != "value_scrub":
        observed_count = _safe_int(field_contract.get("observed_count"))
        if observed_count is not None and observed_count < 2:
            return "replace_field_not_contract_safe"
    return None


def _synthetic_category(row: dict[str, Any]) -> Optional[str]:
    metadata = _synthetic_row_metadata(row)
    for item_key in ("added_item", "removed_item"):
        item = metadata.get(item_key)
        if isinstance(item, dict):
            category = str(item.get("category") or "").strip()
            if category:
                return category
    observed = metadata.get("observed_item_evidence")
    if isinstance(observed, dict):
        category = str(observed.get("category") or "").strip()
        if category:
            return category
    return None


def _synthetic_structure_score(row: dict[str, Any]) -> Optional[float]:
    structure = _synthetic_row_metadata(row).get("structure_similarity")
    if not isinstance(structure, dict):
        return None
    return _safe_float(structure.get("score"))


def _synthetic_score_summary(scores: List[float]) -> Dict[str, Any]:
    if not scores:
        return {"count": 0}
    return {
        "count": len(scores),
        "avg": round(sum(scores) / len(scores), 3),
        "min": round(min(scores), 3),
        "max": round(max(scores), 3),
    }


def _synthetic_structure_components(row: dict[str, Any]) -> Dict[str, float]:
    structure = _synthetic_row_metadata(row).get("structure_similarity")
    if not isinstance(structure, dict):
        return {}
    components = structure.get("components")
    if not isinstance(components, dict):
        return {}
    result: Dict[str, float] = {}
    for key, raw_value in components.items():
        value = _safe_float(raw_value)
        if value is not None:
            result[str(key)] = value
    return result


def _synthetic_real_baseline_comparison(
    row: dict[str, Any],
) -> Optional[Dict[str, Any]]:
    structure = _synthetic_row_metadata(row).get("structure_similarity")
    if not isinstance(structure, dict):
        return None
    baseline = structure.get("real_baseline_comparison")
    return baseline if isinstance(baseline, dict) else None


def _synthetic_real_baseline_min_pair_count() -> int:
    raw = os.environ.get("LAYOUTLM_SYNTHETIC_MIN_REAL_BASELINE_PAIRS")
    if raw is None:
        return 3
    try:
        return max(0, int(raw))
    except ValueError:
        return 3


def _synthetic_real_baseline_failure(row: dict[str, Any]) -> Optional[str]:
    """Return a rejection reason when candidate geometry falls outside real range."""
    comparison = _synthetic_real_baseline_comparison(row)
    if not comparison:
        return None

    pair_count = _safe_int(comparison.get("baseline_pair_count"))
    if (
        pair_count is None
        or pair_count < _synthetic_real_baseline_min_pair_count()
    ):
        return None

    within_real_range = comparison.get("within_real_score_range")
    if within_real_range is False:
        return "below_real_structure_baseline"
    if within_real_range is True:
        return None

    candidate_score = _safe_float(comparison.get("candidate_score"))
    baseline_min = _safe_float(comparison.get("baseline_min"))
    if (
        candidate_score is not None
        and baseline_min is not None
        and candidate_score < baseline_min
    ):
        return "below_real_structure_baseline"
    return None


def _synthetic_real_baseline_summary(
    rows: List[dict[str, Any]],
) -> Dict[str, Any]:
    comparisons = [
        comparison
        for row in rows
        if (comparison := _synthetic_real_baseline_comparison(row))
    ]
    if not comparisons:
        return {"count": 0}

    within_count = sum(
        1 for comparison in comparisons if comparison.get("within_real_score_range")
    )

    def values_for(key: str) -> List[float]:
        return [
            value
            for comparison in comparisons
            if (value := _safe_float(comparison.get(key))) is not None
        ]

    return {
        "count": len(comparisons),
        "within_real_score_range_count": within_count,
        "below_real_score_range_count": len(comparisons) - within_count,
        "within_real_score_range_share": round(within_count / len(comparisons), 3),
        "candidate_score": _synthetic_score_summary(values_for("candidate_score")),
        "baseline_avg": _synthetic_score_summary(values_for("baseline_avg")),
        "baseline_min": _synthetic_score_summary(values_for("baseline_min")),
        "baseline_pair_count": _synthetic_score_summary(
            values_for("baseline_pair_count")
        ),
        "delta_from_avg": _synthetic_score_summary(values_for("delta_from_avg")),
        "delta_from_min": _synthetic_score_summary(values_for("delta_from_min")),
    }


def _synthetic_component_score_summary(
    rows: List[dict[str, Any]],
) -> Dict[str, Any]:
    values_by_component: Dict[str, List[float]] = {}
    for row in rows:
        for component, value in _synthetic_structure_components(row).items():
            values_by_component.setdefault(component, []).append(value)
    return {
        component: _synthetic_score_summary(values)
        for component, values in sorted(values_by_component.items())
    }


def _synthetic_structure_component_thresholds() -> Dict[str, float]:
    return {
        "price_column": 0.75,
        "line_step": 0.45,
        "category_sequence": 0.40,
        "category_set": 0.40,
        "token_count": 0.35,
    }


def _synthetic_structure_component_failure(
    structure: dict[str, Any],
) -> Optional[str]:
    components = structure.get("components")
    if not isinstance(components, dict) or not components:
        return None

    reasons = {
        "price_column": "low_price_column_similarity",
        "line_step": "low_line_step_similarity",
        "category_sequence": "low_category_sequence_similarity",
        "category_set": "low_category_set_similarity",
        "token_count": "low_token_count_similarity",
    }
    for key, threshold in _synthetic_structure_component_thresholds().items():
        value = _safe_float(components.get(key))
        if value is not None and value < threshold:
            return reasons[key]
    return None


def _synthetic_is_grounded(row: dict[str, Any]) -> bool:
    metadata = _synthetic_row_metadata(row)
    added = metadata.get("added_item")
    observed = metadata.get("observed_item_evidence")
    if isinstance(added, dict) and added.get("seen_in_other_receipt"):
        return True
    if isinstance(observed, dict) and observed.get("product_seen_outside_base"):
        return True
    return False


def _synthetic_has_arithmetic(row: dict[str, Any]) -> bool:
    arithmetic = _synthetic_row_metadata(row).get("arithmetic_reconciliation")
    return isinstance(arithmetic, dict) and bool(arithmetic)


def _synthetic_is_high_fidelity(row: dict[str, Any]) -> bool:
    quality = _synthetic_row_metadata(row).get("candidate_quality")
    return isinstance(quality, dict) and quality.get("high_fidelity") is True


def _synthetic_declared_candidate_quality(row: dict[str, Any]) -> Optional[float]:
    quality = _synthetic_row_metadata(row).get("candidate_quality")
    if not isinstance(quality, dict):
        return None
    score = _safe_float(quality.get("score"))
    if score is None:
        return None
    return max(0.0, min(1.0, score))


def _synthetic_declared_candidate_quality_components(
    row: dict[str, Any],
) -> Dict[str, float]:
    quality = _synthetic_row_metadata(row).get("candidate_quality")
    if not isinstance(quality, dict):
        return {}
    components = quality.get("components")
    if not isinstance(components, dict):
        return {}
    result: Dict[str, float] = {}
    for key, raw_value in components.items():
        value = _safe_float(raw_value)
        if value is not None:
            result[str(key)] = value
    return result


def _synthetic_candidate_quality_component_summary(
    rows: List[dict[str, Any]],
) -> Dict[str, Any]:
    values_by_component: Dict[str, List[float]] = {}
    for row in rows:
        for component, value in _synthetic_declared_candidate_quality_components(
            row
        ).items():
            values_by_component.setdefault(component, []).append(value)
    return {
        component: _synthetic_score_summary(values)
        for component, values in sorted(values_by_component.items())
    }


def _synthetic_candidate_quality_threshold() -> float:
    raw = os.environ.get("LAYOUTLM_SYNTHETIC_MIN_CANDIDATE_QUALITY")
    if raw is None:
        return 0.70
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.70


def _synthetic_rejection_record(
    row: dict[str, Any],
    *,
    idx: int,
    reason: str,
) -> dict[str, Any]:
    metadata = _synthetic_row_metadata(row)
    structure = metadata.get("structure_similarity")
    score = _safe_float(structure.get("score")) if isinstance(structure, dict) else None
    record = {
        "candidate_id": str(row.get("candidate_id") or ""),
        "receipt_key": str(row.get("receipt_key") or ""),
        "image_id": str(row.get("image_id") or ""),
        "merchant_name": _synthetic_merchant_name(row),
        "operation": _synthetic_operation(row),
        "reason": reason,
        "idx": idx,
    }
    category = _synthetic_category(row)
    if category:
        record["category"] = category
    if score is not None:
        record["structure_similarity"] = score
    if reason == "below_real_structure_baseline":
        baseline = _synthetic_real_baseline_comparison(row)
        if baseline:
            record["real_baseline_comparison"] = {
                key: baseline[key]
                for key in (
                    "candidate_score",
                    "baseline_min",
                    "baseline_avg",
                    "within_real_score_range",
                    "delta_from_min",
                    "delta_from_avg",
                )
                if key in baseline
            }
    quality_score = _synthetic_declared_candidate_quality(row)
    if quality_score is not None:
        record["candidate_quality"] = quality_score
    return record


def _synthetic_fidelity_score(row: dict[str, Any]) -> float:
    declared_quality = _synthetic_declared_candidate_quality(row)
    if declared_quality is not None:
        return declared_quality

    metadata = _synthetic_row_metadata(row)
    structure = metadata.get("structure_similarity")
    score = (
        _safe_float(structure.get("score")) if isinstance(structure, dict) else None
    ) or 0.0

    operation = _synthetic_operation(row)
    if operation in {"add_line_item", "remove_line_item"} and isinstance(
        metadata.get("arithmetic_reconciliation"),
        dict,
    ):
        score += 0.15
    if operation == "add_line_item":
        observed = metadata.get("observed_item_evidence")
        if isinstance(observed, dict) and observed.get("product_seen_outside_base"):
            score += 0.10
        if (
            isinstance(observed, dict)
            and observed.get("base_receipt_has_category") is True
        ):
            score += 0.05
        accuracy = metadata.get("synthesis_accuracy_evidence")
        if isinstance(accuracy, dict):
            catalog = accuracy.get("catalog_grounding")
            placement = accuracy.get("category_placement")
            if isinstance(catalog, dict):
                outside_count = _safe_int(
                    catalog.get("product_seen_outside_base_count")
                )
                if outside_count and outside_count > 0:
                    score += min(0.06, outside_count * 0.02)
                heading_count = _safe_int(catalog.get("category_heading_seen_count"))
                if heading_count and heading_count > 0:
                    score += 0.03
            if isinstance(placement, dict):
                if placement.get("category_alignment") == "same_category_as_base":
                    score += 0.04
                if placement.get("base_receipt_has_category") is True:
                    score += 0.02
    if operation == "hard_negative":
        score += 0.03
    if operation == "replace_field":
        evidence = metadata.get("mutable_field_evidence")
        if isinstance(evidence, dict) and evidence.get("safe_to_mutate") is True:
            score += 0.08
    return score


def _add_item_optional_evidence_failure(
    *,
    added: dict[str, Any],
    observed: dict[str, Any],
    metadata: dict[str, Any],
) -> Optional[str]:
    accuracy = metadata.get("synthesis_accuracy_evidence")
    if not isinstance(accuracy, dict):
        return None

    added_category = str(added.get("category") or "").strip()
    observed_category = str(observed.get("category") or "").strip()
    catalog = accuracy.get("catalog_grounding")
    if isinstance(catalog, dict):
        catalog_category = str(catalog.get("category") or "").strip()
        if catalog_category and added_category and catalog_category != added_category:
            return "add_item_catalog_category_mismatch"
        outside_count = _safe_int(catalog.get("product_seen_outside_base_count"))
        if outside_count is not None and outside_count <= 0:
            return "add_item_catalog_not_cross_receipt_grounded"
        catalog_seen_count = _safe_int(catalog.get("category_seen_count"))
        if catalog_seen_count is not None and catalog_seen_count <= 0:
            return "add_item_catalog_missing_category_evidence"

    placement = accuracy.get("category_placement")
    if isinstance(placement, dict):
        placement_category = str(placement.get("category") or "").strip()
        expected_category = observed_category or added_category
        if (
            placement_category
            and expected_category
            and placement_category != expected_category
        ):
            return "add_item_placement_category_mismatch"
        if placement.get("base_receipt_has_category") is False:
            return "add_item_placement_base_category_missing"
        alignment = placement.get("category_alignment")
        if alignment and alignment != "same_category_as_base":
            return "add_item_placement_category_mismatch"
    return None


def _synthetic_candidate_quality_failure(
    row: dict[str, Any],
    *,
    min_structure_similarity: Optional[float] = None,
) -> Optional[str]:
    """Return a rejection reason, or None when a candidate is trainable."""
    if not _synthetic_quality_gate_enabled():
        return None

    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return "missing_metadata"

    artifact_readiness = metadata.get("artifact_synthesis_readiness")
    if isinstance(artifact_readiness, dict):
        status = str(artifact_readiness.get("status") or "").strip().lower()
        if status and status not in {"ready", "partial"}:
            return "merchant_synthesis_not_ready"

    if not str(metadata.get("base_receipt_key") or "").strip():
        return "missing_base_receipt_lineage"

    structure = metadata.get("structure_similarity")
    if not isinstance(structure, dict):
        return "missing_structure_similarity"
    score = _safe_float(structure.get("score"))
    threshold = (
        max(0.0, min(1.0, min_structure_similarity))
        if min_structure_similarity is not None
        else _synthetic_structure_threshold()
    )
    if score is None or score < threshold:
        return "low_structure_similarity"
    if component_failure := _synthetic_structure_component_failure(structure):
        return component_failure
    if baseline_failure := _synthetic_real_baseline_failure(row):
        return baseline_failure

    operation = str(metadata.get("operation") or "").strip()

    # Hard geometry defects the generator already detected must fail on EVERY
    # load path, not only require_high_fidelity bundles. A layout_integrity score
    # below 1.0 means an overlap / off-canvas box / edit-introduced collision;
    # training on it teaches the model corrupted geometry regardless of how high
    # the declared candidate-quality score is.
    layout = metadata.get("layout_integrity")
    layout_score = (
        _safe_float(layout.get("score")) if isinstance(layout, dict) else None
    )
    if layout_score is not None and layout_score < 1.0:
        return "layout_integrity_failed"
    # Geometry-mutating operations must CARRY a layout_integrity score. Its
    # absence means the row was authored outside the synthesis pipeline (or by an
    # older generator) and its geometry was never integrity-checked — exactly the
    # case a weak box check would wave through. hard_negative is exempt: it swaps
    # token labels without moving any box, so its geometry equals the real base.
    if layout_score is None and operation != "hard_negative":
        return "missing_layout_integrity"

    declared_quality = _synthetic_declared_candidate_quality(row)
    if (
        declared_quality is not None
        and declared_quality < _synthetic_candidate_quality_threshold()
    ):
        return "low_candidate_quality"

    if operation == "hard_negative":
        if (
            str(metadata.get("error_kind") or "") == "false_positive"
            and str(metadata.get("actual_label") or "").upper() == "O"
            and bool(metadata.get("predicted_label"))
        ):
            return None
        return "invalid_hard_negative_metadata"

    if operation == "add_line_item":
        added = metadata.get("added_item")
        observed = metadata.get("observed_item_evidence")
        arithmetic = metadata.get("arithmetic_reconciliation")
        if not isinstance(added, dict) or not isinstance(observed, dict):
            return "add_item_missing_observed_evidence"
        # An item the generator flagged as inserted below the SUBTOTAL/TOTAL
        # block is invalid geometry; reject it on every path, not just strict.
        if added.get("insertion_position_valid") is False:
            return "insertion_position_invalid"
        if added.get("seen_in_other_receipt") is not True or not observed.get(
            "product_seen_outside_base"
        ):
            return "add_item_not_cross_receipt_grounded"
        added_category = str(added.get("category") or "").strip()
        observed_category = str(observed.get("category") or "").strip()
        category_seen_count = _safe_int(observed.get("category_seen_count"))
        category_seen_sources = observed.get("category_seen_in_receipts")
        if not added_category:
            return "add_item_missing_category_evidence"
        if observed_category and observed_category != added_category:
            return "add_item_category_mismatch"
        if observed.get("base_receipt_has_category") is not True:
            return "add_item_base_category_missing"
        if not (
            category_seen_count
            and category_seen_count > 0
            or isinstance(category_seen_sources, list)
            and category_seen_sources
        ):
            return "add_item_missing_category_evidence"
        if optional_failure := _add_item_optional_evidence_failure(
            added=added,
            observed=observed,
            metadata=metadata,
        ):
            return optional_failure
        if not isinstance(arithmetic, dict):
            return "missing_arithmetic_reconciliation"
        if (
            arithmetic.get("summary_update_policy") != "non_taxable_item_delta"
            or arithmetic.get("tax_delta") != "0.00"
        ):
            return "invalid_arithmetic_reconciliation"
        return None

    if operation == "remove_line_item":
        removed = metadata.get("removed_item")
        arithmetic = metadata.get("arithmetic_reconciliation")
        if not isinstance(removed, dict):
            return "remove_item_missing_evidence"
        if removed.get("taxable") is not False:
            return "remove_item_taxable_or_unknown"
        if not isinstance(arithmetic, dict):
            return "missing_arithmetic_reconciliation"
        if (
            arithmetic.get("summary_update_policy") != "non_taxable_item_delta"
            or arithmetic.get("tax_delta") != "0.00"
        ):
            return "invalid_arithmetic_reconciliation"
        return None

    if operation == "replace_field":
        replacement = metadata.get("field_replacement")
        evidence = metadata.get("mutable_field_evidence")
        if not isinstance(replacement, dict) or not isinstance(evidence, dict):
            return "replace_field_missing_evidence"
        label = str(replacement.get("label") or "").upper()

        # Value-scrub: privacy-safe in-place digit replacement of a single
        # sensitive identifier (masked PAN / loyalty / membership number). The
        # only thing allowed to change is the digits — same length, same mask
        # chars / separators / letters, same token, same box. Verified here
        # independently of the generator's claims.
        if isinstance(evidence, dict) and evidence.get("mutation_kind") == (
            "value_scrub"
        ):
            if label not in {"PAYMENT_METHOD", "LOYALTY_ID"}:
                return "replace_field_unsupported_label"
            if str(evidence.get("label") or "").upper() != label:
                return "replace_field_label_mismatch"
            if evidence.get("safe_to_mutate") is not True:
                return "replace_field_not_mutable"
            if evidence.get("stable_geometry") is not True:
                return "replace_field_unstable_geometry"
            if evidence.get("token_count_preserved") is not True:
                return "replace_field_token_count_changed"
            old_text = str(replacement.get("old_text") or "")
            new_text = str(replacement.get("new_text") or "")
            if not old_text or not new_text or old_text == new_text:
                return "replace_field_invalid_value"
            # ONLY digit characters may differ. Per-position check (not a digit->'#'
            # skeleton, which would treat a literal '#' mask char and a digit as
            # interchangeable): every non-digit position must be byte-identical
            # (mask char / separator / letter preserved) and every digit position
            # must stay a digit. Same length, and the value must actually change.
            if len(old_text) != len(new_text):
                return "replace_field_scrub_altered_structure"
            for old_ch, new_ch in zip(old_text, new_text):
                if old_ch.isdigit():
                    if not new_ch.isdigit():
                        return "replace_field_scrub_altered_structure"
                elif old_ch != new_ch:
                    return "replace_field_scrub_altered_structure"
            # Verify against the ACTUAL token sequence, not just the metadata's
            # claim: the scrubbed value must be present, the original must be
            # gone (privacy), and the scrubbed token must still carry the field
            # label. This closes the gap where metadata could assert a scrub the
            # tokens never received.
            tokens = row.get("tokens")
            tokens = tokens if isinstance(tokens, list) else []
            if new_text not in tokens:
                return "replace_field_scrub_not_applied"
            if old_text in tokens:
                return "replace_field_scrub_residual_original"
            ner_tags = row.get("ner_tags")
            ner_tags = ner_tags if isinstance(ner_tags, list) else []
            scrubbed_index = tokens.index(new_text)
            tag = (
                str(ner_tags[scrubbed_index]).upper()
                if scrubbed_index < len(ner_tags)
                else ""
            )
            if not tag.endswith(label):
                return "replace_field_scrub_label_lost"
            return None

        if label not in {"DATE", "TIME"}:
            return "replace_field_unsupported_label"
        if str(evidence.get("label") or "").upper() != label:
            return "replace_field_label_mismatch"
        if evidence.get("safe_to_mutate") is not True:
            return "replace_field_not_mutable"
        if evidence.get("stable_geometry") is not True:
            return "replace_field_unstable_geometry"
        if not evidence.get("stable_format"):
            return "replace_field_missing_format"
        observed_count = _safe_int(evidence.get("observed_count"))
        if observed_count is None or observed_count < 2:
            return "replace_field_insufficient_observations"
        old_text = str(replacement.get("old_text") or "")
        new_text = str(replacement.get("new_text") or "")
        if not old_text or not new_text or old_text == new_text:
            return "replace_field_invalid_value"
        if replacement.get("format") != evidence.get("stable_format"):
            return "replace_field_format_mismatch"
        return None

    if operation == "compose_online_catalog":
        grounding = metadata.get("online_catalog_grounding")
        label_control = metadata.get("label_control")
        arithmetic = metadata.get("arithmetic_reconciliation")
        if not isinstance(grounding, dict) or not isinstance(
            label_control, dict
        ):
            return "compose_missing_evidence"
        # Every rendered row must have a real name and a real price.
        if not (grounding.get("all_priced") and grounding.get("all_named")):
            return "compose_not_grounded"
        # The defining guarantee of template fill: every item-region token
        # carries the label we assigned (no inherited label noise).
        if label_control.get("all_correct") is not True:
            return "compose_item_labels_uncontrolled"
        if not isinstance(arithmetic, dict):
            return "missing_arithmetic_reconciliation"
        # Internally consistent totals with tax recomputed at a stable observed
        # rate (a composed receipt's own tax, not an edit to a real tax value).
        if (
            arithmetic.get("summary_update_policy") != "composed_catalog_totals"
            or arithmetic.get("subtotal_consistent") is not True
            or arithmetic.get("tax_rate_stable") is not True
        ):
            return "invalid_arithmetic_reconciliation"
        return None

    if operation == "compose_store_header":
        swap = metadata.get("store_header_swap")
        if not isinstance(swap, dict):
            return "store_header_missing_evidence"
        source_place = str(swap.get("source_place_id") or "").strip()
        own_place = str(swap.get("own_place_id") or "").strip()
        if not source_place:
            return "store_header_missing_source_place"
        # The whole point is a DIFFERENT branch: swapping in a receipt's own
        # store gives no location diversity.
        if source_place == own_place:
            return "store_header_same_place"
        if swap.get("merchant_match") is not True:
            return "store_header_merchant_mismatch"
        # Coherence: every swapped field came from the one source place. A header
        # mixing two stores' fields is the incoherent receipt we must not train.
        if swap.get("all_fields_from_single_place") is not True:
            return "store_header_mixed_sources"
        fields = swap.get("fields_swapped")
        if not isinstance(fields, list) or not fields:
            return "store_header_no_fields_swapped"
        tokens = row.get("tokens")
        tokens = tokens if isinstance(tokens, list) else []
        token_set = set(tokens)
        swapped_labels: set[str] = set()
        for swapped in fields:
            if not isinstance(swapped, dict):
                return "store_header_invalid_field"
            field_label = str(swapped.get("label") or "").upper()
            old_value = str(swapped.get("old_text") or "")
            new_value = str(swapped.get("new_text") or "")
            if field_label not in {"ADDRESS_LINE", "PHONE_NUMBER", "WEBSITE"}:
                return "store_header_unsupported_field"
            if not new_value or old_value == new_value:
                return "store_header_unchanged_field"
            # Verify against the ACTUAL tokens, not just metadata: every token of
            # the swapped-in value must be present in the row. Catches metadata
            # that claims a swap the payload never received.
            if any(piece not in token_set for piece in new_value.split()):
                return "store_header_swap_not_applied"
            swapped_labels.add(field_label)
        # The address is the core of a store's identity; a header swap that left
        # the address untouched is not a location swap.
        if "ADDRESS_LINE" not in swapped_labels:
            return "store_header_address_not_swapped"
        return None

    return "unsupported_operation"


def _synthetic_candidate_quality_passes(row: dict[str, Any]) -> bool:
    """Return True when a synthetic candidate is grounded enough for training."""
    return _synthetic_candidate_quality_failure(row) is None


def _synthetic_contract_ready_operation(
    row: dict[str, Any],
    merchant_contracts: Optional[dict[str, dict[str, Any]]],
) -> Optional[str]:
    """Return the candidate operation when its merchant contract marks it ready."""
    if not merchant_contracts:
        return None
    contract = merchant_contracts.get(_synthetic_merchant_key(row))
    if not contract:
        return None
    if str(contract.get("status") or "").strip().lower() != "ready":
        return None
    operation = _synthetic_operation(row).strip()
    return operation if _contract_operation_ready(contract, operation) else None


def _select_synthetic_training_examples(
    rows: List[dict[str, Any]],
    *,
    max_per_merchant: Optional[int] = None,
    max_per_merchant_operation: Optional[int] = None,
    min_structure_similarity: Optional[float] = None,
    merchant_contracts: Optional[dict[str, dict[str, Any]]] = None,
    require_high_fidelity: bool = False,
) -> SyntheticTrainingLoad:
    """Apply LayoutLM synthetic quality gates and diversity caps to rows.

    When ``require_high_fidelity`` is set, a candidate that passes the structure
    and contract gates but is not flagged ``candidate_quality.high_fidelity`` is
    rejected. This is used when building a curated, ready-to-train bundle (which
    must be entirely high-fidelity); ordinary training runs leave it off so the
    looser structure threshold still admits trainable examples.
    """
    examples: List[dict[str, Any]] = []
    accepted: List[dict[str, Any]] = []
    rejected_rows: List[dict[str, Any]] = []
    rejection_reasons: Dict[str, int] = {}

    def reject(
        reason: str,
        *,
        row: Optional[dict[str, Any]] = None,
        idx: int = -1,
    ) -> None:
        rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        if row is not None:
            rejected_rows.append(
                _synthetic_rejection_record(row, idx=idx, reason=reason)
            )

    for idx, row in enumerate(rows):
        if row.get("train_only", True) is False:
            reject("not_train_only", row=row, idx=idx)
            continue

        tokens = row.get("tokens")
        bboxes = row.get("bboxes")
        ner_tags = row.get("ner_tags")
        if not (
            isinstance(tokens, list)
            and isinstance(bboxes, list)
            and isinstance(ner_tags, list)
        ):
            reject("missing_sequence_fields", row=row, idx=idx)
            continue
        if not tokens or len(tokens) != len(bboxes) or len(tokens) != len(ner_tags):
            reject("empty_or_mismatched_lengths", row=row, idx=idx)
            continue
        if not all(isinstance(token, str) and token for token in tokens):
            reject("invalid_tokens", row=row, idx=idx)
            continue
        if not all(_valid_box(box) for box in bboxes):
            reject("invalid_bboxes", row=row, idx=idx)
            continue
        if not all(isinstance(tag, str) and tag for tag in ner_tags):
            reject("invalid_ner_tags", row=row, idx=idx)
            continue
        quality_failure = _synthetic_candidate_quality_failure(
            row,
            min_structure_similarity=min_structure_similarity,
        )
        if quality_failure:
            reject(quality_failure, row=row, idx=idx)
            continue
        contract_failure = _synthetic_contract_quality_failure(
            row,
            merchant_contracts=merchant_contracts,
        )
        if contract_failure:
            reject(contract_failure, row=row, idx=idx)
            continue
        if require_high_fidelity and not _synthetic_is_high_fidelity(row):
            reject("not_high_fidelity", row=row, idx=idx)
            continue

        image_id = str(row.get("image_id") or f"synthetic-{idx:04d}")
        receipt_key = str(row.get("receipt_key") or f"{image_id}#00001")
        if "#" not in receipt_key:
            receipt_key = f"{receipt_key}#00001"

        accepted.append(
            {
                "row": row,
                "idx": idx,
                "example": {
                    "tokens": tokens,
                    "bboxes": bboxes,
                    "ner_tags": ner_tags,
                    "receipt_key": receipt_key,
                    "image_id": image_id,
                },
            }
        )

    merchant_cap = max(1, max_per_merchant or _synthetic_max_examples_per_merchant())
    operation_cap = max(
        1,
        max_per_merchant_operation or _synthetic_max_examples_per_merchant_operation(),
    )
    merchant_counts: Dict[str, int] = {}
    operation_counts: Dict[tuple[str, str], int] = {}
    accepted_rows: List[dict[str, Any]] = []
    accepted.sort(
        key=lambda item: (
            -_synthetic_fidelity_score(item["row"]),
            item["idx"],
        )
    )
    selected_indices: set[int] = set()

    def can_accept(item: dict[str, Any]) -> bool:
        row = item["row"]
        merchant = _synthetic_merchant_key(row)
        operation = _synthetic_operation(row)
        operation_key = (merchant, operation)
        return (
            merchant_counts.get(merchant, 0) < merchant_cap
            and operation_counts.get(operation_key, 0) < operation_cap
        )

    def accept_item(item: dict[str, Any]) -> None:
        row = item["row"]
        merchant = _synthetic_merchant_key(row)
        operation = _synthetic_operation(row)
        operation_key = (merchant, operation)
        accepted_rows.append(row)
        examples.append(item["example"])
        merchant_counts[merchant] = merchant_counts.get(merchant, 0) + 1
        operation_counts[operation_key] = operation_counts.get(operation_key, 0) + 1
        selected_indices.add(item["idx"])

    if merchant_contracts:
        best_ready_operation_items: Dict[tuple[str, str], dict[str, Any]] = {}
        for item in accepted:
            row = item["row"]
            operation = _synthetic_contract_ready_operation(row, merchant_contracts)
            if operation:
                best_ready_operation_items.setdefault(
                    (_synthetic_merchant_key(row), operation),
                    item,
                )
        for item in sorted(
            best_ready_operation_items.values(),
            key=lambda value: (
                -_synthetic_fidelity_score(value["row"]),
                value["idx"],
            ),
        ):
            if can_accept(item):
                accept_item(item)

    for item in accepted:
        if item["idx"] in selected_indices:
            continue
        row = item["row"]
        merchant = _synthetic_merchant_key(row)
        operation = _synthetic_operation(row)
        operation_key = (merchant, operation)
        if merchant_counts.get(merchant, 0) >= merchant_cap:
            reject("merchant_synthetic_cap", row=row, idx=item["idx"])
            continue
        if operation_counts.get(operation_key, 0) >= operation_cap:
            reject(
                "merchant_operation_synthetic_cap",
                row=row,
                idx=item["idx"],
            )
            continue
        accept_item(item)

    accepted_scores = [
        score
        for row in accepted_rows
        if (score := _synthetic_structure_score(row)) is not None
    ]
    accepted_candidate_quality_scores = [
        score
        for row in accepted_rows
        if (score := _synthetic_declared_candidate_quality(row)) is not None
    ]

    return SyntheticTrainingLoad(
        examples=examples,
        accepted_rows=accepted_rows,
        rejected_rows=rejected_rows,
        candidates_seen=len(rows),
        candidates_accepted=len(examples),
        candidates_rejected=sum(rejection_reasons.values()),
        rejection_reasons=rejection_reasons,
        accepted_operation_counts=_count_synthetic_values(
            [_synthetic_operation(row) for row in accepted_rows]
        ),
        accepted_operation_coverage=_synthetic_accepted_operation_coverage(
            accepted_rows,
            merchant_contracts,
        ),
        accepted_category_counts=_count_synthetic_values(
            [
                category
                for row in accepted_rows
                if (category := _synthetic_category(row))
            ]
        ),
        accepted_field_replacement_counts=_count_synthetic_values(
            [
                label
                for row in accepted_rows
                if (label := _synthetic_field_replacement_label(row))
            ]
        ),
        accepted_structure_similarity=_synthetic_score_summary(accepted_scores),
        accepted_structure_components=_synthetic_component_score_summary(accepted_rows),
        accepted_candidate_quality=_synthetic_score_summary(
            accepted_candidate_quality_scores
        ),
        accepted_candidate_quality_components=(
            _synthetic_candidate_quality_component_summary(accepted_rows)
        ),
        accepted_real_baseline_comparison=_synthetic_real_baseline_summary(
            accepted_rows
        ),
        accepted_mix_balance=_synthetic_mix_balance(accepted_rows),
        accepted_grounded_count=sum(
            1 for row in accepted_rows if _synthetic_is_grounded(row)
        ),
        accepted_arithmetic_count=sum(
            1 for row in accepted_rows if _synthetic_has_arithmetic(row)
        ),
    )


def _load_synthetic_training_examples(
    source: Optional[str],
) -> List[dict[str, Any]]:
    """Load accepted train-only synthetic examples from JSON."""
    return _load_synthetic_training_examples_with_summary(source).examples


def _load_synthetic_training_examples_with_summary(
    source: Optional[str],
) -> SyntheticTrainingLoad:
    """Load train-only synthetic LayoutLM examples from JSON.

    Accepted row shape matches the raw training dataset fields:
    ``tokens``, ``bboxes``, ``ner_tags``, ``receipt_key``, and ``image_id``.
    Rows with ``train_only: false`` are ignored so synthetic examples cannot
    leak into validation through this path.
    """
    if not source:
        return SyntheticTrainingLoad()

    payload = _read_json_source(source)
    rows, bundle_rejected_rows, bundle_rejection_reasons = (
        _candidate_rows_with_bundle_readiness(payload)
    )
    merchant_contracts = _merchant_synthesis_contracts_by_merchant(payload)
    selected = _select_synthetic_training_examples(
        rows,
        merchant_contracts=merchant_contracts,
    )
    if bundle_rejection_reasons:
        selected.rejected_rows = bundle_rejected_rows + selected.rejected_rows
        selected.candidates_seen += sum(bundle_rejection_reasons.values())
        selected.candidates_rejected += sum(bundle_rejection_reasons.values())
        for reason, count in bundle_rejection_reasons.items():
            selected.rejection_reasons[reason] = (
                selected.rejection_reasons.get(reason, 0) + count
            )
    return selected


# Anchor labels used to dynamically bound the line-item region per receipt.
_LI_HEADER_ANCHORS = {"ADDRESS_LINE", "PHONE_NUMBER", "STORE_HOURS"}
_LI_TOTALS_ANCHORS = {"SUBTOTAL", "TAX", "GRAND_TOTAL"}


def _crop_to_line_item_band(
    words: List["WordInfo"],
    label_map: Dict[Tuple[str, int, int, int], List[str]],
    img_id: str,
    rec_id: int,
) -> List["WordInfo"]:
    """Crop a receipt's words to the line-item band (between header & totals).

    Mirrors the deterministic second-pass window: bound by the receipt's OWN
    anchor labels (read from the RAW ``label_map`` so a focused ``allowed_labels``
    doesn't hide them), keep words whose y-centroid falls strictly between the
    header and totals bands, and drop the anchor words themselves. Returns [] when
    the receipt can't be bounded (no totals anchor) — those receipts are skipped.
    """
    import statistics

    def cy(wi: "WordInfo") -> float:
        return (wi.bbox[1] + wi.bbox[3]) / 2.0

    def raw(wi: "WordInfo") -> set:
        return set(label_map.get((img_id, rec_id, wi.line_id, wi.word_id), []))

    totals = [cy(wi) for wi in words if raw(wi) & _LI_TOTALS_ANCHORS]
    if not totals:
        return []
    tc = statistics.median(totals)
    header = [cy(wi) for wi in words if raw(wi) & _LI_HEADER_ANCHORS]
    if not header:
        merch = [cy(wi) for wi in words if "MERCHANT_NAME" in raw(wi)]
        if not merch:
            return []
        header = [max(merch, key=lambda y: abs(y - tc))]
    hc = statistics.median(header)
    lo, hi = min(hc, tc), max(hc, tc)
    excl = _LI_HEADER_ANCHORS | _LI_TOTALS_ANCHORS
    return [wi for wi in words if lo < cy(wi) < hi and not (raw(wi) & excl)]


def load_datasets(
    dynamo: DynamoClient,
    label_status: str = ValidationStatus.VALID.value,
    random_seed: Optional[int] = None,
    label_merges: Optional[Dict[str, List[str]]] = None,
    allowed_labels: Optional[List[str]] = None,
    model_version: str = "v1",
    synthetic_training_examples: Optional[str] = None,
) -> Tuple[Any, SplitMetadata, MergeInfo]:
    """Load and process datasets from DynamoDB.

    Args:
        dynamo: DynamoDB client instance.
        label_status: Filter labels by validation status.
        random_seed: Random seed for reproducible train/val split.
        label_merges: Dict mapping target labels to source labels to merge.
            E.g., {"AMOUNT": ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]}
        allowed_labels: Optional list of allowed labels (whitelist).
        synthetic_training_examples: Optional local path or S3 URI containing
            train-only LayoutLM-style synthetic candidates.

    Returns:
        Tuple of (DatasetDict, SplitMetadata, MergeInfo).
    """
    # For now, fetch all labels with status; join to words by PK/SK
    all_labels, _ = dynamo.list_receipt_word_labels_with_status(
        ValidationStatus(label_status), limit=None, last_evaluated_key=None
    )

    # Group labels by (image_id, receipt_id, line_id, word_id)
    label_map: Dict[Tuple[str, int, int, int], List[str]] = {}
    receipts_with_labels: set[Tuple[str, int]] = set()
    for lbl in all_labels:
        key = (lbl.image_id, lbl.receipt_id, lbl.line_id, lbl.word_id)
        label_map.setdefault(key, []).append(lbl.label)
        receipts_with_labels.add((lbl.image_id, lbl.receipt_id))

    # Curated receipt allowlist (scoped model trains on a hand-picked subset).
    # `is not None` so an explicitly-empty allowlist filters to nothing rather
    # than silently training on the full corpus.
    receipt_allowlist = _load_receipt_allowlist()
    if receipt_allowlist is not None:
        receipts_with_labels = {
            (i, r)
            for (i, r) in receipts_with_labels
            if f"{i}#{r:05d}" in receipt_allowlist
        }

    # Fetch ALL words for receipts that have any VALID labels, so unlabeled tokens become 'O'
    words: List[Any] = []
    for img_id, rec_id in receipts_with_labels:
        words.extend(dynamo.list_receipt_words_from_receipt(img_id, rec_id))

    # Compute per-image maxima to normalize pixel coordinates if needed
    image_extents: Dict[str, Tuple[float, float]] = {}
    for w in words:
        x0, y0, x1, y1 = _box_from_word(w)
        max_x = max(x1, image_extents.get(w.image_id, (0.0, 0.0))[0])
        max_y = max(y1, image_extents.get(w.image_id, (0.0, 0.0))[1])
        image_extents[w.image_id] = (max_x, max_y)

    # Build label_merges from parameter or environment variables (backwards compat)
    effective_label_merges = label_merges
    if effective_label_merges is None:
        # Backwards compatibility: check env vars for legacy merge flags
        env_merges: Dict[str, List[str]] = {}
        if os.getenv("LAYOUTLM_MERGE_AMOUNTS", "0") == "1":
            env_merges["AMOUNT"] = [
                "LINE_TOTAL",
                "SUBTOTAL",
                "TAX",
                "GRAND_TOTAL",
            ]
        if os.getenv("LAYOUTLM_MERGE_DATE_TIME", "0") == "1":
            env_merges["DATE"] = ["TIME"]
        if os.getenv("LAYOUTLM_MERGE_ADDRESS_PHONE", "0") == "1":
            env_merges["ADDRESS"] = ["PHONE_NUMBER", "ADDRESS_LINE"]
        if env_merges:
            effective_label_merges = env_merges

    # Build the merge lookup for efficient label transformation
    merge_lookup = _build_merge_lookup(effective_label_merges)

    # Build allowed set from parameter or environment variable
    allowed: Optional[set[str]] = None
    if allowed_labels:
        allowed = {label.upper() for label in allowed_labels}
    else:
        # Backwards compat: check env var
        allowed_labels_env = os.getenv("LAYOUTLM_ALLOWED_LABELS")
        if allowed_labels_env:
            allowed = {
                s.strip().upper() for s in allowed_labels_env.split(",") if s.strip()
            }

    # Add merge targets to allowed set so they pass through filtering
    if allowed and merge_lookup:
        allowed.update(merge_lookup.values())

    # Filter allowed to valid labels (core set + merge targets)
    valid_labels = _CORE_SET.copy()
    if merge_lookup:
        valid_labels.update(merge_lookup.values())
    if allowed:
        allowed = {label for label in allowed if label in valid_labels}

    # Track resulting labels for metrics
    resulting_labels_set: set[str] = set()

    # Group words by receipt for spatial block grouping
    # This allows multi-line entities to be properly tagged with BIO continuity
    receipt_words: Dict[Tuple[str, int], List[WordInfo]] = {}
    for w in words:
        word_key = (w.image_id, w.receipt_id, w.line_id, w.word_id)
        receipt_key_tuple = (w.image_id, w.receipt_id)
        # Get both original (for grouping) and merged (for BIO tags) labels
        original_label, merged_label = _get_original_and_merged_labels(
            label_map.get(word_key, []),
            allowed,
            merge_lookup,
        )
        # Track non-O labels for resulting_labels (use merged)
        if merged_label != "O":
            resulting_labels_set.add(merged_label)
        max_x, max_y = image_extents.get(w.image_id, (1.0, 1.0))
        x0, y0, x1, y1 = _box_from_word(w)
        norm_box = _normalize_box_from_extents(x0, y0, x1, y1, max_x, max_y)

        word_info = WordInfo(
            word_id=w.word_id,
            text=w.text,
            bbox=norm_box,
            label=merged_label,  # Used for BIO tagging
            original_label=original_label,  # Used for spatial grouping
            line_id=w.line_id,
            image_id=w.image_id,
            receipt_id=w.receipt_id,
        )
        receipt_words.setdefault(receipt_key_tuple, []).append(word_info)

    # Build per-receipt sliding-window examples with mixed labels (incl. O).
    # This matches the inference distribution where the Mac OCR worker feeds
    # full multi-line word sequences — not pre-segmented same-label blocks.
    examples: List[LineExample] = []
    window_size = int(os.getenv("LAYOUTLM_WINDOW_SIZE", "200"))
    window_stride = int(os.getenv("LAYOUTLM_WINDOW_STRIDE", "150"))
    scope = os.getenv("LAYOUTLM_SCOPE", "full")

    for (img_id, rec_id), words_in_receipt in receipt_words.items():
        if scope == "line_items":
            # Second-pass scope: train only on the line-item band of each receipt.
            words_in_receipt = _crop_to_line_item_band(
                words_in_receipt, label_map, img_id, rec_id
            )
            if not words_in_receipt:
                continue
        receipt_key = f"{img_id}#{rec_id:05d}"
        examples.extend(
            _build_receipt_window_examples(
                words_in_receipt,
                receipt_key,
                window_size=window_size,
                stride=window_stride,
            )
        )

    ds_mod = importlib.import_module("datasets")
    Dataset = getattr(ds_mod, "Dataset")
    DatasetDict = getattr(ds_mod, "DatasetDict")
    Features = getattr(ds_mod, "Features")
    Sequence = getattr(ds_mod, "Sequence")
    Value = getattr(ds_mod, "Value")

    features = Features(
        {
            "tokens": Sequence(Value("string")),
            "bboxes": Sequence(Sequence(Value("int64"))),
            "ner_tags": Sequence(Value("string")),
            "receipt_key": Value("string"),
            "image_id": Value("string"),
        }
    )

    dataset = Dataset.from_dict(
        {
            "tokens": [ex.tokens for ex in examples],
            "bboxes": [ex.bboxes for ex in examples],
            "ner_tags": [ex.ner_tags for ex in examples],
            "receipt_key": [ex.receipt_key for ex in examples],
            "image_id": [ex.image_id for ex in examples],
        },
        features=features,
    )

    # Receipt-level split by unique (image_id, receipt_id).
    unique_receipts = sorted(
        {ex.receipt_key for ex in examples}
    )  # Sort for determinism
    fixed_val = _load_fixed_val_keys()
    if fixed_val:
        # Pinned canonical val set: hold out exactly these receipts (those
        # present in this run's data); train on everything else. Every run is
        # then directly comparable. Seed is recorded but not used for the split.
        if random_seed is None:
            random_seed = 0
        # Seed the global PRNG even though the split is deterministic — the
        # downstream O-only line downsampling consumes random.random(), so two
        # pinned-val runs must seed identically to keep the SAME training lines.
        random.seed(random_seed)
        val_receipts_list = [r for r in unique_receipts if r in fixed_val]
        train_receipts_list = [r for r in unique_receipts if r not in fixed_val]
    else:
        # Use provided seed or generate one for reproducibility
        if random_seed is None:
            random_seed = random.randint(0, 2**31 - 1)
        random.seed(random_seed)
        random.shuffle(unique_receipts)
        cut = max(1, int(len(unique_receipts) * 0.9))
        train_receipts_list = unique_receipts[:cut]
        val_receipts_list = unique_receipts[cut:]
    train_receipts = set(train_receipts_list)
    val_receipts = set(val_receipts_list)

    # Compute hashes of receipt lists for verification
    train_receipts_hash = hashlib.sha256(
        ",".join(sorted(train_receipts_list)).encode()
    ).hexdigest()[:16]
    val_receipts_hash = hashlib.sha256(
        ",".join(sorted(val_receipts_list)).encode()
    ).hexdigest()[:16]

    train_indices = [
        i for i, ex in enumerate(examples) if ex.receipt_key in train_receipts
    ]
    val_indices = [i for i, ex in enumerate(examples) if ex.receipt_key in val_receipts]

    # Downsample all-O lines in training to reach target O:entity token ratio
    # Only affects training; validation remains untouched
    # Compute token counts over candidate train set
    target_ratio_env = os.getenv("LAYOUTLM_O_TO_ENTITY_RATIO")
    try:
        target_ratio = float(target_ratio_env) if target_ratio_env else 2.0
    except ValueError:
        target_ratio = 2.0

    entity_tokens = 0
    o_tokens_in_entity_lines = 0
    o_only_lines_count = 0
    entity_lines_count = 0
    o_only_tokens_total = 0
    is_o_only_flags: Dict[int, bool] = {}

    for idx in train_indices:
        ex = examples[idx]
        has_entity = any(tag != "O" for tag in ex.ner_tags)
        is_o_only_flags[idx] = not has_entity
        if has_entity:
            entity_lines_count += 1
            entity_tokens += sum(1 for tag in ex.ner_tags if tag != "O")
            o_tokens_in_entity_lines += sum(1 for tag in ex.ner_tags if tag == "O")
        else:
            o_only_lines_count += 1
            o_only_tokens_total += len(ex.tokens)

    # Compute O:entity ratio before downsampling
    total_o_tokens_before = o_tokens_in_entity_lines + o_only_tokens_total
    o_entity_ratio_before = (
        total_o_tokens_before / entity_tokens if entity_tokens > 0 else float("inf")
    )

    keep_ratio = 1.0
    if o_only_tokens_total > 0 and entity_tokens > 0:
        keep_ratio = min(
            1.0, (target_ratio * entity_tokens) / float(o_only_tokens_total)
        )

    filtered_train_indices: List[int] = []
    o_only_lines_kept = 0
    o_tokens_kept = 0
    for idx in train_indices:
        if not is_o_only_flags.get(idx, False):
            filtered_train_indices.append(idx)
        else:
            if random.random() < keep_ratio:
                filtered_train_indices.append(idx)
                o_only_lines_kept += 1
                o_tokens_kept += len(examples[idx].tokens)

    # Compute O:entity ratio after downsampling
    total_o_tokens_after = o_tokens_in_entity_lines + o_tokens_kept
    o_entity_ratio_after = (
        total_o_tokens_after / entity_tokens if entity_tokens > 0 else float("inf")
    )

    # Compute validation O:entity ratio
    val_entity_tokens = 0
    val_o_tokens = 0
    for idx in val_indices:
        ex = examples[idx]
        val_entity_tokens += sum(1 for tag in ex.ner_tags if tag != "O")
        val_o_tokens += sum(1 for tag in ex.ner_tags if tag == "O")
    o_entity_ratio_val = (
        val_o_tokens / val_entity_tokens if val_entity_tokens > 0 else float("inf")
    )

    # Download warped receipt images for v3 training
    image_cache_dir: Optional[str] = None
    if model_version == "v3":
        unique_receipt_keys = {(ex.image_id, ex.receipt_id) for ex in examples}
        image_cache_dir = download_receipt_images(dynamo, unique_receipt_keys)

    synthetic_source = synthetic_training_examples or os.getenv(
        "LAYOUTLM_SYNTHETIC_TRAINING_EXAMPLES"
    )
    synthetic_load = _load_synthetic_training_examples_with_summary(synthetic_source)
    synthetic_examples = synthetic_load.examples

    # Create split metadata
    split_metadata = SplitMetadata(
        random_seed=random_seed,
        num_train_receipts=len(train_receipts_list),
        num_val_receipts=len(val_receipts_list),
        num_train_lines=len(filtered_train_indices),
        num_val_lines=len(val_indices),
        train_receipts_hash=train_receipts_hash,
        val_receipts_hash=val_receipts_hash,
        o_entity_ratio_before_downsample=o_entity_ratio_before,
        o_entity_ratio_after_downsample=o_entity_ratio_after,
        o_entity_ratio_val=o_entity_ratio_val,
        o_only_lines_total=o_only_lines_count,
        o_only_lines_kept=o_only_lines_kept,
        o_only_lines_dropped=o_only_lines_count - o_only_lines_kept,
        entity_lines_total=entity_lines_count,
        target_o_entity_ratio=target_ratio,
        image_cache_dir=image_cache_dir,
        val_receipt_keys=sorted(val_receipts_list),
        window_size=window_size,
        window_stride=window_stride,
        synthetic_train_examples=len(synthetic_examples),
        synthetic_source=synthetic_source,
        synthetic_candidates_seen=synthetic_load.candidates_seen,
        synthetic_candidates_accepted=synthetic_load.candidates_accepted,
        synthetic_candidates_rejected=synthetic_load.candidates_rejected,
        synthetic_rejection_reasons=synthetic_load.rejection_reasons,
        synthetic_accepted_operation_counts=synthetic_load.accepted_operation_counts,
        synthetic_accepted_operation_coverage=(
            synthetic_load.accepted_operation_coverage
        ),
        synthetic_accepted_category_counts=synthetic_load.accepted_category_counts,
        synthetic_accepted_field_replacement_counts=(
            synthetic_load.accepted_field_replacement_counts
        ),
        synthetic_accepted_structure_similarity=(
            synthetic_load.accepted_structure_similarity
        ),
        synthetic_accepted_structure_components=(
            synthetic_load.accepted_structure_components
        ),
        synthetic_accepted_candidate_quality=(
            synthetic_load.accepted_candidate_quality
        ),
        synthetic_accepted_candidate_quality_components=(
            synthetic_load.accepted_candidate_quality_components
        ),
        synthetic_accepted_real_baseline_comparison=(
            synthetic_load.accepted_real_baseline_comparison
        ),
        synthetic_accepted_mix_balance=synthetic_load.accepted_mix_balance,
        synthetic_accepted_grounded_count=synthetic_load.accepted_grounded_count,
        synthetic_accepted_arithmetic_count=synthetic_load.accepted_arithmetic_count,
    )

    # v3 needs receipt_key during preprocessing (image cache lookup); removed later in trainer.map()
    remove_after_split = ["receipt_key"] if model_version != "v3" else []
    train_ds = dataset.select(filtered_train_indices).remove_columns(remove_after_split)  # type: ignore[attr-defined]
    val_ds = dataset.select(val_indices).remove_columns(remove_after_split)  # type: ignore[attr-defined]

    if synthetic_examples:
        synthetic_ds = Dataset.from_dict(
            {
                "tokens": [ex["tokens"] for ex in synthetic_examples],
                "bboxes": [ex["bboxes"] for ex in synthetic_examples],
                "ner_tags": [ex["ner_tags"] for ex in synthetic_examples],
                "receipt_key": [ex["receipt_key"] for ex in synthetic_examples],
                "image_id": [ex["image_id"] for ex in synthetic_examples],
            },
            features=features,
        ).remove_columns(remove_after_split)
        concatenate_datasets = getattr(ds_mod, "concatenate_datasets")
        train_ds = concatenate_datasets([train_ds, synthetic_ds])
        split_metadata.num_train_lines += len(synthetic_examples)

    # Build merge info for tracking
    merge_info = MergeInfo(
        label_merges=effective_label_merges,
        merge_lookup=merge_lookup,
        resulting_labels=sorted(resulting_labels_set),
    )

    return (
        DatasetDict({"train": train_ds, "validation": val_ds}),
        split_metadata,
        merge_info,
    )
