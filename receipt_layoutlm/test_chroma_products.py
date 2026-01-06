#!/usr/bin/env python3
"""Test ChromaDB for PRODUCT_NAME and QUANTITY classification."""

from receipt_chroma import ChromaClient
from collections import defaultdict

CHROMA_PATH = "/tmp/chroma_latest"

print("Testing ChromaDB for PRODUCT_NAME and QUANTITY...")
print()

client = ChromaClient(persist_directory=CHROMA_PATH, mode="read")
collection = client.client.get_collection("words")

# Get sample with embeddings
print("=== Data Overview ===")
sample = collection.get(limit=5000, include=["metadatas", "documents", "embeddings"])

# Count labels
label_counts = defaultdict(int)
product_examples = []
quantity_examples = []

for doc, meta, emb in zip(sample["documents"], sample["metadatas"], sample["embeddings"]):
    labels = meta.get("valid_labels", "").strip(",")
    if not labels:
        continue

    for label in labels.split(","):
        label_counts[label] += 1

    if labels == "PRODUCT_NAME":
        product_examples.append((doc, emb))
    elif labels == "QUANTITY":
        quantity_examples.append((doc, emb))

print(f"PRODUCT_NAME examples: {len(product_examples)}")
print(f"QUANTITY examples: {len(quantity_examples)}")
print()

# Show some examples
print("=== PRODUCT_NAME examples ===")
for doc, _ in product_examples[:15]:
    print(f"  '{doc}'")
print()

print("=== QUANTITY examples ===")
for doc, _ in quantity_examples[:15]:
    print(f"  '{doc}'")
print()

# Test PRODUCT_NAME prediction
print("=== Testing PRODUCT_NAME prediction ===")

def predict_label(embedding, collection, n_results=10, target_labels=None):
    """Predict label from ChromaDB."""
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
        if not labels:
            continue
        weight = 1.0 / (1.0 + dist)
        for label in labels.split(","):
            if target_labels is None or label in target_labels:
                label_votes[label] += weight

    if not label_votes:
        return None, 0.0, []

    best = max(label_votes, key=label_votes.get)
    total = sum(label_votes.values())
    conf = label_votes[best] / total if total > 0 else 0
    top = sorted(label_votes.items(), key=lambda x: -x[1])[:3]
    return best, conf, top

# Test PRODUCT_NAME - use 30 examples
test_products = product_examples[:30]
correct = 0
for doc, emb in test_products:
    predicted, conf, votes = predict_label(emb, collection)
    is_correct = predicted == "PRODUCT_NAME"
    if is_correct:
        correct += 1
    mark = "✓" if is_correct else "✗"
    print(f"  '{doc:<20}' -> {predicted:<15} ({conf:.2f}) {mark}")
    if not is_correct:
        print(f"      votes: {votes[:3]}")

print(f"\nPRODUCT_NAME accuracy: {correct}/{len(test_products)} = {correct/len(test_products)*100:.0f}%")
print()

# Test QUANTITY prediction
print("=== Testing QUANTITY prediction ===")
test_quantities = quantity_examples[:15]
correct = 0
for doc, emb in test_quantities:
    predicted, conf, votes = predict_label(emb, collection)
    is_correct = predicted == "QUANTITY"
    if is_correct:
        correct += 1
    mark = "✓" if is_correct else "✗"
    print(f"  '{doc:<20}' -> {predicted:<15} ({conf:.2f}) {mark}")
    if not is_correct:
        print(f"      votes: {votes[:3]}")

if test_quantities:
    print(f"\nQUANTITY accuracy: {correct}/{len(test_quantities)} = {correct/len(test_quantities)*100:.0f}%")
print()

# Test with some synthetic product names
print("=== Testing unseen product-like words ===")
# These won't have embeddings, so we need to generate them
# For now, let's find similar words in the DB

test_words = ["BANANA", "MILK", "BREAD", "CHICKEN", "APPLE", "CHEESE", "EGGS"]
print("(Searching for similar words in DB)")

for word in test_words:
    # Find a word in DB that contains this
    matches = []
    for doc, meta, emb in zip(sample["documents"], sample["metadatas"], sample["embeddings"]):
        if word.lower() in doc.lower():
            labels = meta.get("valid_labels", "").strip(",")
            matches.append((doc, labels, emb))
            if len(matches) >= 1:
                break

    if matches:
        doc, actual, emb = matches[0]
        predicted, conf, votes = predict_label(emb, collection)
        mark = "✓" if predicted == "PRODUCT_NAME" else "✗"
        print(f"  '{doc:<25}' actual={actual:<15} pred={predicted:<15} ({conf:.2f}) {mark}")
    else:
        print(f"  '{word}' - not found in DB")

client.close()
print("\nDone!")
