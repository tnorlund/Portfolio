import json
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


def get_embedding(input_text):
    response = openai_client.embeddings.create(
        input=input_text, model="text-embedding-3-small"
    )
    return response.data[0].embedding


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

# Filter for ADDRESS_LINE labels
address_line_labels = [lbl for lbl in labels if lbl.label == "ADDRESS_LINE"]

# Map words by their (line_id, word_id)
word_map = {(w.line_id, w.word_id): w for w in words}

# Collect all words matching those ADDRESS_LINE labels
receipt_words = [
    word_map[(lbl.line_id, lbl.word_id)]
    for lbl in address_line_labels
    if (lbl.line_id, lbl.word_id) in word_map
]

for word in receipt_words:
    # Determine the label for this word
    matching_label = next(
        (
            lbl
            for lbl in labels
            if lbl.line_id == word.line_id and lbl.word_id == word.word_id
        ),
        None,
    )
    label_text = matching_label.label if matching_label else "UNKNOWN"

    # Build a 3-word window around the target word for context
    window_words = [
        w
        for w in words
        if w.line_id == word.line_id and abs(w.word_id - word.word_id) <= 1
    ]
    context = " ".join(w.text for w in window_words)

    # Build enriched input text for embedding
    centroid = word.calculate_centroid()
    x_center, y_center = centroid
    input_text = (
        f"{context} [label={label_text}] "
        f"(merchant={merchant_name}) "
        f"(pos={x_center:.2f},{y_center:.2f}) "
        f"angle={word.angle_degrees:.2f} "
        f"conf={word.confidence:.2f}"
    )
    embedding = get_embedding(input_text)

    # Query Pinecone for similar vectors, filtering by merchant
    query_response = pinecone_index.query(
        vector=embedding,
        top_k=10,
        include_metadata=True,
        filter={"merchant_name": {"$eq": merchant_name}},
    )
    # Construct the vector ID to filter out the self-match
    input_id = (
        f"IMAGE#{image_id}#"
        f"RECEIPT#{receipt_id:05d}#"
        f"LINE#{word.line_id:05d}#"
        f"WORD#{word.word_id:05d}#"
        f"LABEL#{label_text}"
    )
    # Exclude the exact self-match
    matches = [m for m in query_response.matches if m.id != input_id]

    # Show context and input for debugging
    print(f"\nWindow context: '{context}'")
    print(f"Input text: {input_text}")

    # Anomaly detection: if 5th neighbor is too dissimilar, flag it
    if len(matches) >= 5 and matches[4].score < 0.85:
        print(
            f"⚠️ Low-confidence anomaly for '{word.text}' (5th score: {matches[4].score:.4f})"
        )
    else:
        # Majority-vote on the first 5 neighbors' labels
        voted_label, vote_count = Counter(
            m.metadata["label"] for m in matches[:5]
        ).most_common(1)[0]
        print(
            f"✔️ Majority vote label for '{word.text}' is '{voted_label}' ({vote_count}/5)"
        )

    # Display the top 5 neighbor examples
    print("Top 5 neighbors:")
    for i, match in enumerate(matches[:5], start=1):
        meta = match.metadata
        print(
            f"  {i}. {meta['text']} (label: {meta['label']}) — score: {match.score:.4f}"
        )


# # List 25 receipt word labels
# labels, lek = dynamo_client.listReceiptWordLabels(
#     limit=25,
#     lastEvaluatedKey=None,
# )

# # Fetch receipt words for each label
# receipt_words = []
# keys = [label.to_ReceiptWord_key() for label in labels]
# keys = list({json.dumps(k, sort_keys=True): k for k in keys}.values())
# for i in range(0, len(keys), 25):
#     chunk = keys[i : i + 25]
#     receipt_words.extend(dynamo_client.getReceiptWordsByKeys(chunk))

# # Form the pinecone vectors
# # "{word.text} [label={label}] (pos={x:.4f},{y:.4f}) angle={angle} conf={conf}"
# vectors = []
# for label, word in zip(labels, receipt_words):
#     centroid = word.calculate_centroid()
#     input_text = (
#         f"{word.text} [label={label.label}] "
#         f"(pos={centroid[0]:.4f},{centroid[1]:.4f}) "
#         f"angle={word.angle_degrees:.2f} "
#         f"conf={word.confidence:.2f}"
#     )
#     embedding = get_embedding(input_text)
#     query_result = pinecone_index.query(
#         vector=embedding, top_k=5, include_metadata=True
#     )
#     print(f"\nWord: {word.text}  Label: {label.label}")
#     for i, match in enumerate(query_result.matches):
#         print(
#             f"{i+1}. {match.metadata.get('text')} (label: {match.metadata.get('label')}) — score: {match.score:.4f}"
#         )
