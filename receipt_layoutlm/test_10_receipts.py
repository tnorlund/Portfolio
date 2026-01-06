#!/usr/bin/env python3
"""Test the hybrid pipeline on 10 random receipts with robust filtering."""

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
import re

# Paths
MODEL_DIR = "/tmp/best_model/checkpoint-13605"
CHROMA_PATH = "/tmp/chroma_latest"
DYNAMO_TABLE = "ReceiptsTable-dc5be22"

# 10 random receipts
RECEIPTS = [
    "383a90d8-1d0e-46f4-9f8a-5f2313d9e983_2",
    "0e222ed9-5a1f-49b3-be48-483d13442881_1",
    "854e7672-d629-4cc9-8cb0-6aefa1068390_2",
    "78b51418-c0bc-497e-b02e-dd4cfbfed53e_1",
    "702a8ebf-0690-42b6-9fb5-08e231d6f13e_1",
    "490a4076-6d7c-45bf-8fcc-5570a295d429_1",
    "3442685c-4f52-4bf2-879a-5597eee79e12_1",
    "2c9b770c-9407-4cdc-b0eb-3a5b27f0af15_1",
    "ceb066a5-b621-46b6-9f8f-754d998670a7_1",
    "105a4af7-9381-418c-b1ef-be1b4f6a2991_1",
]


def is_valid_product_candidate(text):
    """Robust product candidate filter - minimal hardcoding."""
    text = text.strip()

    # Skip very short words (1-2 chars)
    if len(text) <= 2:
        return False

    # Skip pure numbers/prices/symbols
    if re.match(r'^[\d\.\$\-\+\%\:\@\/\#\*]+$', text):
        return False

    # Skip words that are mostly numbers (>60% digits)
    digits = sum(c.isdigit() for c in text)
    if len(text) > 0 and digits / len(text) > 0.6:
        return False

    return True


print("=" * 70)
print("HYBRID PIPELINE TEST - 10 Random Receipts (Robust Filtering)")
print("=" * 70)
print()

# Initialize
print("Loading model...")
inference = LayoutLMInference(model_dir=MODEL_DIR)
print(f"Model loaded on device: {inference._device}")

print("Loading ChromaDB...")
client = ChromaClient(persist_directory=CHROMA_PATH, mode="read")
collection = client.client.get_collection("words")
all_docs = collection.get(limit=15000, include=["documents", "embeddings", "metadatas"])
doc_to_idx = {doc.upper(): i for i, doc in enumerate(all_docs["documents"])}
print(f"ChromaDB loaded: {len(doc_to_idx)} words")
print()

dynamo = DynamoClient(table_name=DYNAMO_TABLE, region="us-east-1")


def lookup_product_name(word_text, exclude_image_id=None, exclude_receipt_id=None, min_validated_ratio=0.5, min_confidence=0.7, max_distance=0.25):
    """Look up if word is PRODUCT_NAME using stored embedding.

    Robust filtering via:
    - exclude_image_id, exclude_receipt_id: Exclude neighbors from this receipt (leave-one-out testing)
    - min_validated_ratio: Require X% of neighbors to be validated
    - min_confidence: Minimum confidence for prediction
    - max_distance: Maximum embedding distance to consider
    """
    idx = doc_to_idx.get(word_text.upper())
    if idx is None:
        return None, 0, []

    emb = all_docs["embeddings"][idx]
    # Query more results since we'll filter some out
    results = collection.query(
        query_embeddings=[emb],
        n_results=20,
        include=["metadatas", "distances"]
    )

    label_votes = defaultdict(float)
    vote_details = []
    validated_count = 0
    total_neighbors = 0

    for meta, dist in zip(results["metadatas"][0], results["distances"][0]):
        # Stricter distance threshold
        if dist > max_distance:
            continue

        # Exclude neighbors from the same receipt (leave-one-out)
        if exclude_image_id and exclude_receipt_id is not None:
            if meta.get("image_id") == exclude_image_id and meta.get("receipt_id") == exclude_receipt_id:
                continue

        labels = meta.get("valid_labels", "").strip(",")
        if not labels:
            continue

        total_neighbors += 1
        is_validated = meta.get("label_status") == "validated"
        if is_validated:
            validated_count += 1

        # Weight by distance and validation status
        base_weight = 1.0 / (1.0 + dist)
        validation_multiplier = 2.0 if is_validated else 1.0
        weight = base_weight * validation_multiplier

        for label in labels.split(","):
            label_votes[label] += weight

        vote_details.append({
            "validated": is_validated,
            "dist": dist,
            "weight": weight,
            "label": labels,
            "receipt_id": meta.get("receipt_id"),
        })

    if not label_votes or total_neighbors == 0:
        return None, 0, []

    # Check validation ratio
    validation_ratio = validated_count / total_neighbors
    if validation_ratio < min_validated_ratio:
        return None, 0, vote_details

    best = max(label_votes, key=label_votes.get)
    total = sum(label_votes.values())
    conf = label_votes[best] / total

    return best, conf, vote_details


# Process each receipt
all_results = []

for receipt_key in RECEIPTS:
    parts = receipt_key.rsplit("_", 1)
    image_id = parts[0]
    receipt_id = int(parts[1])

    print(f"{'=' * 70}")
    print(f"Receipt: {receipt_key}")
    print(f"{'=' * 70}")

    try:
        # Step 1: LayoutLM inference
        result = inference.predict_receipt_from_dynamo(dynamo, image_id, receipt_id)

        # Convert to word list
        words = []
        for line in result.lines:
            for token, box, label, conf in zip(
                line.tokens, line.boxes, line.labels, line.confidences
            ):
                base_label = label
                if label.startswith("B-") or label.startswith("I-"):
                    base_label = label[2:]

                words.append({
                    "text": token,
                    "bbox": {"x": box[0], "y": box[1], "width": box[2] - box[0], "height": box[3] - box[1]},
                    "label": base_label,
                    "confidence": conf,
                    "line_id": line.line_id,
                })

        print(f"Lines: {result.meta['num_lines']}, Words: {len(words)}")

        # Show LayoutLM labels
        by_label = defaultdict(list)
        for w in words:
            if w["label"] != "O":
                by_label[w["label"]].append(w["text"])

        print("LayoutLM predictions:")
        for label, texts in sorted(by_label.items()):
            print(f"  {label}: {texts[:5]}{'...' if len(texts) > 5 else ''}")

        # Step 2: Detect line item region
        amounts = [w for w in words if w["label"] == "AMOUNT"]
        region = detect_line_item_region(words, "AMOUNT", y_threshold=30.0)

        # Step 3: ChromaDB for PRODUCT_NAME
        product_names = []
        if region:
            candidates = [w for w in words
                          if w["label"] == "O"
                          and region.y_min - 30 <= w["bbox"]["y"] <= region.y_max + 30
                          and is_valid_product_candidate(w["text"])]

            for w in candidates:
                # Robust lookup with leave-one-out (exclude current receipt)
                label, conf, vote_details = lookup_product_name(
                    w["text"],
                    exclude_image_id=image_id,
                    exclude_receipt_id=receipt_id,
                    min_validated_ratio=0.5,  # At least 50% validated
                    min_confidence=0.7,        # High confidence required
                    max_distance=0.25          # Very close matches only
                )

                if label == "PRODUCT_NAME" and conf > 0.7:
                    w["label"] = "PRODUCT_NAME"
                    w["label_source"] = "chroma"
                    validated_count = sum(1 for v in vote_details if v["validated"])
                    product_names.append({
                        "text": w["text"],
                        "conf": conf,
                        "validated": validated_count,
                        "total": len(vote_details)
                    })

        print(f"\nPRODUCT_NAME from ChromaDB: {len(product_names)}")
        for p in product_names[:15]:
            print(f"  '{p['text']}' ({p['conf']:.2f}) - {p['validated']}/{p['total']} validated")
        if len(product_names) > 15:
            print(f"  ... and {len(product_names) - 15} more")

        # Step 4: Arithmetic
        words = classify_amounts_by_arithmetic(words, "AMOUNT", y_threshold=30.0)

        # Step 5: Position rules
        words = detect_quantities(words, y_threshold=30.0)
        words = detect_unit_prices(words, y_threshold=30.0)

        # Summary
        label_counts = defaultdict(int)
        for w in words:
            label = w.get("label", "O")
            subtype = w.get("amount_subtype", "")
            if label != "O":
                key = f"{label}/{subtype}" if subtype else label
                label_counts[key] += 1

        print(f"\nFinal summary:")
        for label, count in sorted(label_counts.items()):
            print(f"  {label}: {count}")

        all_results.append({
            "key": receipt_key,
            "words": len(words),
            "products": len(product_names),
            "amounts": len(amounts),
            "labels": dict(label_counts)
        })

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        all_results.append({"key": receipt_key, "error": str(e)})

    print()

client.close()

# Final summary
print("=" * 70)
print("OVERALL SUMMARY")
print("=" * 70)

total_products = 0
total_receipts = 0
for r in all_results:
    if "error" not in r:
        total_receipts += 1
        total_products += r["products"]
        print(f"{r['key'][:20]}...: {r['words']:3} words, {r['products']:2} products, {r['amounts']:2} amounts")

print()
print(f"Successfully processed: {total_receipts}/{len(RECEIPTS)}")
print(f"Total PRODUCT_NAME found: {total_products}")
print(f"Avg products per receipt: {total_products/total_receipts:.1f}" if total_receipts > 0 else "N/A")
