import json
import random
from receipt_dynamo.entities import ReceiptWord
from receipt_label.poll_embedding_batch import (
    list_pending_embedding_batches,
    get_openai_batch_status,
    download_openai_batch_result,
    upsert_embeddings_to_pinecone,
    write_embedding_results_to_dynamo,
    mark_batch_complete,
)
from receipt_label.utils import get_clients
from collections import Counter


dynamo_client, openai_client, pinecone_index = get_clients()


# Assume `words` is a list of ReceiptWord objects with:
#   word.image_id, word.receipt_id, word.line_id, word.word_id,
#   word.calculate_centroid() → (x_center, y_center),
#   word.y_min, word.y_max  (or similar bounding‑box coords)


def get_hybrid_context(
    word: ReceiptWord,
    words: list[ReceiptWord],
    max_words_line: int = 10,
    y_thresh: float = 0.02,
) -> list[ReceiptWord]:
    """
    Returns a mix of all words on the same line (if that line is short enough),
    plus any off‑line words whose vertical position is within y_thresh of the target.

    - max_words_line: if the line has <= this many words, include the whole line.
    - y_thresh: fraction of page height (or normalized coords) to capture near‑by rows.
    """
    # 1) Line‑based group
    same_line = [w for w in words if w.line_id == word.line_id]
    if len(same_line) <= max_words_line:
        context = same_line.copy()
    else:
        # Fall back to sliding window on the line
        same_line_sorted = sorted(same_line, key=lambda w: w.word_id)
        idx = [i for i, w in enumerate(same_line_sorted) if w.word_id == word.word_id][
            0
        ]
        left = max(0, idx - 2)
        right = min(len(same_line_sorted), idx + 3)
        context = same_line_sorted[left:right]

    # 2) Spatial neighbors off the line
    _, y0 = word.calculate_centroid()
    # Collect words whose vertical centroid is within y_thresh
    spatial_neighbors = [
        w
        for w in words
        if w.line_id != word.line_id and abs(w.calculate_centroid()[1] - y0) < y_thresh
    ]

    # Merge and dedupe
    key = lambda w: (w.line_id, w.word_id)
    merged = {key(w): w for w in context + spatial_neighbors}
    return list(merged.values())


image_id = "03fa2d0f-33c6-43be-88b0-dae73ec26c93"
receipt_id = 1

# Get the receipt details
(
    receipt,
    lines,
    words,
    letters,
    tags,
    labels,
) = dynamo_client.getReceiptDetails(image_id, receipt_id)
receipt_metadata = dynamo_client.getReceiptMetadata(image_id, receipt_id)
merchant_name = receipt_metadata.merchant_name

# Randomly select a word from the receipt
word = random.choice(words)

print(word.text)
# Get the hybrid context for the word
context = get_hybrid_context(word, words)

print(" ".join([w.text for w in context]))
