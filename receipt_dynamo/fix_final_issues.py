#!/usr/bin/env python3
"""Fix final remaining syntax issues."""

import re

# Fix chatgpt validation file
chatgpt_file = "receipt_dynamo/data/_receipt_chatgpt_validation.py"
with open(chatgpt_file, "r") as f:
    content = f.read()

# Fix broken method definitions and error strings
content = re.sub(
    r":\s*\{def delete_receipt_chat_gpt_validations\(",
    '") from e\n\n    def delete_receipt_chat_gpt_validations(',
    content,
)
content = re.sub(
    r":\s*\{e\}\"\)def get_receipt_chat_gpt_validation\(",
    '": {e}") from e\n\n    def get_receipt_chat_gpt_validation(',
    content,
)
content = re.sub(
    r":\s*\{e\}\"\)\s*frodef list_receipt_chat_gpt_validations\(",
    '": {e}") from e\n\n    def list_receipt_chat_gpt_validations(',
    content,
)
content = re.sub(
    r"\"status must not be empty\.\"$",
    '"status must not be empty.")',
    content,
    flags=re.MULTILINE,
)

with open(chatgpt_file, "w") as f:
    f.write(content)

# Fix validation category file
validation_file = "receipt_dynamo/data/_receipt_validation_category.py"
with open(validation_file, "r") as f:
    content = f.read()

# Fix remaining f-string issues
content = re.sub(
    r"\"last_evaluated_key must be a dictionary or None\.f\"",
    '"last_evaluated_key must be a dictionary or None."',
    content,
)

with open(validation_file, "w") as f:
    f.write(content)

print("Fixed remaining syntax issues")
