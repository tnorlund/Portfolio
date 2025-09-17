from typing import Any, Dict, List, Tuple

import importlib

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus


def _normalize_box(word) -> List[int]:
    # LayoutLM expects [x0, y0, x1, y1] scaled to 0-1000 ints
    # Our words provide corners; compute min/max and scale by image size if available
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
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    # Assume coordinates are already 0..1 normalized; scale to 0..1000
    return [int(x0 * 1000), int(y0 * 1000), int(x1 * 1000), int(y1 * 1000)]


def _bio_label_for_word(labels: List[str]) -> str:
    # Simplest consolidation: pick first label, convert to "B-<LABEL>"
    # In future: consolidate by validation status and frequency
    if not labels:
        return "O"
    return f"B-{labels[0].upper()}"


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

    # Group by line so each example is a sequence of tokens
    seq_map: Dict[
        Tuple[str, int, int], List[Tuple[int, str, List[int], str]]
    ] = {}
    for w in words:
        word_key = (w.image_id, w.receipt_id, w.line_id, w.word_id)
        line_key = (w.image_id, w.receipt_id, w.line_id)
        label = _bio_label_for_word(label_map.get(word_key, []))
        seq_map.setdefault(line_key, []).append(
            (w.word_id, w.text, _normalize_box(w), label)
        )

    tokens_seqs: List[List[str]] = []
    bboxes_seqs: List[List[List[int]]] = []
    ner_tags_seqs: List[List[str]] = []

    for line_key, items in seq_map.items():
        # sort by word_id to preserve order
        items.sort(key=lambda t: t[0])
        tokens_seqs.append([t[1] for t in items])
        bboxes_seqs.append([t[2] for t in items])
        ner_tags_seqs.append([t[3] for t in items])

    ds_mod = importlib.import_module("datasets")
    Dataset = getattr(ds_mod, "Dataset")
    DatasetDict = getattr(ds_mod, "DatasetDict")

    dataset = Dataset.from_dict(
        {
            "tokens": tokens_seqs,
            "bboxes": bboxes_seqs,
            "ner_tags": ner_tags_seqs,
        }
    )

    # Placeholder: split 90/10
    n = len(tokens_seqs)
    split = max(1, int(n * 0.9))
    train_ds = dataset.select(range(0, split))
    val_ds = dataset.select(range(split, n))
    return DatasetDict({"train": train_ds, "validation": val_ds})
