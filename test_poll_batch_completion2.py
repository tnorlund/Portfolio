import json
from pathlib import Path
from receipt_label.utils import get_clients
from receipt_dynamo.entities import ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus
from dataclasses import dataclass

dynamo_client, _, pinecone_index = get_clients()

file_name = Path(
    "/Users/tnorlund/Downloads/batch_680fd17bc4b0819083af2476f0a155e4_output.jsonl"
)

error_counts = {
    "label_not_found": 0,
    "label_already_valid": 0,
    "label_already_correct": 0,
    "no_is_valid": 0,
}


@dataclass
class LabelResult:
    label_from_dynamo: ReceiptWordLabel
    result: dict
    other_labels: list[ReceiptWordLabel]


def build_vector_id(label: ReceiptWordLabel) -> str:
    return (
        f"IMAGE#{label.image_id}#"
        f"RECEIPT#{label.receipt_id:05d}#"
        f"LINE#{label.line_id:05d}#"
        f"WORD#{label.word_id:05d}"
    )


def _get_results(data: dict) -> list[dict]:
    response = data["response"]
    body = response["body"]
    choices = body["choices"]
    if len(choices) != 1:
        raise ValueError(f"Expected 1 choice, got {len(choices)}")
    message = choices[0]["message"]
    if "function_call" in message:
        arguments = message["function_call"].get("arguments")
        if not arguments:
            raise ValueError(f"Missing arguments in function_call: {message}")
        try:
            return json.loads(arguments)["results"]
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f"Could not parse function_call arguments: {e}")
    elif message["content"] is not None:
        return json.loads(message["content"])["results"]
    else:
        raise ValueError(f"No usable result found in message: {message}")


def _get_label_info(result: dict) -> tuple[str, int, int, int, str]:
    id_split = result["id"].split("#")
    if len(id_split) != 12:
        raise ValueError(f"Invalid ID: {result['id']}")
    image_id = id_split[1]
    receipt_id = int(id_split[3])
    line_id = int(id_split[5])
    word_id = int(id_split[7])
    label = id_split[9]
    return image_id, receipt_id, line_id, word_id, label


def _get_other_labels_from_dynamo(
    this_label: ReceiptWordLabel, all_labels_from_receipt: list[ReceiptWordLabel]
) -> list[ReceiptWordLabel]:
    all_labels_with_same_id = [
        label
        for label in all_labels_from_receipt
        if label.image_id == this_label.image_id
        and label.receipt_id == this_label.receipt_id
        and label.line_id == this_label.line_id
        and label.word_id == this_label.word_id
        and label.label != this_label.label
    ]
    # remove the label from the list
    return all_labels_with_same_id


pending_labels_to_update = []
valid_labels: list[LabelResult] = []
invalid_labels: list[LabelResult] = []

with open(file_name, "r") as f:
    for line in f:
        data = json.loads(line)
        custom_id = data["custom_id"]
        image_id = custom_id.split("#")[1]
        receipt_id = int(custom_id.split("#")[3])
        # print(f"{image_id} {receipt_id}")
        (
            _,
            all_lines_from_receipt,
            all_words_from_receipt,
            all_letters_from_receipt,
            all_tags_from_receipt,
            all_labels_from_receipt,
        ) = dynamo_client.getReceiptDetails(image_id=image_id, receipt_id=receipt_id)
        pending_labels = [
            label
            for label in all_labels_from_receipt
            if label.validation_status == ValidationStatus.PENDING.value
        ]
        results = _get_results(data)
        if results is None:
            error_counts["no_results"] += 1
            continue
        for result in results:
            (
                result_image_id,
                result_receipt_id,
                result_line_id,
                result_word_id,
                result_label,
            ) = _get_label_info(result)
            label_from_dynamo = next(
                (
                    label
                    for label in all_labels_from_receipt
                    if label.image_id == result_image_id
                    and label.receipt_id == result_receipt_id
                    and label.line_id == result_line_id
                    and label.word_id == result_word_id
                    and label.label == result_label
                ),
                None,
            )
            if label_from_dynamo is None:
                # Label not found in DynamoDB
                error_counts["label_not_found"] += 1
                print(
                    f"🔴 label_not_found: Could not match result to label in Dynamo: {result['id']}"
                )
                continue
            else:
                # print(
                #     f"✅ Matched result to pending label: {label_from_dynamo.image_id} {label_from_dynamo.receipt_id} {label_from_dynamo.line_id} {label_from_dynamo.word_id} {label_from_dynamo.label}"
                # )
                # remove the label from the pending_labels list
                pending_labels = [
                    label
                    for label in pending_labels
                    if not (
                        label.image_id == label_from_dynamo.image_id
                        and label.receipt_id == label_from_dynamo.receipt_id
                        and label.line_id == label_from_dynamo.line_id
                        and label.word_id == label_from_dynamo.word_id
                        and label.label == label_from_dynamo.label
                    )
                ]
            if label_from_dynamo.validation_status == ValidationStatus.VALID:
                # Label is already valid
                error_counts["label_already_valid"] += 1
                continue
            other_labels = _get_other_labels_from_dynamo(
                label_from_dynamo, all_labels_from_receipt
            )
            if result["is_valid"]:
                # Update the label in DynamoDB
                valid_labels.append(
                    LabelResult(
                        label_from_dynamo=label_from_dynamo,
                        result=result,
                        other_labels=other_labels,
                    )
                )
            else:
                # Update the label in DynamoDB
                invalid_labels.append(
                    LabelResult(
                        label_from_dynamo=label_from_dynamo,
                        result=result,
                        other_labels=other_labels,
                    )
                )
        # Reset leftover pending labels
        if pending_labels:
            for label in pending_labels:
                label.validation_status = ValidationStatus.NONE.value
                pending_labels_to_update.append(label)
                print(
                    f"🟡 Unmatched pending label (reset to NONE): {label.image_id} {label.receipt_id} {label.line_id} {label.word_id} {label.label}"
                )
            # dynamo_client.updateReceiptWordLabels(pending_labels)


print(error_counts)
print(f"valid_labels: {len(valid_labels)}")
print(f"invalid_labels: {len(invalid_labels)}")
print(f"pending_labels_to_update: {len(pending_labels_to_update)}")


valid_by_vector = {}
invalid_by_vector = {}
labels_to_update: list[ReceiptWordLabel] = []
labels_to_add: list[ReceiptWordLabel] = []

for pending_label in pending_labels_to_update:
    pending_label.validation_status = ValidationStatus.NONE.value
    labels_to_update.append(pending_label)


for label in valid_labels:
    label.label_from_dynamo.validation_status = ValidationStatus.VALID.value
    labels_to_update.append(label.label_from_dynamo)
    vector_id = build_vector_id(label.label_from_dynamo)
    valid_by_vector.setdefault(vector_id, []).append(label.label_from_dynamo.label)

for id_batch in _chunk(valid_by_vector.keys(), 100):
    fetched = pinecone_index.fetch(ids=id_batch, namespace="words").vectors
    for vid in id_batch:
        meta = (fetched[vid].metadata if vid in fetched else {}) or {}
        existing = meta.get("valid_labels", [])
        if isinstance(existing, str):
            existing = [existing]
        new = valid_by_vector[vid]
        meta["valid_labels"] = list(set(existing + new))
        pinecone_index.update(id=vid, set_metadata=meta, namespace="words")


seen_new_label_keys = set()

for label in invalid_labels:
    if any(
        l.validation_status == ValidationStatus.INVALID.value
        for l in label.other_labels
    ):
        label.label_from_dynamo.validation_status = ValidationStatus.NEEDS_REVIEW.value
    else:
        label.label_from_dynamo.validation_status = ValidationStatus.INVALID.value
    label.label_from_dynamo.label_proposed_by = "COMPLETION_BATCH"
    labels_to_update.append(label.label_from_dynamo)

    if "correct_label" in label.result and label.result["correct_label"]:
        key = (
            label.label_from_dynamo.image_id,
            label.label_from_dynamo.receipt_id,
            label.label_from_dynamo.line_id,
            label.label_from_dynamo.word_id,
            label.result["correct_label"],
        )
        if key not in seen_new_label_keys:
            seen_new_label_keys.add(key)
            new_label = ReceiptWordLabel(
                image_id=label.label_from_dynamo.image_id,
                receipt_id=label.label_from_dynamo.receipt_id,
                line_id=label.label_from_dynamo.line_id,
                word_id=label.label_from_dynamo.word_id,
                label=label.result["correct_label"],
                validation_status=ValidationStatus.NONE.value,
                reasoning=None,
                label_proposed_by="COMPLETION_BATCH",
                label_consolidated_from=label.label_from_dynamo.label,
            )
            labels_to_add.append(new_label)
