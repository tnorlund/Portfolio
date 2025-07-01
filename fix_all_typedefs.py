#!/usr/bin/env python3
"""Fix all TypeDef imports in data files."""

import subprocess
import sys

# Files and their runtime TypeDefs
files_to_fix = {
    "_ai_usage_metric.py": ["WriteRequestTypeDef", "PutRequestTypeDef"],
    "_batch_summary.py": ["WriteRequestTypeDef", "PutRequestTypeDef"],
    "_completion_batch_result.py": ["WriteRequestTypeDef", "PutRequestTypeDef"],
    "_embedding_batch_result.py": ["WriteRequestTypeDef", "PutRequestTypeDef"],
    "_image.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_instance.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteTypeDef",
    ],
    "_job.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteTypeDef",
    ],
    "_job_dependency.py": ["WriteRequestTypeDef", "PutRequestTypeDef"],
    "_job_log.py": ["WriteRequestTypeDef", "PutRequestTypeDef"],
    "_letter.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "DeleteRequestTypeDef",
    ],  # Already fixed
    "_line.py": ["WriteRequestTypeDef", "PutRequestTypeDef", "DeleteRequestTypeDef"],
    "_ocr_job.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteTypeDef",
    ],
    "_ocr_routing_decision.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteTypeDef",
    ],
    "_places_cache.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_queue.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteTypeDef",
    ],
    "_receipt.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteTypeDef",
    ],
    "_receipt_label_analysis.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_letter.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_line.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_line_item_analysis.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_metadata.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteTypeDef",
    ],
    "_receipt_section.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
    ],
    "_receipt_structure_analysis.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_validation_category.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_validation_result.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_validation_summary.py": ["TransactWriteItemTypeDef", "PutTypeDef"],
    "_receipt_word.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_word_label.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "TransactWriteItemTypeDef",
        "PutTypeDef",
        "DeleteRequestTypeDef",
    ],
    "_receipt_word_tag.py": [
        "WriteRequestTypeDef",
        "PutRequestTypeDef",
        "DeleteRequestTypeDef",
    ],
}

# Already fixed files
already_fixed = {
    "_letter.py",
    "_receipt_field.py",
    "_receipt_chatgpt_validation.py",
    "_word_tag.py",
    "_word.py",
    "_label_count_cache.py",
}


def main():
    """Run sed commands to fix imports."""
    base_path = (
        "/Users/tnorlund/GitHub/example-issue-130/receipt_dynamo/receipt_dynamo/data"
    )

    for filename, typedefs in files_to_fix.items():
        if filename in already_fixed:
            print(f"Skipping {filename} - already fixed")
            continue

        filepath = f"{base_path}/{filename}"

        # Create import statement
        runtime_imports = "\n# These are used at runtime, not just for type checking\nfrom receipt_dynamo.data._base import (\n"
        for typedef in sorted(typedefs):
            runtime_imports += f"    {typedef},\n"
        runtime_imports += ")"

        # Use a marker to insert after TYPE_CHECKING block
        print(f"Fixing {filename}...")

        # First, add a marker after the TYPE_CHECKING block's closing parenthesis
        marker_cmd = [
            "sed",
            "-i",
            "",
            r"/^if TYPE_CHECKING:/,/^[[:space:]]*)/s/^[[:space:]]*)$/    )\n###RUNTIME_IMPORTS_HERE###/",
            filepath,
        ]

        result = subprocess.run(marker_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Error adding marker: {result.stderr}")
            continue

        # Then replace the marker with our runtime imports
        with open(filepath, "r") as f:
            content = f.read()

        content = content.replace("###RUNTIME_IMPORTS_HERE###", runtime_imports)

        with open(filepath, "w") as f:
            f.write(content)

        print(f"  Added runtime imports for: {', '.join(sorted(typedefs))}")

    print("\nDone!")


if __name__ == "__main__":
    main()
