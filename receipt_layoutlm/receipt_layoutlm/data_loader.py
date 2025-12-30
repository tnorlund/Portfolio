from typing import Any, Dict, List, Tuple, Optional
from dataclasses import dataclass, field
import hashlib

import importlib
import random
import os

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus


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

CORE_LABELS: dict[str, str] = {
    # ── Merchant & store info ───────────────────────────────────
    "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
    "STORE_HOURS": "Printed business hours or opening times for the merchant.",
    "PHONE_NUMBER": "Telephone number printed on the receipt "
    "(store's main line).",
    "WEBSITE": "Web or email address printed on the receipt "
    "(e.g., sprouts.com).",
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    # ── Location / address ──────────────────────────────────────
    "ADDRESS_LINE": "Full address line (street + city etc.) printed on "
    "the receipt.",
    # If you later break it down, add:
    # "ADDRESS_NUMBER": "Street/building number.",
    # "STREET_NAME":    "Street name.",
    # "CITY":           "City name.",
    # "STATE":          "State / province abbreviation.",
    # "POSTAL_CODE":    "ZIP or postal code.",
    # ── Transaction info ───────────────────────────────────────
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": "Payment instrument summary "
    "(e.g., VISA ••••1234, CASH).",
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": "Any non-coupon discount line item "
    "(e.g., 10% member discount).",
    # ── Line-item fields ───────────────────────────────────────
    "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
    "QUANTITY": "Numeric count or weight of the item (e.g., 2, 1.31 lb).",
    "UNIT_PRICE": "Price per single unit / weight before tax.",
    "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
    # ── Totals & taxes ─────────────────────────────────────────
    "SUBTOTAL": "Sum of all line totals before tax and discounts.",
    "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
    "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
}


@dataclass
class LineExample:
    image_id: str
    receipt_id: int
    line_id: int
    tokens: List[str]
    bboxes: List[List[int]]
    ner_tags: List[str]
    receipt_key: str


def _build_line_example(
    line_key: Tuple[str, int, int],
    items: List[Tuple[int, str, List[int], str]],
    line_to_receipt: Dict[Tuple[str, int, int], Tuple[str, int]],
) -> LineExample:
    # sort by word_id to preserve order
    items.sort(key=lambda t: t[0])
    img_id, rec_id, ln_id = line_key

    tokens: List[str] = []
    boxes: List[List[int]] = []
    raw_labels: List[str] = []

    for wid, tok, box, lbl in items:
        if not isinstance(tok, str):
            raise ValueError(
                "Invalid token type: expected str, got "
                f"{type(tok).__name__} for image_id={img_id} "
                f"receipt_id={rec_id} line_id={ln_id} word_id={wid}"
            )
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(not isinstance(v, int) for v in box)
        ):
            raise ValueError(
                "Invalid bbox: expected list[int] of length 4 for "
                f"image_id={img_id} receipt_id={rec_id} "
                f"line_id={ln_id} word_id={wid}; got {box!r}"
            )
        if not isinstance(lbl, str):
            raise ValueError(
                "Invalid label type: expected str, got "
                f"{type(lbl).__name__} for image_id={img_id} "
                f"receipt_id={rec_id} line_id={ln_id} word_id={wid}"
            )
        tokens.append(tok)
        boxes.append(box)
        raw_labels.append(lbl)

    # Compute simple BIO tags per-line: B- for first occurrence, I- for contiguous same label
    bio_labels: List[str] = []
    prev = "O"
    for lbl in raw_labels:
        if lbl == "O":
            bio_labels.append("O")
            prev = "O"
        else:
            bio_labels.append("B-" + lbl if prev != lbl else "I-" + lbl)
            prev = lbl

    rk = line_to_receipt.get(line_key)
    receipt_key = f"{rk[0]}#{rk[1]:05d}" if rk else ""

    return LineExample(
        image_id=img_id,
        receipt_id=rec_id,
        line_id=ln_id,
        tokens=tokens,
        bboxes=boxes,
        ner_tags=bio_labels,
        receipt_key=receipt_key,
    )


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
    label_merges: Optional[Dict[str, List[str]]]
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


@dataclass
class MergeInfo:
    """Information about label merging applied during dataset loading."""

    label_merges: Optional[Dict[str, List[str]]]
    merge_lookup: Dict[str, str]
    resulting_labels: List[str]


def load_datasets(
    dynamo: DynamoClient,
    label_status: str = ValidationStatus.VALID.value,
    random_seed: Optional[int] = None,
    label_merges: Optional[Dict[str, List[str]]] = None,
    allowed_labels: Optional[List[str]] = None,
) -> Tuple[Any, SplitMetadata, MergeInfo]:
    """Load and process datasets from DynamoDB.

    Args:
        dynamo: DynamoDB client instance.
        label_status: Filter labels by validation status.
        random_seed: Random seed for reproducible train/val split.
        label_merges: Dict mapping target labels to source labels to merge.
            E.g., {"AMOUNT": ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]}
        allowed_labels: Optional list of allowed labels (whitelist).

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
            env_merges["AMOUNT"] = ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]
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
        allowed = {l.upper() for l in allowed_labels}
    else:
        # Backwards compat: check env var
        allowed_labels_env = os.getenv("LAYOUTLM_ALLOWED_LABELS")
        if allowed_labels_env:
            allowed = {
                s.strip().upper()
                for s in allowed_labels_env.split(",")
                if s.strip()
            }

    # Add merge targets to allowed set so they pass through filtering
    if allowed and merge_lookup:
        allowed.update(merge_lookup.values())

    # Filter allowed to valid labels (core set + merge targets)
    valid_labels = _CORE_SET.copy()
    if merge_lookup:
        valid_labels.update(merge_lookup.values())
    if allowed:
        allowed = {l for l in allowed if l in valid_labels}

    # Track resulting labels for metrics
    resulting_labels_set: set[str] = set()

    # Group by line so each example is a sequence of tokens
    seq_map: Dict[
        Tuple[str, int, int], List[Tuple[int, str, List[int], str]]
    ] = {}
    # Track receipt key per line for receipt-level splitting
    line_to_receipt: Dict[Tuple[str, int, int], Tuple[str, int]] = {}
    for w in words:
        word_key = (w.image_id, w.receipt_id, w.line_id, w.word_id)
        line_key = (w.image_id, w.receipt_id, w.line_id)
        label = _raw_label(
            label_map.get(word_key, []),
            allowed,
            merge_lookup,
        )
        # Track non-O labels for resulting_labels
        if label != "O":
            resulting_labels_set.add(label)
        max_x, max_y = image_extents.get(w.image_id, (1.0, 1.0))
        x0, y0, x1, y1 = _box_from_word(w)
        norm_box = _normalize_box_from_extents(x0, y0, x1, y1, max_x, max_y)
        seq_map.setdefault(line_key, []).append(
            (w.word_id, w.text, norm_box, label)
        )
        line_to_receipt[line_key] = (w.image_id, w.receipt_id)

    examples: List[LineExample] = []

    for line_key, items in seq_map.items():
        examples.append(_build_line_example(line_key, items, line_to_receipt))

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
        }
    )

    dataset = Dataset.from_dict(
        {
            "tokens": [ex.tokens for ex in examples],
            "bboxes": [ex.bboxes for ex in examples],
            "ner_tags": [ex.ner_tags for ex in examples],
            "receipt_key": [ex.receipt_key for ex in examples],
        },
        features=features,
    )

    # Receipt-level split (90/10) by unique (image_id, receipt_id)
    # Use provided seed or generate one for reproducibility
    if random_seed is None:
        random_seed = random.randint(0, 2**31 - 1)
    random.seed(random_seed)

    unique_receipts = sorted({ex.receipt_key for ex in examples})  # Sort for determinism
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
    val_indices = [
        i for i, ex in enumerate(examples) if ex.receipt_key in val_receipts
    ]

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
    )

    train_ds = dataset.select(filtered_train_indices).remove_columns(["receipt_key"])  # type: ignore[attr-defined]
    val_ds = dataset.select(val_indices).remove_columns(["receipt_key"])  # type: ignore[attr-defined]

    # Build merge info for tracking
    merge_info = MergeInfo(
        label_merges=effective_label_merges,
        merge_lookup=merge_lookup,
        resulting_labels=sorted(resulting_labels_set),
    )

    return DatasetDict({"train": train_ds, "validation": val_ds}), split_metadata, merge_info
