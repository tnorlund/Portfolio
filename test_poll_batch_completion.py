import json
from pathlib import Path
from receipt_label.utils import get_clients
from receipt_label.submit_completion_batch._format_prompt import _format_receipt_lines
import random
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel
from datetime import datetime, timezone
from receipt_label.constants import CORE_LABELS

dynamo_client, _, pinecone_index = get_clients()

# Counters for specific error types
error_counts = {
    "no_function_call": 0,
    "json_decode_error": 0,
    "not_a_dict": 0,
    "missing_expected_keys": {
        "is_valid": 0,
        "id": 0,
    },
    "label_not_found": 0,
    "already_valid": 0,
    "invalid_correct_label": 0,
    "invalid_label_not_core": 0,
    "other_logic_error": 0,
    "invalid_id": 0,
    "correct_label_none": 0,
}
labels_to_update = []
labels_to_add = []

file_path = Path(
    # "/Users/tnorlund/Downloads/batch_680f8b4e6a0c81908a22d1074417f9fd_output.jsonl"
    "/Users/tnorlund/Downloads/batch_680fd17bc4b0819083af2476f0a155e4_output.jsonl"
)

expect_keys = [
    "is_valid",
    # "proposed_label",
    "id",
    # "text",
    # "line_id",
    # "word_id",
]

with open(file_path, "r") as f:
    for line in f:
        parsed_line = json.loads(line)
        pass_type = parsed_line["custom_id"].split("#")[-1]
        image_id = parsed_line["custom_id"].split("#")[1]
        receipt_id = int(parsed_line["custom_id"].split("#")[3])
        receipt, lines, words, letters, tags, labels = dynamo_client.getReceiptDetails(
            image_id=image_id, receipt_id=receipt_id
        )
        print(image_id, receipt_id, pass_type)
        if pass_type == "FIRST_PASS":
            response = parsed_line["response"]
            body = response["body"]
            choices = body["choices"]
            if len(choices) != 1:
                raise ValueError(f"Expected 1 choice, got {len(choices)}")
            choice = choices[0]
            message = choice["message"]
            content = message["content"]
            if "function_call" not in message:
                error_counts["no_function_call"] += 1
                continue
            function_call = message["function_call"]
            arguments = function_call["arguments"]
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                error_counts["json_decode_error"] += 1
                continue
            results = arguments["results"]
            for result in results:
                # Skip non-dict results
                if not isinstance(result, dict):
                    error_counts["not_a_dict"] += 1
                    continue
                if not set(expect_keys).issubset(result.keys()):
                    missing = set(expect_keys) - set(result.keys())
                    # print(f"Missing expected keys {missing} in result {result}")
                    for key in missing:
                        error_counts["missing_expected_keys"][key] += 1
                    continue
                is_valid = result["is_valid"]
                if not is_valid:
                    # For invalid cases, correct_label must be provided
                    if "correct_label" not in result:
                        error_counts["correct_label_none"] += 1
                        continue
                    correct_label = result["correct_label"]
                else:
                    # For valid cases, ignore any missing correct_label
                    correct_label = None
                id_split = result["id"].split("#")
                if len(id_split) != 12:
                    error_counts["invalid_id"] += 1
                    continue
                image_id = id_split[1]
                receipt_id = int(id_split[3])
                line_id = int(id_split[5])
                word_id = int(id_split[7])
                label = id_split[9]
                label_from_dynamo = next(
                    (
                        l
                        for l in labels
                        if l.image_id == image_id
                        and l.receipt_id == receipt_id
                        and l.line_id == line_id
                        and l.word_id == word_id
                    ),
                    None,
                )
                if label_from_dynamo is None:
                    # Label not found in database
                    error_counts["label_not_found"] += 1
                    continue
                if label_from_dynamo.validation_status == ValidationStatus.VALID:
                    # raise ValueError(f"Label {label} is already valid")
                    error_counts["already_valid"] += 1
                    continue

                if not is_valid and label_from_dynamo.label == correct_label:
                    # The label is invalid but the correct label is the current label
                    # The label should be marked as validation_status=VALID
                    error_counts["other_logic_error"] += 1
                    continue

                if is_valid and label_from_dynamo.label not in CORE_LABELS:
                    # The label is valid but it is not a core label
                    # The label should be marked as validation_status=VALID
                    error_counts["invalid_label_not_core"] += 1
                    continue

                if not is_valid and correct_label not in CORE_LABELS:
                    # The label is invalid but the correct_label is not a core label
                    # The label should be marked as validation_status=VALID
                    error_counts["invalid_correct_label"] += 1
                    continue

                if is_valid:
                    label_from_dynamo.validation_status = ValidationStatus.VALID.value
                    labels_to_update.append(label_from_dynamo)

                if not is_valid:
                    label_from_dynamo.validation_status = ValidationStatus.INVALID.value
                    labels_to_update.append(label_from_dynamo)
                    labels_to_add.append(
                        ReceiptWordLabel(
                            image_id=image_id,
                            receipt_id=receipt_id,
                            line_id=line_id,
                            word_id=word_id,
                            label=correct_label,
                            validation_status=ValidationStatus.NONE.value,
                            reasoning=None,
                            label_proposed_by="COMPLETION_BATCH",
                            label_consolidated_from=label_from_dynamo.label,
                            timestamp_added=datetime.now(timezone.utc),
                        )
                    )


print("Error summary:")
for name, count in error_counts.items():
    print(f"  {name}: {count}")
print(f"labels_to_update: {len(labels_to_update)}")
print(f"labels_to_add: {len(labels_to_add)}")

labels_to_add = list(set(labels_to_add))
print(f"labels_to_add: {len(labels_to_add)}")

# for label in labels_to_add:
#     dynamo_client.createReceiptWordLabel(label)

# for label in labels_to_update:
#     dynamo_client.updateReceiptWordLabel(label)
