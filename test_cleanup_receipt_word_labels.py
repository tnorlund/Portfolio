import json
from receipt_dynamo.entities import ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus
import random
from receipt_label.utils import get_clients
from collections import Counter
import logging
import os


def cleanup_labels(
    rows: list[ReceiptWordLabel],
) -> tuple[list[ReceiptWordLabel], list[ReceiptWordLabel]]:
    """
    New policy for rows produced by the *invalid‑path* consolidation:

    • DELETE every row that was *inserted* by the batch:
        validation_status in {NONE, VALID}  AND  label_consolidated_from is not None
    • RESET the original rows (those whose label matches `.label_consolidated_from`)
      to PENDING (ValidationStatus.NONE).

    Returns (to_delete, to_update)
    """
    # index originals by word coordinates and label for quick lookup
    originals: dict[tuple[int, int, str], ReceiptWordLabel] = (
        {}
    )  # (line, word, label) -> row

    to_delete, to_update = [], []

    for r in rows:
        coord_key = (r.line_id, r.word_id, r.label)
        if r.label_consolidated_from:
            # This row is a *new* candidate – schedule for deletion
            to_delete.append(r)

            # Locate the corresponding original
            orig_key = (r.line_id, r.word_id, r.label_consolidated_from)
            originals_row = originals.get(orig_key)
            if originals_row:
                # reset later
                pass
        else:
            originals[(r.line_id, r.word_id, r.label)] = r

    # second pass: for each row we decided to delete, find its original and reset
    for doomed in to_delete:
        orig_key = (doomed.line_id, doomed.word_id, doomed.label_consolidated_from)
        orig = originals.get(orig_key)
        if orig and orig.validation_status != ValidationStatus.NONE.value:
            orig.validation_status = ValidationStatus.NONE.value
            to_update.append(orig)

    return to_delete, to_update


S3_BUCKET = os.environ["S3_BUCKET"]

dynamo_client, openai_client, pinecone_index = get_clients()

image_id = "03fa2d0f-33c6-43be-88b0-dae73ec26c93"
receipt_id = 1

details = dynamo_client.getReceiptDetails(image_id, receipt_id)
labels = details[-1]

# Get unique combinations of (line_id, word_id)
unique_coords = set((r.line_id, r.word_id) for r in labels)

labels_to_update = []
labels_to_delete = []
for coord in unique_coords:
    word_labels = [r for r in labels if r.line_id == coord[0] and r.word_id == coord[1]]
    if len(word_labels) == 3:
        # Sort by timestamp_added
        word_labels.sort(key=lambda x: x.timestamp_added)
        # Only keep the first one
        word_to_update = word_labels[0]
        word_to_update.validation_status = ValidationStatus.PENDING.value
        labels_to_update.append(word_to_update)
        labels_to_delete.append(word_labels[1])
        labels_to_delete.append(word_labels[2])
    elif len(word_labels) == 2:
        word_to_delete = next(
            r for r in word_labels if r.validation_status == ValidationStatus.NONE.value
        )
        word_to_update = next(
            r
            for r in word_labels
            if r.validation_status == ValidationStatus.INVALID.value
        )
        word_to_update.validation_status = ValidationStatus.PENDING.value
        labels_to_update.append(word_to_update)
        labels_to_delete.append(word_to_delete)
    elif len(word_labels) == 1:
        word_labels[0].validation_status = ValidationStatus.PENDING.value
        labels_to_update.append(word_labels[0])
    else:
        print(f"Unexpected number of word labels: {len(word_labels)}")

print(f"Labels to update: {len(labels_to_update)}")
print(f"Labels to delete: {len(labels_to_delete)}")

dynamo_client.updateReceiptWordLabels(labels_to_update)
dynamo_client.deleteReceiptWordLabels(labels_to_delete)

# valid_labels = [
#     r for r in labels if r.validation_status == ValidationStatus.VALID.value
# ]
# invalid_labels = [
#     r for r in labels if r.validation_status == ValidationStatus.INVALID.value
# ]
# none_labels = [r for r in labels if r.validation_status == ValidationStatus.NONE.value]
# print(f"Valid: {len(valid_labels)}")
# print(f"Invalid: {len(invalid_labels)}")
# print(f"None: {len(none_labels)}")

# # # for label in labels:
# # #     print(label)

# # import json, datetime, pathlib

# # # backup_rows = dynamo_client.getReceiptWordLabels(image_id, receipt_id)
# # stamp = datetime.datetime.utcnow().isoformat(timespec="seconds").replace(":", "")
# # backup_path = pathlib.Path(f"backup_rwl_{image_id}_{stamp}.json")
# # backup_path.write_text(json.dumps([dict(row) for row in labels], indent=2))
# # print(f"✓ Backup written to {backup_path}")

# # to_delete, to_update = cleanup_labels(labels)

# # print(f"Will delete {len(to_delete)} rows, update {len(to_update)} rows")
