# Pylint Line-Too-Long Fix Prompts

This document contains per-file prompts for fixing line-too-long (C0301) pylint errors using AI code assistants.

## General Instructions

For each file, copy the entire prompt (including the file content) and paste it into your AI coding assistant (e.g., OpenAI Codex, Claude, etc.).

## Example Workflow

1. **Check current errors for a file:**
   ```bash
   cd receipt_dynamo
   pylint receipt_dynamo/data/base_operations.py --disable=all --enable=line-too-long
   ```

2. **Copy the file content:**
   ```bash
   cat receipt_dynamo/data/base_operations.py | pbcopy  # macOS
   # or
   cat receipt_dynamo/data/base_operations.py | xclip -selection clipboard  # Linux
   ```

3. **Use the prompt:**
   - Find the file's specific prompt in this document
   - Copy the prompt
   - Paste into your AI assistant
   - Paste the file content after the prompt
   - Copy the AI's fixed code

4. **Apply the fixes:**
   - Replace the file content with the AI's suggestions
   - Run pylint again to verify all issues are fixed
   
5. **Update this document:**
   - Mark the file as completed in the progress tracking table
   - Add the PR number when merged

## Base Prompt Template

```
Please fix all line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

Guidelines:
1. Break long lines at appropriate points (after commas, before operators, etc.)
2. Use implicit line continuation inside parentheses, brackets, or braces when possible
3. For long strings, use parentheses for implicit string concatenation
4. For long function signatures, break parameters onto separate lines
5. For long dictionary/list literals, use multi-line formatting
6. Maintain proper indentation (4 spaces)
7. Keep the code readable - don't just break lines arbitrarily
8. For imports, use parentheses for multiple imports from the same module
9. For long comments, break them into multiple lines with proper comment markers

Only return the fixed code, no explanations needed.

Here's the file content:
[FILE CONTENT GOES HERE]
```

## File-Specific Prompts

### 1. data/base_operations.py (113 line-too-long errors)

```
Please fix all 113 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file contains base operations for DynamoDB interactions with many long method signatures and error messages. 

Guidelines:
1. Break long method signatures after commas, putting each parameter on its own line
2. For long error messages, use implicit string concatenation with parentheses
3. For long type hints, consider using type aliases at the module level
4. Break long conditional expressions using parentheses
5. For docstrings with long lines, wrap them properly
6. Maintain proper indentation (4 spaces)

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/base_operations.py
```

### 2. data/_receipt_chatgpt_validation.py (54 line-too-long errors)

```
Please fix all 54 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles ChatGPT validation data with long method names and error messages.

Guidelines:
1. Break long f-strings using implicit concatenation with parentheses
2. For methods like 'list_receipt_chat_gpt_validations_for_receipt', break after underscores in names
3. Long dictionary key names in DynamoDB operations should be on separate lines
4. Break long docstring lines, especially parameter descriptions
5. For query expressions, break at logical operators

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_receipt_chatgpt_validation.py
```

### 3. data/_gpt.py (42 line-too-long errors)

```
Please fix all 42 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file contains GPT API integration code with long JSON structures and API calls.

Guidelines:
1. For long dictionary literals (especially JSON), use multi-line formatting
2. Break long URL strings at appropriate points (after slashes)
3. For long list comprehensions, use multi-line format
4. Break long conditional expressions with parentheses
5. API response parsing with long key chains should be broken into multiple lines

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_gpt.py
```

### 4. entities/receipt_line_item_analysis.py (32 line-too-long errors)

```
Please fix all 32 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file contains entity definitions with long attribute names and type annotations.

Guidelines:
1. For dataclass field definitions with long type hints, break after the colon
2. Long default_factory lambda expressions should be on separate lines
3. Break long method return type annotations
4. For validation error messages, use implicit string concatenation
5. Complex type unions should be broken across lines

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/receipt_line_item_analysis.py
```

### 5. data/_instance.py (32 line-too-long errors)

```
Please fix all 32 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file manages instance data with long DynamoDB operations.

Guidelines:
1. Break long boto3 client method calls after parameters
2. For error handling with long exception messages, use parentheses
3. Long conditional expressions in queries should use line continuation
4. Break imports from typing module if too long
5. DynamoDB expression attribute names/values should be multi-line

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_instance.py
```

## Usage Instructions

1. Open the file with line-too-long errors
2. Copy the entire file content
3. Find the corresponding prompt in this document
4. Paste the prompt followed by the file content into your AI assistant
5. Review the AI's suggestions and apply them
6. Run `pylint --disable=all --enable=line-too-long <filename>` to verify fixes
7. Update this document after each PR to track progress

## Progress Tracking

**Total errors reduced from 1139 to 866 (273 fixed!)**

| File | Line-too-long errors | Status | PR # |
|------|---------------------|---------|------|
| data/base_operations.py | 113 → 0 | ✅ Fixed | #227 |
| data/_receipt_chatgpt_validation.py | 54 → 0 | ✅ Fixed | #227 |
| data/_gpt.py | 42 → 0 | ✅ Fixed | #227 |
| entities/receipt_line_item_analysis.py | 32 → 0 | ✅ Fixed | #227 |
| data/_instance.py | 32 → 0 | ✅ Fixed | #227 |
| entities/receipt_word.py | 30 | 🔄 Next | - |
| data/_receipt.py | 30 | 🔄 Next | - |
| data/_receipt_word_label.py | 30 | 🔄 Next | - |
| data/_queue.py | 30 | 🔄 Next | - |
| entities/receipt_metadata.py | 28 | ❌ Not started | - |
| entities/receipt_letter.py | 26 | ❌ Not started | - |
| data/_batch_summary.py | 26 | ❌ Not started | - |
| data/_receipt_structure_analysis.py | 25 | ❌ Not started | - |
| data/_embedding_batch_result.py | 21 | ❌ Not started | - |
| entities/receipt_line.py | 20 | ❌ Not started | - |
| data/_receipt_word.py | 20 | ❌ Not started | - |
| data/_receipt_metadata.py | 18 | ❌ Not started | - |
| entities/word.py | 17 | ❌ Not started | - |
| entities/receipt.py | 17 | ❌ Not started | - |
| entities/receipt_word_label.py | 17 | ❌ Not started | - |
| data/_job.py | 17 | ❌ Not started | - |
| entities/job_dependency.py | 16 | ❌ Not started | - |
| data/_receipt_line_item_analysis.py | 16 | ❌ Not started | - |
| data/_job_metric.py | 16 | ❌ Not started | - |
| data/_job_checkpoint.py | 16 | ❌ Not started | - |

### 6. entities/receipt_word.py (30 line-too-long errors)

```
Please fix all 30 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file contains entity definitions for receipt words with long attribute names and relationships.

Guidelines:
1. For dataclass field definitions with long descriptions, break after the field type
2. Long property methods should have return types on separate lines
3. Break long relationship definitions across multiple lines
4. For validation logic with long conditions, use parentheses for grouping
5. Multi-field string representations should use proper formatting

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/receipt_word.py
```

### 7. data/_receipt.py (30 line-too-long errors)

```
Please fix all 30 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles receipt data operations with complex queries and batch operations.

Guidelines:
1. Break long DynamoDB query conditions at logical operators
2. For batch operations with multiple items, use multi-line formatting
3. Long error messages should use implicit string concatenation
4. Method signatures with many parameters should break after commas
5. Complex return type annotations should be on separate lines

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_receipt.py
```

### 8. data/_receipt_word_label.py (30 line-too-long errors)

```
Please fix all 30 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file manages receipt word label data with complex DynamoDB operations.

Guidelines:
1. Break long PK/SK construction strings at component boundaries
2. For methods with long names, consider breaking after underscores
3. Complex conditional expressions should use parentheses
4. List comprehensions that are too long should be converted to loops
5. Long docstring parameter descriptions should wrap properly

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_receipt_word_label.py
```

### 9. data/_queue.py (30 line-too-long errors)

```
Please fix all 30 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles queue operations with timestamp-based sorting and status updates.

Guidelines:
1. Break long timestamp formatting strings appropriately
2. For update expressions with multiple attributes, use multi-line format
3. Status validation lists should be on separate lines if too long
4. Complex filter expressions should be broken at logical boundaries
5. Method names with multiple concepts should break at underscores

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_queue.py
```

## Additional Prompts Template

For files not listed above, use this template:

```
Please fix all [NUMBER] line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

Guidelines:
1. Break long lines at appropriate points (after commas, before operators, etc.)
2. Use implicit line continuation inside parentheses, brackets, or braces when possible
3. For long strings, use parentheses for implicit string concatenation
4. For long function signatures, break parameters onto separate lines
5. For long dictionary/list literals, use multi-line formatting
6. Maintain proper indentation (4 spaces)
7. Keep the code readable - don't just break lines arbitrarily

Only return the fixed code, no explanations needed.

File: [FILEPATH]
[FILE CONTENT]
```

## Common Line-Too-Long Patterns

### 1. Long Import Statements
```python
# Before:
from receipt_dynamo.data.base_operations import BatchOperationsMixin, DynamoDBBaseOperations, SingleEntityCRUDMixin

# After:
from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
)
```

### 2. Long Method Signatures
```python
# Before:
def list_receipt_chat_gpt_validations_for_receipt(self, receipt_id: int, image_id: str) -> list[ReceiptChatGPTValidation]:

# After:
def list_receipt_chat_gpt_validations_for_receipt(
    self, receipt_id: int, image_id: str
) -> list[ReceiptChatGPTValidation]:
```

### 3. Long String Literals
```python
# Before:
raise ValueError(f"ReceiptChatGPTValidation for receipt {validation.receipt_id} and timestamp {validation.timestamp} already exists")

# After:
raise ValueError(
    f"ReceiptChatGPTValidation for receipt {validation.receipt_id} "
    f"and timestamp {validation.timestamp} already exists"
)
```

### 4. Long Dictionary/List Literals
```python
# Before:
response = self._client.query(TableName=self.table_name, KeyConditionExpression="PK = :pk", ExpressionAttributeValues={":pk": {"S": f"IMAGE#{image_id}"}})

# After:
response = self._client.query(
    TableName=self.table_name,
    KeyConditionExpression="PK = :pk",
    ExpressionAttributeValues={
        ":pk": {"S": f"IMAGE#{image_id}"}
    }
)
```

## Automated Checks

### Check All Files at Once
```bash
cd receipt_dynamo
pylint receipt_dynamo --disable=all --enable=line-too-long | grep "C0301" | awk -F: '{print $1}' | sort | uniq -c | sort -nr
```

### Check Progress
```bash
# Before starting
BEFORE=$(pylint receipt_dynamo --disable=all --enable=line-too-long | grep -c "C0301")

# After fixing
AFTER=$(pylint receipt_dynamo --disable=all --enable=line-too-long | grep -c "C0301")

echo "Fixed $((BEFORE - AFTER)) line-too-long errors"
```

### Verify No New Issues
```bash
# Run full pylint check on the modified file
pylint receipt_dynamo/data/base_operations.py
```

## Notes

- The pylint line length limit is 79 characters (not 80)
- Some files may have different line length settings - check `.pylintrc` or `pyproject.toml`
- After fixing, always run the full pylint check to ensure no new issues were introduced
- Consider using `black` formatter with `--line-length 79` for consistency
- When fixing, prioritize readability over minimal line count
- Group related parameters/arguments together when breaking lines