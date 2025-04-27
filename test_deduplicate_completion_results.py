from datetime import datetime, timezone
from receipt_dynamo.constants import ValidationStatus


def update_invalid_labels(
    invalid_results: list,
    dynamo_client,
):
    labels_to_update = []
    new_labels_to_create = []

    # ------------------------------------------------------------------
    # Guard against duplicate “new label” insertions for the *same*
    # (image, receipt, line, word, proposed_label) key.
    # We’ll keep only the first instance that appears in `invalid_results`.
    seen_new_label_keys: set[tuple] = set()
    # ------------------------------------------------------------------

    for result in invalid_results:
        if result.correct_label:
            key = (
                result.image_id,
                result.receipt_id,
                result.line_id,
                result.word_id,
                result.correct_label,
            )
            if key in seen_new_label_keys:
                # Duplicate – skip to avoid writing the same label twice
                continue
            seen_new_label_keys.add(key)

            from receipt_dynamo.entities import ReceiptWordLabel

            new_label = ReceiptWordLabel(
                image_id=result.image_id,
                receipt_id=result.receipt_id,
                line_id=result.line_id,
                word_id=result.word_id,
                label=result.correct_label,
                validation_status=ValidationStatus.NONE.value,
                reasoning=None,
                label_proposed_by="COMPLETION_BATCH",
                label_consolidated_from=result.label,
                timestamp_added=datetime.now(timezone.utc),
            )
            new_labels_to_create.append(new_label)

        else:
            # handle other cases if any (not specified in instructions)
            pass

    # The rest of the function continues unchanged...
