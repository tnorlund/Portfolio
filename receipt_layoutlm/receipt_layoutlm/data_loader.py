from typing import Any, Dict, List, Tuple, Optional
from dataclasses import dataclass

import importlib
import random
import os

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

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


def _normalize_word_label(
    raw: str,
    allowed: Optional[set[str]] = None,
    merge_amounts: bool = False,
    merge_date_time: bool = False,
    merge_address_phone: bool = False,
) -> str:
    lab = (raw or "").upper()
    if lab == "O":
        return "O"

    # Merge currency labels into AMOUNT
    if merge_amounts and lab in {
        "LINE_TOTAL",
        "SUBTOTAL",
        "TAX",
        "GRAND_TOTAL",
    }:
        lab = "AMOUNT"

    # Merge DATE and TIME into DATE
    if merge_date_time and lab == "TIME":
        lab = "DATE"

    # Merge ADDRESS_LINE and PHONE_NUMBER into ADDRESS
    if merge_address_phone and lab == "PHONE_NUMBER":
        lab = "ADDRESS"
    elif merge_address_phone and lab == "ADDRESS_LINE":
        lab = "ADDRESS"

    if allowed is not None and lab not in allowed:
        return "O"
    return lab if lab in _CORE_SET or lab == "AMOUNT" or lab == "ADDRESS" else "O"


def _raw_label(
    labels: List[str],
    allowed: Optional[set[str]] = None,
    merge_amounts: bool = False,
    merge_date_time: bool = False,
    merge_address_phone: bool = False,
) -> str:
    # Pick first label if present; else 'O'. Normalize to CORE set or 'O'
    if not labels:
        return "O"
    return _normalize_word_label(
        labels[0], allowed, merge_amounts, merge_date_time, merge_address_phone
    )


def load_datasets(
    dynamo: DynamoClient,
    label_status: str = ValidationStatus.VALID.value,
) -> Any:
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

    # Build allowed set from environment-configured whitelist via DataConfig
    allowed_labels_env = os.getenv("LAYOUTLM_ALLOWED_LABELS")
    allowed: Optional[set[str]] = None
    if allowed_labels_env:
        allowed = {
            s.strip().upper()
            for s in allowed_labels_env.split(",")
            if s.strip()
        }
        # Only keep labels that are in core set or special merged labels ('AMOUNT', 'ADDRESS')
        allowed = {l for l in allowed if l in _CORE_SET or l == "AMOUNT" or l == "ADDRESS"}

    merge_amounts = os.getenv("LAYOUTLM_MERGE_AMOUNTS", "0") == "1"
    merge_date_time = os.getenv("LAYOUTLM_MERGE_DATE_TIME", "0") == "1"
    merge_address_phone = os.getenv("LAYOUTLM_MERGE_ADDRESS_PHONE", "0") == "1"

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
            merge_amounts,
            merge_date_time,
            merge_address_phone,
        )
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
    unique_receipts = list({ex.receipt_key for ex in examples})
    random.shuffle(unique_receipts)
    cut = max(1, int(len(unique_receipts) * 0.9))
    train_receipts = set(unique_receipts[:cut])
    val_receipts = set(unique_receipts[cut:])
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
    o_only_tokens_total = 0
    is_o_only_flags: Dict[int, bool] = {}
    for idx in train_indices:
        ex = examples[idx]
        has_entity = any(tag != "O" for tag in ex.ner_tags)
        is_o_only_flags[idx] = not has_entity
        if has_entity:
            entity_tokens += sum(1 for tag in ex.ner_tags if tag != "O")
        else:
            o_only_tokens_total += len(ex.tokens)

    keep_ratio = 1.0
    if o_only_tokens_total > 0 and entity_tokens > 0:
        keep_ratio = min(
            1.0, (target_ratio * entity_tokens) / float(o_only_tokens_total)
        )

    filtered_train_indices: List[int] = []
    for idx in train_indices:
        if not is_o_only_flags.get(idx, False):
            filtered_train_indices.append(idx)
        else:
            if random.random() < keep_ratio:
                filtered_train_indices.append(idx)

    train_ds = dataset.select(filtered_train_indices).remove_columns(["receipt_key"])  # type: ignore[attr-defined]
    val_ds = dataset.select(val_indices).remove_columns(["receipt_key"])  # type: ignore[attr-defined]
    return DatasetDict({"train": train_ds, "validation": val_ds})
