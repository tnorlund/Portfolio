import hashlib
import importlib
import os
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import CORE_LABELS, ValidationStatus


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


def _ranges_overlap(
    a_min: float, a_max: float, b_min: float, b_max: float
) -> bool:
    """Check if two 1D ranges overlap."""
    return a_min < b_max and b_min < a_max


def _find_right_neighbor(
    word: WordInfo, all_words: List[WordInfo]
) -> Optional[int]:
    """Find the index of the word directly to the right of this word.

    Returns the closest word that:
    - Has x_min > word's x_max (is to the right)
    - Has overlapping y-range (on same visual line)
    """
    word_x_max = word.bbox[2]
    word_y_min, word_y_max = word.bbox[1], word.bbox[3]

    best_idx: Optional[int] = None
    best_distance = float("inf")

    for i, other in enumerate(all_words):
        if other is word:
            continue

        other_x_min = other.bbox[0]
        other_y_min, other_y_max = other.bbox[1], other.bbox[3]

        # Must be to the right
        if other_x_min <= word_x_max:
            continue

        # Must have overlapping y-range (same visual line)
        if not _ranges_overlap(
            word_y_min, word_y_max, other_y_min, other_y_max
        ):
            continue

        distance = other_x_min - word_x_max
        if distance < best_distance:
            best_distance = distance
            best_idx = i

    return best_idx


def _find_below_neighbor(
    word: WordInfo, all_words: List[WordInfo]
) -> Optional[int]:
    """Find the index of the word directly below this word.

    Returns the closest word that:
    - Has y_min > word's y_max (is below)
    - Has overlapping x-range (vertically aligned)
    """
    word_y_max = word.bbox[3]
    word_x_min, word_x_max = word.bbox[0], word.bbox[2]

    best_idx: Optional[int] = None
    best_distance = float("inf")

    for i, other in enumerate(all_words):
        if other is word:
            continue

        other_y_min = other.bbox[1]
        other_x_min, other_x_max = other.bbox[0], other.bbox[2]

        # Must be below
        if other_y_min <= word_y_max:
            continue

        # Must have overlapping x-range (vertically aligned)
        if not _ranges_overlap(
            word_x_min, word_x_max, other_x_min, other_x_max
        ):
            continue

        distance = other_y_min - word_y_max
        if distance < best_distance:
            best_distance = distance
            best_idx = i

    return best_idx


def _group_words_into_blocks(words: List[WordInfo]) -> List[List[WordInfo]]:
    """Group words into spatially contiguous blocks of the same ORIGINAL label.

    Uses spatial adjacency (right neighbor, below neighbor) to build a graph,
    then finds connected components where all words share the same original label.

    Grouping by original_label (before merging) ensures that:
    - Multi-line addresses (all ADDRESS_LINE) are grouped together
    - Separate amounts (SUBTOTAL, TAX, GRAND_TOTAL) stay as separate blocks
      even though they merge to the same AMOUNT label

    Args:
        words: List of WordInfo objects to group.

    Returns:
        List of word groups, each containing words of the same original label.
    """
    if not words:
        return []

    n = len(words)

    # Build adjacency list: connect words with same ORIGINAL label that are neighbors
    adjacency: List[List[int]] = [[] for _ in range(n)]

    for i, word in enumerate(words):
        # Find right neighbor
        right_idx = _find_right_neighbor(word, words)
        if (
            right_idx is not None
            and words[right_idx].original_label == word.original_label
        ):
            adjacency[i].append(right_idx)
            adjacency[right_idx].append(i)

        # Find below neighbor
        below_idx = _find_below_neighbor(word, words)
        if (
            below_idx is not None
            and words[below_idx].original_label == word.original_label
        ):
            adjacency[i].append(below_idx)
            adjacency[below_idx].append(i)

    # Find connected components using BFS
    visited = [False] * n
    blocks: List[List[WordInfo]] = []

    for start in range(n):
        if visited[start]:
            continue

        # BFS to find all words in this component
        component: List[WordInfo] = []
        queue: deque[int] = deque([start])
        visited[start] = True

        while queue:
            idx = queue.popleft()
            component.append(words[idx])

            for neighbor in adjacency[idx]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    queue.append(neighbor)

        blocks.append(component)

    return blocks


def _build_block_example(
    block: List[WordInfo],
    receipt_key: str,
) -> LineExample:
    """Build a LineExample from a word block with proper BIO tagging.

    Args:
        block: List of WordInfo objects (all same label) to convert.
        receipt_key: The receipt key for this example.

    Returns:
        LineExample with proper BIO tags across the entire block.
    """
    # Sort words by y then x for reading order
    sorted_words = sorted(block, key=lambda w: (w.bbox[1], w.bbox[0]))

    tokens: List[str] = []
    bboxes: List[List[int]] = []
    bio_labels: List[str] = []

    # All words in a block have the same label
    label = sorted_words[0].label
    for i, word in enumerate(sorted_words):
        tokens.append(word.text)
        bboxes.append(word.bbox)

        if label == "O":
            bio_labels.append("O")
        else:
            # First word is B-, rest are I-
            bio_labels.append("B-" + label if i == 0 else "I-" + label)

    # Use first word's IDs for the example
    first_word = sorted_words[0]

    return LineExample(
        image_id=first_word.image_id,
        receipt_id=first_word.receipt_id,
        line_id=first_word.line_id,  # Use first word's line_id
        tokens=tokens,
        bboxes=bboxes,
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

    # Build examples from spatial blocks within each receipt
    examples: List[LineExample] = []

    for (img_id, rec_id), words_in_receipt in receipt_words.items():
        receipt_key = f"{img_id}#{rec_id:05d}"

        # Group words into spatially contiguous blocks of the same label
        blocks = _group_words_into_blocks(words_in_receipt)

        # Build an example from each block
        for block in blocks:
            examples.append(_build_block_example(block, receipt_key))

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

    unique_receipts = sorted(
        {ex.receipt_key for ex in examples}
    )  # Sort for determinism
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
            o_tokens_in_entity_lines += sum(
                1 for tag in ex.ner_tags if tag == "O"
            )
        else:
            o_only_lines_count += 1
            o_only_tokens_total += len(ex.tokens)

    # Compute O:entity ratio before downsampling
    total_o_tokens_before = o_tokens_in_entity_lines + o_only_tokens_total
    o_entity_ratio_before = (
        total_o_tokens_before / entity_tokens
        if entity_tokens > 0
        else float("inf")
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
        total_o_tokens_after / entity_tokens
        if entity_tokens > 0
        else float("inf")
    )

    # Compute validation O:entity ratio
    val_entity_tokens = 0
    val_o_tokens = 0
    for idx in val_indices:
        ex = examples[idx]
        val_entity_tokens += sum(1 for tag in ex.ner_tags if tag != "O")
        val_o_tokens += sum(1 for tag in ex.ner_tags if tag == "O")
    o_entity_ratio_val = (
        val_o_tokens / val_entity_tokens
        if val_entity_tokens > 0
        else float("inf")
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

    return (
        DatasetDict({"train": train_ds, "validation": val_ds}),
        split_metadata,
        merge_info,
    )
