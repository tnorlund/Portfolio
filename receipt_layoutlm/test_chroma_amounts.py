#!/usr/bin/env python3
"""Test ChromaDB for amount subtype classification."""

from receipt_chroma import ChromaClient
from collections import defaultdict

CHROMA_PATH = "/tmp/chroma_latest"

print("Testing ChromaDB for amount labels...")
print()

client = ChromaClient(persist_directory=CHROMA_PATH, mode="read")
collection = client.client.get_collection("words")

# What amount-related labels exist in the data?
print("=== Amount-related labels in ChromaDB ===")
sample = collection.get(limit=5000, include=["metadatas", "documents"])

amount_labels = ["LINE_TOTAL", "UNIT_PRICE", "SUBTOTAL", "TAX", "GRAND_TOTAL",
                 "QUANTITY", "TENDER", "DISCOUNT", "AMOUNT"]

label_examples = defaultdict(list)
for doc, meta in zip(sample["documents"], sample["metadatas"]):
    labels = meta.get("valid_labels", "").strip(",")
    if labels:
        for label in labels.split(","):
            if label in amount_labels:
                label_examples[label].append(doc)

print("Labels found:")
for label in amount_labels:
    examples = label_examples.get(label, [])
    print(f"  {label:<15}: {len(examples):>4} examples")
    if examples[:5]:
        print(f"                   e.g., {examples[:5]}")
print()

# Test: Can ChromaDB distinguish amount subtypes?
print("=== Testing amount subtype prediction ===")

# Get some labeled amounts to test
test_cases = []
for doc, meta, emb in zip(sample["documents"], sample["metadatas"],
                          collection.get(limit=5000, include=["embeddings"])["embeddings"]):
    labels = meta.get("valid_labels", "").strip(",")
    if labels in amount_labels and labels not in ["AMOUNT"]:  # Skip generic AMOUNT
        test_cases.append((doc, labels, emb))
    if len(test_cases) >= 30:
        break

if not test_cases:
    print("No amount subtype labels found in sample!")
    client.close()
    exit()

print(f"Testing {len(test_cases)} labeled amounts\n")

def predict_amount_type(embedding, collection, n_results=10):
    """Predict amount subtype from ChromaDB."""
    results = collection.query(
        query_embeddings=[embedding],
        n_results=n_results,
        include=["metadatas", "distances", "documents"]
    )

    label_votes = defaultdict(float)
    for meta, dist, doc in zip(results["metadatas"][0],
                                results["distances"][0],
                                results["documents"][0]):
        labels = meta.get("valid_labels", "").strip(",")
        if not labels or labels == "AMOUNT":
            continue
        # Only count amount-related labels
        for label in labels.split(","):
            if label in amount_labels:
                weight = 1.0 / (1.0 + dist)
                label_votes[label] += weight

    if not label_votes:
        return None, 0.0, []

    best = max(label_votes, key=label_votes.get)
    total = sum(label_votes.values())
    conf = label_votes[best] / total if total > 0 else 0
    top = sorted(label_votes.items(), key=lambda x: -x[1])[:3]
    return best, conf, top

# Test predictions
correct = 0
by_label = defaultdict(lambda: {"correct": 0, "total": 0})

for doc, actual, emb in test_cases:
    predicted, conf, votes = predict_amount_type(emb, collection)

    is_correct = predicted == actual
    if is_correct:
        correct += 1
    by_label[actual]["total"] += 1
    if is_correct:
        by_label[actual]["correct"] += 1

    mark = "✓" if is_correct else "✗"
    print(f"  '{doc:<12}' actual={actual:<12} pred={predicted:<12} ({conf:.2f}) {mark}")
    if not is_correct and votes:
        print(f"      votes: {votes}")

print()
print(f"=== Summary ===")
print(f"Overall: {correct}/{len(test_cases)} = {correct/len(test_cases)*100:.0f}%")
print()
print("Per-label:")
for label in sorted(by_label.keys()):
    stats = by_label[label]
    acc = stats["correct"] / stats["total"] * 100 if stats["total"] > 0 else 0
    print(f"  {label:<15}: {stats['correct']}/{stats['total']} = {acc:.0f}%")

client.close()
print("\nDone!")
