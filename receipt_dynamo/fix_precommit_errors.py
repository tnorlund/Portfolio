#!/usr/bin/env python3
"""Fix remaining pre-commit errors in syntax."""

import re

# Fix the _image.py import issue
image_file = "receipt_dynamo/data/_image.py"
with open(image_file, "r") as f:
    content = f.read()

# Fix the malformed import on lines 31-42
content = re.sub(
    r"from receipt_dynamo\.entities import \(\s*DynamoDBServerError,\s*DynamoDBThroughputError,\s*ImageDetails,\s*OperationError,\s*ReceiptMetadata,\s*assert_valid_uuid,\s*from,\s*import,\s*item_to_ocr_job,\s*item_to_ocr_routing_decision,\s*item_to_receipt_metadata,\s*receipt_dynamo\.data\.shared_exceptions,\s*\)",
    """from receipt_dynamo.data.shared_exceptions import (
    DynamoDBServerError,
    DynamoDBThroughputError,
    OperationError,
)
from receipt_dynamo.entities import (
    ImageDetails,
    ReceiptMetadata,
    assert_valid_uuid,
    item_to_ocr_job,
    item_to_ocr_routing_decision,
    item_to_receipt_metadata,
)""",
    content,
    flags=re.MULTILINE | re.DOTALL,
)

with open(image_file, "w") as f:
    f.write(content)

print("Fixed _image.py imports")

# Fix method signature issues in other files
files_to_fix = [
    "receipt_dynamo/data/_receipt_line.py",
    "receipt_dynamo/data/_receipt_letter.py",
    "receipt_dynamo/data/_receipt_chatgpt_validation.py",
    "receipt_dynamo/data/_receipt_section.py",
    "receipt_dynamo/data/_receipt_structure_analysis.py",
    "receipt_dynamo/data/_receipt_validation_summary.py",
    "receipt_dynamo/data/_receipt_word.py",
]

for file_path in files_to_fix:
    try:
        with open(file_path, "r") as f:
            content = f.read()

        # Fix malformed method names and unterminated strings - more robust
        content = re.sub(
            r"(\w+)\s*\}\s*[\w_]+\s*\(", r'\1") from e\n\n    def \2(', content
        )
        content = re.sub(
            r'(\w+Error\([^)]+)\{([^}]+)\}([^"]*)"([^)]*)\)', r'\1{\2}\3")\4', content
        )
        content = re.sub(
            r'(["\'])([^"\']*)\{([^}]+)\}([^"\']*delete_\w+)', r"\1\2{\3}\4"
        )

        # Fix specific patterns found in the errors
        content = re.sub(r"lastEvaluatedKey", "last_evaluated_key", content)

        with open(file_path, "w") as f:
            f.write(content)
        print(f"Applied fixes to {file_path}")
    except Exception as e:
        print(f"Error fixing {file_path}: {e}")

print("Pre-commit error fixes completed")
