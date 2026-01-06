#!/usr/bin/env python3
"""Test ChromaDB labeler - quick validation with small sample."""

from receipt_chroma import ChromaClient

CHROMA_PATH = "/tmp/chroma_latest"

print("Testing ChromaDB for receipt labeling...")
print()

client = ChromaClient(persist_directory=CHROMA_PATH, mode="read")
collection = client.client.get_collection("words")

print(f"Total embeddings: {collection.count()}")
print()

# Get sample with labels to understand the data
print("=== Data Overview ===")
sample = collection.get(limit=1000, include=["metadatas"])

# Count by receipt_id
receipt_counts = {}
label_counts = {}
for meta in sample["metadatas"]:
    rid = meta.get("receipt_id", "unknown")
    receipt_counts[rid] = receipt_counts.get(rid, 0) + 1

    labels = meta.get("valid_labels", "").strip(",")
    if labels:
        for label in labels.split(","):
            if label:
                label_counts[label] = label_counts.get(label, 0) + 1

print(f"Receipt IDs in sample: {dict(sorted(receipt_counts.items(), key=lambda x: -x[1]))}")
print(f"Labels in sample: {dict(sorted(label_counts.items(), key=lambda x: -x[1]))}")
print()

# Find a receipt with fewer words for testing
print("=== Finding smaller receipt for testing ===")
# Get words from receipt 2 or 3 (smaller ones)
for test_rid in [2, 3, "2", "3"]:
    try:
        test_words = collection.get(
            where={"receipt_id": test_rid},
            limit=10,
            include=["metadatas", "documents"]
        )
        if test_words["ids"]:
            print(f"Receipt {test_rid} has words available")
            break
    except Exception as e:
        print(f"Receipt {test_rid}: {e}")
else:
    print("Using sample from receipt 1")
    test_rid = 1

# Get a small sample of labeled words to test
print()
print("=== Testing label prediction on sample ===")

# Get 50 words with labels
labeled_sample = collection.get(
    where={"label_status": "validated"},
    limit=50,
    include=["embeddings", "metadatas", "documents"]
)

print(f"Got {len(labeled_sample['ids'])} validated words for testing")
print()

def predict_label(embedding, collection, n_results=10, max_distance=1.0):
    """Predict label using nearest neighbor voting."""
    results = collection.query(
        query_embeddings=[embedding],
        n_results=n_results,
        include=["metadatas", "distances"]
    )

    label_votes = {}
    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        if dist > max_distance:
            continue
        labels = meta.get("valid_labels", "").strip(",")
        if not labels:
            continue
        weight = 1.0 / (1.0 + dist)
        for label in labels.split(","):
            if label:
                label_votes[label] = label_votes.get(label, 0) + weight

    if not label_votes:
        return None, 0.0, []

    best_label = max(label_votes, key=label_votes.get)
    total = sum(label_votes.values())
    confidence = label_votes[best_label] / total if total > 0 else 0

    return best_label, confidence, sorted(label_votes.items(), key=lambda x: -x[1])[:3]

# Test predictions
correct = 0
total = 0
by_label = {}

for doc, emb, meta in zip(
    labeled_sample["documents"],
    labeled_sample["embeddings"],
    labeled_sample["metadatas"]
):
    actual = meta.get("valid_labels", "").strip(",")
    if not actual or "," in actual:  # Skip multi-label
        continue

    predicted, conf, votes = predict_label(emb, collection)

    is_correct = predicted == actual
    if is_correct:
        correct += 1
    total += 1

    if actual not in by_label:
        by_label[actual] = {"correct": 0, "total": 0}
    by_label[actual]["total"] += 1
    if is_correct:
        by_label[actual]["correct"] += 1

    mark = "✓" if is_correct else "✗"
    print(f"  '{doc[:25]:<25}' actual={actual:<15} pred={predicted} ({conf:.2f}) {mark}")
    if not is_correct and votes:
        print(f"      top votes: {votes}")

print()
print(f"=== Summary ===")
print(f"Overall: {correct}/{total} = {correct/total*100:.1f}%" if total > 0 else "No samples tested")
print()
print("Per-label:")
for label, stats in sorted(by_label.items()):
    acc = stats["correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
    print(f"  {label:<20}: {stats['correct']}/{stats['total']} = {acc:.0f}%")

client.close()
print()
print("Done!")
