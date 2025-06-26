#!/usr/bin/env python3
"""Fix remaining simple syntax errors."""

import re

# Fix specific issues in each file
files_to_fix = [
    "receipt_dynamo/data/_receipt_chatgpt_validation.py",
    "receipt_dynamo/data/_receipt_validation_category.py",
]

for file_path in files_to_fix:
    try:
        with open(file_path, "r") as f:
            content = f.read()

        # Fix specific broken method definitions and strings
        content = re.sub(
            r'wadd_receipt_chat_gpt_validation"""',
            'with a specific status."""',
            content,
        )
        content = re.sub(
            r"delete_receipt_chat_gpt_validationsptChatGPTValidations\(",
            "def delete_receipt_chat_gpt_validations(",
            content,
        )
        content = re.sub(
            r"list_receipt_chat_gpt_validationsTValidations\(",
            "def list_receipt_chat_gpt_validations(",
            content,
        )
        content = re.sub(
            r"get_receipt_chat_gpt_validationhatGPTValidation\(",
            "def get_receipt_chat_gpt_validation(",
            content,
        )
        content = re.sub(
            r"list_receipt_chat_gpt_validations_for_receipteceipt\(",
            "def list_receipt_chat_gpt_validations_for_receipt(",
            content,
        )
        content = re.sub(r"lastEvaluatedKey", "last_evaluated_key", content)
        content = re.sub(
            r"\"All categories must be instances of the ReceiptValidationCategory class\.f\"",
            '"All categories must be instances of the ReceiptValidationCategory class."',
            content,
        )
        content = re.sub(
            r"\"field_name must not be empty\.f\"",
            '"field_name must not be empty."',
            content,
        )
        content = re.sub(r"\"SK\": \{", '"SK": {', content)
        content = re.sub(r"\"SKf\":", '"SK":', content)
        content = re.sub(
            r"\"ExpressionAttributeValuesf\":", '"ExpressionAttributeValues":', content
        )
        content = re.sub(
            r"\"lastEvaluatedKey must be a dictionary or None\.f\"",
            '"last_evaluated_key must be a dictionary or None."',
            content,
        )
        content = re.sub(r"\":skPrefixf\":", '":skPrefix":', content)
        content = re.sub(
            r"\"status must not be empty\.\"\) from e",
            '"status must not be empty."',
            content,
        )

        with open(file_path, "w") as f:
            f.write(content)
        print(f"Fixed {file_path}")
    except Exception as e:
        print(f"Error fixing {file_path}: {e}")

print("Simple error fixes completed")
