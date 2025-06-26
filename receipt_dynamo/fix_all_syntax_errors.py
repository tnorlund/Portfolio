#!/usr/bin/env python3
"""Script to fix all remaining syntax errors in the receipt_dynamo package."""

import re

# Define files and their specific fixes
fixes = [
    # _receipt_line.py - line 251 indentation issue
    {
        "file": "receipt_dynamo/data/_receipt_line.py",
        "line_num": 251,
        "fix": lambda lines: fix_indentation(lines, 251),
    },
    # _image.py - line 151 smart quote
    {
        "file": "receipt_dynamo/data/_image.py",
        "line_num": 151,
        "fix": lambda lines: fix_smart_quotes(lines),
    },
    # _receipt_validation_summary.py - line 95 malformed method
    {
        "file": "receipt_dynamo/data/_receipt_validation_summary.py",
        "pattern": r'(\s+)raise DynamoDBError\("Could not add receipt validation summaries to DynamoDB: Table not found"\)\s*update_receipt_validation_summary',
        "replacement": r'\1raise DynamoDBError("Could not add receipt validation summaries to DynamoDB: Table not found")\n\n    def update_receipt_validation_summary',
    },
    # _receipt_chatgpt_validation.py - line 279 unterminated string
    {
        "file": "receipt_dynamo/data/_receipt_chatgpt_validation.py",
        "pattern": r'raise DynamoDBError\("Could not delete ReceiptChatGPTValidations from the database: \{e\}"\)\s*list_receipt_chat_gpt_validations',
        "replacement": r'raise DynamoDBError("Could not delete ReceiptChatGPTValidations from the database: {e}")\n\n    def list_receipt_chat_gpt_validations',
    },
    # _receipt_section.py - line 168 indentation issue
    {
        "file": "receipt_dynamo/data/_receipt_section.py",
        "pattern": r'(\s+)raise ValueError\("One or more ReceiptSections do not exist"\) from e\s*def delete_receipt_section',
        "replacement": r'\1raise ValueError("One or more ReceiptSections do not exist") from e\n\n    def delete_receipt_section',
    },
    # _receipt_letter.py - Missing closing triple quotes
    {
        "file": "receipt_dynamo/data/_receipt_letter.py",
        "line_num": 473,
        "fix": lambda lines: ensure_docstring_closed(lines, 473),
    },
    # _receipt_structure_analysis.py - line 98 unterminated string
    {
        "file": "receipt_dynamo/data/_receipt_structure_analysis.py",
        "pattern": r'raise DynamoDBError\("Could not add receipt structure analysis to DynamoDB"\)\s*add_receipt_structure_analyses',
        "replacement": r'raise DynamoDBError("Could not add receipt structure analysis to DynamoDB")\n\n    def add_receipt_structure_analyses',
    },
    # _receipt_word.py - line 236 unterminated string
    {
        "file": "receipt_dynamo/data/_receipt_word.py",
        "pattern": r'raise ValueError\("ReceiptWord with ID \{word_id\} not found"\)\s*get_receipt_words_by_indices',
        "replacement": r'raise ValueError("ReceiptWord with ID {word_id} not found")\n\n    def get_receipt_words_by_indices',
    },
]


def fix_indentation(lines, line_num):
    """Fix indentation issues."""
    if line_num - 1 < len(lines):
        # Ensure proper indentation
        lines[line_num - 1] = lines[line_num - 1].lstrip() + "\n"
        # Add proper indentation based on context
        if "def " in lines[line_num - 1]:
            lines[line_num - 1] = "    " + lines[line_num - 1]
    return lines


def fix_smart_quotes(lines):
    """Replace smart quotes with regular quotes."""
    for i, line in enumerate(lines):
        lines[i] = (
            line.replace(""", "'").replace(""", "'").replace('"', '"').replace('"', '"')
        )
    return lines


def ensure_docstring_closed(lines, start_line):
    """Ensure docstring is properly closed."""
    in_docstring = False
    quote_type = None

    for i in range(start_line - 1, len(lines)):
        line = lines[i]

        # Check if we're starting a docstring
        if '"""' in line and not in_docstring:
            in_docstring = True
            quote_type = '"""'
            # Check if it's closed on the same line
            if line.count('"""') >= 2:
                in_docstring = False
                continue
        elif "'''" in line and not in_docstring:
            in_docstring = True
            quote_type = "'''"
            # Check if it's closed on the same line
            if line.count("'''") >= 2:
                in_docstring = False
                continue

        # Check if we're closing a docstring
        if in_docstring and quote_type in line and i > start_line - 1:
            in_docstring = False
            continue

        # If we reach a def/class and still in docstring, close it before
        if in_docstring and i > start_line and ("def " in line or "class " in line):
            # Insert closing quotes before this line
            indent = len(line) - len(line.lstrip())
            lines.insert(i, " " * indent + quote_type + "\n")
            return lines

    return lines


def apply_pattern_fix(content, pattern, replacement):
    """Apply regex pattern fix."""
    return re.sub(pattern, replacement, content, flags=re.MULTILINE | re.DOTALL)


# Apply fixes
for fix_info in fixes:
    try:
        with open(fix_info["file"], "r", encoding="utf-8") as f:
            if "pattern" in fix_info:
                # Pattern-based fix
                content = f.read()
                content = apply_pattern_fix(
                    content, fix_info["pattern"], fix_info["replacement"]
                )
                with open(fix_info["file"], "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                # Line-based fix
                lines = f.readlines()
                lines = fix_info["fix"](lines)
                with open(fix_info["file"], "w", encoding="utf-8") as f:
                    f.writelines(lines)
        print(f"Fixed {fix_info['file']}")
    except Exception as e:
        print(f"Error fixing {fix_info['file']}: {e}")

print("\nAll syntax error fixes attempted.")
