#!/usr/bin/env python3
"""Test the full hybrid pipeline with real inference using today's best model."""

import sys
sys.path.insert(0, "/Users/tnorlund/portfolio_sagemaker/receipt_layoutlm")

from receipt_dynamo import DynamoClient
from receipt_layoutlm.inference import LayoutLMInference
from receipt_layoutlm.chroma_labeler import (
    classify_amounts_by_arithmetic,
    detect_line_item_region,
    detect_quantities,
    detect_unit_prices,
)
from receipt_chroma import ChromaClient
from collections import defaultdict

# Paths
MODEL_DIR = "/tmp/best_model/checkpoint-13605"
CHROMA_PATH = "/tmp/chroma_latest"
DYNAMO_TABLE = "ReceiptsTable-dc5be22"

print("=" * 60)
print("HYBRID PIPELINE TEST - Real Inference")
print("=" * 60)
print()

# Initialize model
print("Loading model from:", MODEL_DIR)
inference = LayoutLMInference(model_dir=MODEL_DIR)
print(f"Model loaded on device: {inference._device}")
print(f"Labels: {list(inference._model.config.id2label.values())}")
print()

# Get a receipt from DynamoDB
print("Fetching receipt from DynamoDB...")
dynamo = DynamoClient(table_name=DYNAMO_TABLE, region="us-east-1")

# List receipts and pick one
page = dynamo.list_receipt_details(limit=5)
keys = list(page.summaries.keys())
print(f"Available receipts: {keys[:5]}")

# Parse the key (format: image_id_receipt_id)
key = keys[0]
parts = key.rsplit("_", 1)
image_id = parts[0]
receipt_id = int(parts[1])

print(f"Using: image_id={image_id}, receipt_id={receipt_id}")
print()

# Run inference
print("=== STEP 1: LayoutLM Inference ===")
result = inference.predict_receipt_from_dynamo(dynamo, image_id, receipt_id)

print(f"Lines: {result.meta['num_lines']}, Words: {result.meta['num_words']}")

# Convert to word list format for hybrid pipeline
words = []
for line in result.lines:
    for i, (token, box, label, conf) in enumerate(zip(
        line.tokens, line.boxes, line.labels, line.confidences
    )):
        # Strip BIO prefix
        base_label = label
        if label.startswith("B-") or label.startswith("I-"):
            base_label = label[2:]

        words.append({
            "text": token,
            "bbox": {"x": box[0], "y": box[1], "width": box[2] - box[0], "height": box[3] - box[1]},
            "label": base_label,
            "confidence": conf,
            "line_id": line.line_id,
            "bio_label": label,
        })

# Show LayoutLM results
by_label = defaultdict(list)
for w in words:
    if w["label"] != "O":
        by_label[w["label"]].append(w["text"])

print("\nLayoutLM predictions:")
for label, texts in sorted(by_label.items()):
    print(f"  {label}: {texts[:8]}{'...' if len(texts) > 8 else ''}")

amounts = [w for w in words if w["label"] == "AMOUNT"]
print(f"\nAMOUNT tokens: {len(amounts)}")
if amounts:
    print(f"  Values: {[w['text'] for w in amounts[:15]]}")
print()

# Step 2: Detect line item region
print("=== STEP 2: Detect Line Item Region ===")
region = detect_line_item_region(words, "AMOUNT", y_threshold=30.0)
if region:
    print(f"Line item region: y={region.y_min:.0f} to {region.y_max:.0f} ({region.line_count} lines)")
else:
    print("No line item region detected")
print()

# Step 3: ChromaDB for PRODUCT_NAME
print("=== STEP 3: ChromaDB for PRODUCT_NAME ===")

client = ChromaClient(persist_directory=CHROMA_PATH, mode="read")
collection = client.client.get_collection("words")

# Load embeddings for lookup
all_docs = collection.get(limit=15000, include=["documents", "embeddings", "metadatas"])
doc_to_idx = {doc.upper(): i for i, doc in enumerate(all_docs["documents"])}

def lookup_product_name(word_text, current_merchant=None):
    """Look up if word is PRODUCT_NAME using stored embedding with metadata ranking.

    Uses metadata to improve ranking:
    - Validated labels weighted 2x higher than unvalidated
    - Same merchant matches weighted 1.5x higher
    - Distance-based weighting as before
    """
    idx = doc_to_idx.get(word_text.upper())
    if idx is None:
        return None, 0, []

    emb = all_docs["embeddings"][idx]
    results = collection.query(
        query_embeddings=[emb],
        n_results=10,
        include=["metadatas", "distances"]
    )

    label_votes = defaultdict(float)
    vote_details = []  # Track what contributed to votes

    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        if dist > 1.0:
            continue
        labels = meta.get("valid_labels", "").strip(",")
        if not labels:
            continue

        # Base weight from distance
        base_weight = 1.0 / (1.0 + dist)

        # Boost validated labels (human-verified)
        validation_multiplier = 2.0 if meta.get("label_status") == "validated" else 1.0

        # Boost same-merchant matches
        merchant_multiplier = 1.0
        if current_merchant and meta.get("merchant_name"):
            if current_merchant.lower() in meta.get("merchant_name", "").lower():
                merchant_multiplier = 1.5

        weight = base_weight * validation_multiplier * merchant_multiplier

        for label in labels.split(","):
            label_votes[label] += weight

        vote_details.append({
            "word": meta.get("left", "") + " [" + labels + "] " + meta.get("right", ""),
            "label": labels,
            "dist": dist,
            "validated": meta.get("label_status") == "validated",
            "merchant": meta.get("merchant_name", ""),
            "weight": weight
        })

    if not label_votes:
        return None, 0, []

    best = max(label_votes, key=label_votes.get)
    total = sum(label_votes.values())
    conf = label_votes[best] / total
    return best, conf, vote_details

# Find unlabeled words in line item region (skip pure numbers and non-product words)
import re

# Words that are never products
SKIP_WORDS = {
    # Common short words
    "a", "an", "the", "of", "or", "and", "to", "in", "on", "at", "by", "for", "is", "it",
    "us", "t", "x", "w", "s", "n", "e", "i", "o", "u",
    # Receipt common words
    "tax", "total", "subtotal", "change", "cash", "card", "credit", "debit",
    "balance", "due", "paid", "amount", "price", "qty", "item", "items",
    "member", "value", "save", "saving", "savings", "discount", "off",
    "thank", "you", "thanks", "welcome", "back", "visit", "again",
    "receipt", "store", "date", "time", "ref", "trans", "aid", "crv",
}

def is_valid_product_candidate(text):
    """Check if word is a valid product name candidate."""
    text_lower = text.lower().strip()

    # Skip short words (1-2 chars)
    if len(text_lower) <= 2:
        return False

    # Skip pure numbers or prices
    if re.match(r'^[\d\.\$\-\+\%\:\@]+$', text):
        return False

    # Skip common non-product words
    if text_lower in SKIP_WORDS:
        return False

    # Skip words that are mostly numbers
    digits = sum(c.isdigit() for c in text)
    if len(text) > 0 and digits / len(text) > 0.5:
        return False

    return True

if region:
    candidates = [w for w in words
                  if w["label"] == "O"
                  and region.y_min - 30 <= w["bbox"]["y"] <= region.y_max + 30
                  and is_valid_product_candidate(w["text"])]

    print(f"Unlabeled product candidates in region: {len(candidates)}")
    print()
    print("Metadata-enhanced ranking (validated=2x, same-merchant=1.5x):")
    product_count = 0
    for w in candidates:
        label, conf, vote_details = lookup_product_name(w["text"])
        # Higher confidence threshold for product names
        if label == "PRODUCT_NAME" and conf > 0.6:
            w["label"] = "PRODUCT_NAME"
            w["label_source"] = "chroma"
            product_count += 1
            if product_count <= 10:
                # Show top contributors
                validated_count = sum(1 for v in vote_details if v["validated"])
                print(f"  '{w['text']}' -> PRODUCT_NAME ({conf:.2f})")
                print(f"      {validated_count}/{len(vote_details)} neighbors validated")
                if vote_details[:2]:
                    for v in vote_details[:2]:
                        val_mark = "[V]" if v["validated"] else "[U]"
                        print(f"        {val_mark} d={v['dist']:.3f} w={v['weight']:.2f}: {v['word'][:40]}")

    if product_count > 10:
        print(f"  ... and {product_count - 10} more")
    print(f"\nTotal PRODUCT_NAME from ChromaDB: {product_count}")

client.close()
print()

# Step 4: Arithmetic for amount subtypes
print("=== STEP 4: Arithmetic for Amount Subtypes ===")
words = classify_amounts_by_arithmetic(words, "AMOUNT", y_threshold=30.0)

subtypes = defaultdict(list)
for w in words:
    subtype = w.get("amount_subtype")
    if subtype:
        subtypes[subtype].append(w["text"])

for subtype, values in sorted(subtypes.items()):
    print(f"  {subtype}: {values[:5]}{'...' if len(values) > 5 else ''}")
print()

# Step 5: Position rules
print("=== STEP 5: Position Rules ===")
words = detect_quantities(words, y_threshold=30.0)
words = detect_unit_prices(words, y_threshold=30.0)

qty = [w for w in words if w.get("label") == "QUANTITY"]
print(f"QUANTITY: {[w['text'] for w in qty[:10]]}")
print()

# Final summary
print("=" * 60)
print("FINAL LABELED RECEIPT (first 40 lines)")
print("=" * 60)

by_line = defaultdict(list)
for w in words:
    by_line[w.get("line_id", 0)].append(w)

for line_id in sorted(by_line.keys())[:40]:
    line_words = sorted(by_line[line_id], key=lambda w: w["bbox"]["x"])
    labeled = [(w["text"], w.get("label", "O"), w.get("amount_subtype", ""))
               for w in line_words if w.get("label", "O") != "O" or w.get("amount_subtype")]
    if labeled:
        line_str = " | ".join(f"{t}:{l}{'/'+s if s else ''}" for t, l, s in labeled)
        print(f"Line {line_id:3}: {line_str}")

print()
print("=== SUMMARY ===")
label_counts = defaultdict(int)
for w in words:
    label = w.get("label", "O")
    subtype = w.get("amount_subtype", "")
    if label != "O":
        key = f"{label}/{subtype}" if subtype else label
        label_counts[key] += 1

for label, count in sorted(label_counts.items()):
    print(f"  {label}: {count}")

print("\nDone!")
