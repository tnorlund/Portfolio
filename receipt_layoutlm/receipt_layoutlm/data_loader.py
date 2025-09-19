from typing import Any, Dict, List, Tuple

import importlib
import random

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus


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


def _raw_label(labels: List[str]) -> str:
    # Pick first label if present; else 'O'. Uppercase for normalization
    if not labels:
        return "O"
    return labels[0].upper()


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
    key_set: set[Tuple[str, str]] = set()
    for lbl in all_labels:
        key = (lbl.image_id, lbl.receipt_id, lbl.line_id, lbl.word_id)
        label_map.setdefault(key, []).append(lbl.label)
        pk = f"IMAGE#{lbl.image_id}"
        sk = f"RECEIPT#{lbl.receipt_id:05d}#LINE#{lbl.line_id:05d}#WORD#{lbl.word_id:05d}"
        key_set.add((pk, sk))
    key_list: List[Dict[str, Dict[str, str]]] = [
        {"PK": {"S": pk}, "SK": {"S": sk}} for (pk, sk) in key_set
    ]

    words = dynamo.get_receipt_words_by_keys(key_list) if key_list else []

    # Compute per-image maxima to normalize pixel coordinates if needed
    image_extents: Dict[str, Tuple[float, float]] = {}
    for w in words:
        x0, y0, x1, y1 = _box_from_word(w)
        max_x = max(x1, image_extents.get(w.image_id, (0.0, 0.0))[0])
        max_y = max(y1, image_extents.get(w.image_id, (0.0, 0.0))[1])
        image_extents[w.image_id] = (max_x, max_y)

    # Group by line so each example is a sequence of tokens
    seq_map: Dict[
        Tuple[str, int, int], List[Tuple[int, str, List[int], str]]
    ] = {}
    # Track receipt key per line for receipt-level splitting
    line_to_receipt: Dict[Tuple[str, int, int], Tuple[str, int]] = {}
    for w in words:
        word_key = (w.image_id, w.receipt_id, w.line_id, w.word_id)
        line_key = (w.image_id, w.receipt_id, w.line_id)
        label = _raw_label(label_map.get(word_key, []))
        max_x, max_y = image_extents.get(w.image_id, (1.0, 1.0))
        x0, y0, x1, y1 = _box_from_word(w)
        norm_box = _normalize_box_from_extents(x0, y0, x1, y1, max_x, max_y)
        seq_map.setdefault(line_key, []).append(
            (w.word_id, w.text, norm_box, label)
        )
        line_to_receipt[line_key] = (w.image_id, w.receipt_id)

    tokens_seqs: List[List[str]] = []
    bboxes_seqs: List[List[List[int]]] = []
    ner_tags_seqs: List[List[str]] = []
    receipts_seq_keys: List[Tuple[str, int]] = []

    for line_key, items in seq_map.items():
        # sort by word_id to preserve order
        items.sort(key=lambda t: t[0])
        tokens = [t[1] for t in items]
        boxes = [t[2] for t in items]
        raw_labels = [t[3] for t in items]
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
        tokens_seqs.append(tokens)
        bboxes_seqs.append(boxes)
        ner_tags_seqs.append(bio_labels)
        receipts_seq_keys.append(line_to_receipt.get(line_key, ("", 0)))

    ds_mod = importlib.import_module("datasets")
    Dataset = getattr(ds_mod, "Dataset")
    DatasetDict = getattr(ds_mod, "DatasetDict")

    dataset = Dataset.from_dict(
        {
            "tokens": tokens_seqs,
            "bboxes": bboxes_seqs,
            "ner_tags": ner_tags_seqs,
            "receipt_key": receipts_seq_keys,
        }
    )

    # Receipt-level split (90/10) by unique (image_id, receipt_id)
    unique_receipts = list({rk for rk in receipts_seq_keys})
    random.shuffle(unique_receipts)
    cut = max(1, int(len(unique_receipts) * 0.9))
    train_receipts = set(unique_receipts[:cut])
    val_receipts = set(unique_receipts[cut:])
    train_indices = [
        i for i, rk in enumerate(receipts_seq_keys) if rk in train_receipts
    ]
    val_indices = [
        i for i, rk in enumerate(receipts_seq_keys) if rk in val_receipts
    ]
    train_ds = dataset.select(train_indices).remove_columns(["receipt_key"])  # type: ignore[attr-defined]
    val_ds = dataset.select(val_indices).remove_columns(["receipt_key"])  # type: ignore[attr-defined]
    return DatasetDict({"train": train_ds, "validation": val_ds})
