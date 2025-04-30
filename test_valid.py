import random
import pandas as pd
from receipt_dynamo.constants import (
    ValidationStatus,
    PassNumber,
    BatchType,
    BatchStatus,
)
from receipt_label.utils.clients import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()
labels, _ = dynamo_client.getReceiptWordLabelsByValidationStatus(
    validation_status=ValidationStatus.VALID.value
)

# Get words that need review
word_indices = [
    (label.image_id, label.receipt_id, label.line_id, label.word_id) for label in labels
]
word_indices = list(set(word_indices))
words = dynamo_client.getReceiptWordsByIndices(word_indices)

print(f"Found {len(labels)} labels that are valid")
print(f"Found {len(words)} words that are valid")

# Get unique combinations of image_id and receipt_id
unique_image_receipt_combinations = list(
    set((label.image_id, label.receipt_id) for label in labels)
)
metadatas = dynamo_client.getReceiptMetadatasByIndices(
    unique_image_receipt_combinations
)


# Combine labels (NEEDS_REVIEW) with their corresponding words
# ------------------------------------------------------------
word_by_idx = {(w.image_id, w.receipt_id, w.line_id, w.word_id): w for w in words}

combined = []
for lbl in labels:
    idx = (lbl.image_id, lbl.receipt_id, lbl.line_id, lbl.word_id)
    word_entity = word_by_idx.get(idx)
    if word_entity is None:
        continue  # Orphaned label – skip

    # 🔍 Fetch ALL labels attached to this word
    all_lbls, _ = dynamo_client.getReceiptWordLabelsForWord(
        lbl.image_id,
        lbl.receipt_id,
        lbl.line_id,
        lbl.word_id,
    )
    # Summarise: "LABEL:STATUS"
    label_summary = [f"{l.label}:{l.validation_status}" for l in all_lbls]
    metadata = next(
        (
            m
            for m in metadatas
            if m.image_id == lbl.image_id and m.receipt_id == lbl.receipt_id
        ),
        None,
    )
    merchant_name = metadata.merchant_name if metadata else None

    combined.append(
        {
            "image_id": lbl.image_id,
            "receipt_id": lbl.receipt_id,
            "line_id": lbl.line_id,
            "word_id": lbl.word_id,
            "word_text": getattr(word_entity, "text", ""),
            "needs_review_label": lbl.label,
            "proposed_by": getattr(lbl, "label_proposed_by", None),
            "all_labels": label_summary,  # ← NEW
            "merchant_name": merchant_name,
        }
    )

# ------------------------------------------------------------
# Save to CSV
# ------------------------------------------------------------
df = pd.DataFrame(combined)
csv_path = "valid_labels.csv"
df.to_csv(csv_path, index=False)
print(f"✅ Saved {len(df)} rows to {csv_path}")
