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

### 1. data/base_operations.py (113 line-too-long errors) - ✅ FIXED

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

### 2. data/_receipt_chatgpt_validation.py (54 line-too-long errors) - ✅ FIXED

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

### 3. data/_gpt.py (42 line-too-long errors) - ✅ FIXED

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

### 4. entities/receipt_line_item_analysis.py (32 line-too-long errors) - ✅ FIXED

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

### 5. data/_instance.py (32 line-too-long errors) - ✅ FIXED

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

**Total errors reduced from 1139 to 462 (677 fixed! 59% reduction)**

| File | Line-too-long errors | Status | PR # |
|------|---------------------|---------|------|
| data/base_operations.py | 113 → 0 | ✅ Fixed | #227 |
| data/_receipt_chatgpt_validation.py | 54 → 0 | ✅ Fixed | #227 |
| data/_gpt.py | 42 → 0 | ✅ Fixed | #227 |
| entities/receipt_line_item_analysis.py | 32 → 0 | ✅ Fixed | #227 |
| data/_instance.py | 32 → 0 | ✅ Fixed | #227 |
| entities/receipt_word.py | 30 → 0 | ✅ Fixed | #227 |
| data/_receipt.py | 30 → 0 | ✅ Fixed | #227 |
| data/_receipt_word_label.py | 30 → 0 | ✅ Fixed | #227 |
| data/_queue.py | 30 | 🚫 Still has errors | - |
| entities/receipt_metadata.py | 28 → 0 | ✅ Fixed | #227 |
| entities/receipt_letter.py | 26 → 0 | ✅ Fixed | #227 |
| data/_batch_summary.py | 26 → 0 | ✅ Fixed | #227 |
| data/_receipt_structure_analysis.py | 25 → 0 | ✅ Fixed | #227 |
| data/_embedding_batch_result.py | 21 → 0 | ✅ Fixed | #227 |
| entities/receipt_line.py | 20 → 0 | ✅ Fixed | #227 |
| data/_receipt_word.py | 20 → 0 | ✅ Fixed | #227 |
| data/_receipt_metadata.py | 18 → 0 | ✅ Fixed | #227 |
| entities/word.py | 17 → 0 | ✅ Fixed | #227 |
| entities/receipt.py | 17 → 0 | ✅ Fixed | #227 |
| entities/receipt_word_label.py | 17 → 0 | ✅ Fixed | #227 |
| data/_job.py | 17 → 0 | ✅ Fixed | #227 |
| entities/job_dependency.py | 16 → 0 | ✅ Fixed | #227 |
| data/_receipt_line_item_analysis.py | 16 → 0 | ✅ Fixed | #227 |
| data/_job_metric.py | 16 → 0 | ✅ Fixed | #227 |
| data/_job_checkpoint.py | 16 → 0 | ✅ Fixed | #227 |

## Files Still Needing Fixes

| File | Line-too-long errors | Status | Notes |
|------|---------------------|---------|-------|
| data/_queue.py | 30 | 🚫 Still has errors | File #9 - prompt available above |
| entities/receipt_label_analysis.py | 15 | ❌ Not started | New file |
| entities/job_checkpoint.py | 14 | ❌ Not started | New file |
| data/_receipt_label_analysis.py | 14 | ❌ Not started | New file |
| data/_job_resource.py | 14 | ❌ Not started | New file |
| entities/letter.py | 13 | ❌ Not started | New file |
| entities/job_resource.py | 13 | ❌ Not started | New file |
| entities/job_metric.py | 13 | ❌ Not started | New file |
| data/_places_cache.py | 13 | ❌ Not started | New file |
| data/_ocr_job.py | 12 | ❌ Not started | New file |
| data/_job_dependency.py | 12 | ❌ Not started | New file |
| entities/rwl_queue.py | 11 | ❌ Not started | New file |
| entities/receipt_structure_analysis.py | 11 | ❌ Not started | New file |
| entities/places_cache.py | 11 | ❌ Not started | New file |
| entities/instance.py | 11 | ❌ Not started | New file |
| data/_receipt_validation_result.py | 11 | ❌ Not started | New file |
| data/_ocr_routing_decision.py | 11 | ❌ Not started | New file |
| entities/receipt_field.py | 10 | ❌ Not started | New file |
| entities/job.py | 10 | ❌ Not started | New file |
| data/import_image.py | 10 | ❌ Not started | New file |
| data/_receipt_field.py | 10 | ❌ Not started | New file |
| data/_job_status.py | 10 | ❌ Not started | New file |
| **Plus 84 more files** | 242 total | ❌ Not started | Files with <10 errors each |

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

### 10. entities/receipt_metadata.py (28 line-too-long errors)

```
Please fix all 28 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file contains receipt metadata entity definitions with complex nested structures.

Guidelines:
1. Break long dataclass field definitions at appropriate points
2. For nested dictionary type hints, use multi-line formatting
3. Long validation messages should use implicit string concatenation
4. Method signatures with multiple type parameters should break cleanly
5. Complex property return types should be on separate lines

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/receipt_metadata.py
```

### 11. entities/receipt_letter.py (26 line-too-long errors)

```
Please fix all 26 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file defines receipt letter entities with coordinate and text data.

Guidelines:
1. Break long coordinate calculations across multiple lines
2. For bounding box representations, use multi-line formatting
3. Long string representations should use proper concatenation
4. Method signatures with coordinate parameters should break after commas
5. Complex type annotations for geometric data should be split

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/receipt_letter.py
```

### 12. data/_batch_summary.py (26 line-too-long errors)

```
Please fix all 26 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file manages batch summary operations with aggregation and statistics.

Guidelines:
1. Break long aggregation expressions at operator boundaries
2. For statistical calculations, use intermediate variables if needed
3. Long DynamoDB update expressions should be multi-line
4. Method names describing batch operations should break at underscores
5. Complex return type annotations should use line breaks

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_batch_summary.py
```

### 13. data/_receipt_structure_analysis.py (25 line-too-long errors)

```
Please fix all 25 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles receipt structure analysis with complex hierarchical data.

Guidelines:
1. Break long analysis method names at logical boundaries
2. For nested structure representations, use proper indentation
3. Long validation logic should use parentheses for grouping
4. Complex query conditions should break at AND/OR operators
5. Type hints for hierarchical data should be multi-line

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_receipt_structure_analysis.py
```

### 14. data/_embedding_batch_result.py (21 line-too-long errors)

```
Please fix all 21 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file manages embedding batch results with vector data operations.

Guidelines:
1. Break long vector operation expressions appropriately
2. For embedding dimension specifications, use clear line breaks
3. Long batch processing method names should break at underscores
4. Complex numpy/tensor operations should use intermediate variables
5. Error messages about dimension mismatches should be multi-line

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_embedding_batch_result.py
```

### 15. entities/receipt_line.py (20 line-too-long errors)

```
Please fix all 20 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file defines receipt line entities with text and position data.

Guidelines:
1. Break long line position calculations across multiple lines
2. For text concatenation operations, use proper line breaks
3. Bounding box calculations should be clearly formatted
4. Method signatures with position parameters should break cleanly
5. String representations of lines should use proper formatting

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/receipt_line.py
```

### 16. data/_receipt_word.py (20 line-too-long errors)

```
Please fix all 20 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file manages receipt word data with position and label information.

Guidelines:
1. Break long word position queries at logical points
2. For label association methods, use clear parameter breaks
3. Complex filter conditions should use parentheses
4. Long error messages about word positions should be multi-line
5. Batch operations on words should have clear formatting

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_receipt_word.py
```

### 17. data/_receipt_metadata.py (18 line-too-long errors)

```
Please fix all 18 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles receipt metadata operations with nested attribute updates.

Guidelines:
1. Break long metadata attribute paths at dot boundaries
2. For nested update expressions, use proper indentation
3. Complex metadata validation should use multi-line conditions
4. Long attribute names in queries should break appropriately
5. Error messages about metadata structure should be clear

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_receipt_metadata.py
```

### 18. entities/word.py (17 line-too-long errors)

```
Please fix all 17 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file defines word entities with character-level information.

Guidelines:
1. Break long character position lists appropriately
2. For word boundary calculations, use clear formatting
3. Type annotations for character data should be multi-line
4. Validation messages about word structure should break cleanly
5. String representations should use proper concatenation

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/word.py
```

### 19. entities/receipt.py (17 line-too-long errors)

```
Please fix all 17 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file defines the main receipt entity with relationships to other entities.

Guidelines:
1. Break long relationship definitions across lines
2. For aggregate property calculations, use clear formatting
3. Complex validation logic should use parentheses
4. Method signatures with multiple entity parameters should break
5. String representations of receipts should be well-formatted

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/receipt.py
```

### 20. entities/receipt_word_label.py (17 line-too-long errors)

```
Please fix all 17 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file defines receipt word label entities with classification data.

Guidelines:
1. Break long label category lists across lines
2. For confidence score calculations, use clear formatting
3. Validation messages about label constraints should be multi-line
4. Complex type hints for label data should break appropriately
5. String representations should show label hierarchy clearly

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/receipt_word_label.py
```

### 21. data/_job.py (17 line-too-long errors)

```
Please fix all 17 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file manages job data with status tracking and dependencies.

Guidelines:
1. Break long job status transition logic at appropriate points
2. For dependency chain queries, use clear formatting
3. Complex job filtering conditions should use parentheses
4. Long error messages about job states should be multi-line
5. Timestamp formatting in queries should break cleanly

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_job.py
```

### 22. entities/job_dependency.py (16 line-too-long errors)

```
Please fix all 16 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file defines job dependency relationships and validation.

Guidelines:
1. Break long dependency chain representations across lines
2. For circular dependency checks, use clear formatting
3. Complex validation messages should be multi-line
4. Type annotations for dependency graphs should break appropriately
5. String representations of dependencies should be hierarchical

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/job_dependency.py
```

### 23. data/_receipt_line_item_analysis.py (16 line-too-long errors)

```
Please fix all 16 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles receipt line item analysis with price and quantity extraction.

Guidelines:
1. Break long analysis method names at logical boundaries
2. For price calculation expressions, use clear formatting
3. Complex regex patterns should be split with comments
4. Long validation messages about item formats should be multi-line
5. Aggregate calculations should use intermediate variables

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_receipt_line_item_analysis.py
```

### 24. data/_job_metric.py (16 line-too-long errors)

```
Please fix all 16 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file manages job metrics with performance and resource tracking.

Guidelines:
1. Break long metric aggregation queries at operator boundaries
2. For statistical calculations, use clear variable names
3. Complex metric filtering should use parentheses
4. Long metric names in updates should break appropriately
5. Time series queries should have clear date formatting

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_job_metric.py
```

### 25. data/_job_checkpoint.py (16 line-too-long errors)

```
Please fix all 16 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles job checkpoint operations for resumable processing.

Guidelines:
1. Break long checkpoint state serialization at logical points
2. For checkpoint comparison logic, use clear formatting
3. Complex state validation should use multi-line conditions
4. Long error messages about checkpoint conflicts should break
5. Timestamp and version handling should be clearly formatted

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_job_checkpoint.py
```

### 26. entities/receipt_label_analysis.py (15 line-too-long errors)

```
Please fix all 15 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file contains receipt label analysis entities with ML-based classifications.

Guidelines:
1. Break long confidence score calculations at operator boundaries
2. For label category mappings, use multi-line dictionaries
3. Complex validation rules should use parentheses for grouping
4. Long method names for analysis operations should break at underscores
5. Type annotations for ML model outputs should be clearly formatted

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/receipt_label_analysis.py
```

### 27. entities/job_checkpoint.py (14 line-too-long errors)

```
Please fix all 14 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file defines job checkpoint entities for resumable processing.

Guidelines:
1. Break long checkpoint state representations across lines
2. For serialization methods, use clear formatting
3. Timestamp comparisons should use intermediate variables
4. Complex state validation should be multi-line
5. String representations of checkpoints should be hierarchical

Only return the fixed code, no explanations needed.

File: receipt_dynamo/entities/job_checkpoint.py
```

### 28. data/_receipt_label_analysis.py (14 line-too-long errors)

```
Please fix all 14 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file manages receipt label analysis data operations.

Guidelines:
1. Break long ML model query parameters at boundaries
2. For batch analysis operations, use clear formatting
3. Complex filtering conditions should use parentheses
4. Long error messages about analysis failures should be multi-line
5. Confidence threshold comparisons should be clearly formatted

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_receipt_label_analysis.py
```

### 29. data/_job_resource.py (14 line-too-long errors)

```
Please fix all 14 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles job resource allocation and tracking.

Guidelines:
1. Break long resource allocation expressions at logical points
2. For resource limit validations, use clear formatting
3. Complex resource calculations should use intermediate variables
4. Long error messages about resource conflicts should be multi-line
5. Resource utilization queries should break at operator boundaries

Only return the fixed code, no explanations needed.

File: receipt_dynamo/data/_job_resource.py
```

### 30. data/_queue.py (30 line-too-long errors) - RETRY NEEDED

```
Please fix all 30 line-too-long (C0301) pylint errors in this Python file. The maximum line length is 79 characters.

This file handles queue operations with timestamp-based sorting and status updates. Previous attempts to fix this file may have missed some errors.

Guidelines:
1. Check ALL string literals - break long ones with parentheses for implicit concatenation
2. Break ALL method signatures that exceed 79 characters
3. Split long DynamoDB key constructions (PK/SK) at component boundaries
4. For update expressions with multiple attributes, use multi-line format
5. Ensure NO line exceeds 79 characters - count carefully!

Common issues to check:
- Long f-strings in error messages
- Method names with many underscores
- DynamoDB expression attribute values
- Complex conditional expressions
- Long docstring lines

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