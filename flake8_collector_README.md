# Flake8 Error Collector

This script scans Python files for Flake8 errors and creates prompts that instruct ChatGPT to return *ONLY* the fixed code. It generates one markdown file per Python file with errors, saving them in the same directory as the script.

## Requirements

- Python 3.6+
- Flake8 (`pip install flake8`)

## Usage

```bash
./flake8_collector.py [options]
```

### Options

- `-d, --directory`: Directory to scan for Python files (default: current directory)
- `-e, --exclude`: Patterns to exclude (space-separated)
- `-c, --config`: Path to a Flake8 config file
- `-f, --file_list`: Path to a file containing a list of specific Python files to check, one per line
- `-o, --output_dir`: Custom output directory for ChatGPT prompts (default: same directory as this script)
- `-s, --suffix`: Suffix to add to markdown filenames (default: '_fix')

### Examples

Scan all Python files in the current directory:
```bash
./flake8_collector.py
```

Scan a specific directory:
```bash
./flake8_collector.py -d ./my_project
```

Specify a custom output directory:
```bash
./flake8_collector.py -d ./my_project -o my_fixes
```

Use a different suffix for output files:
```bash
./flake8_collector.py -s _flake8_fixes
```

Check only specific files:
```bash
./flake8_collector.py -f files_to_check.txt
```

Exclude certain patterns:
```bash
./flake8_collector.py -e "*/migrations/*" "*/venv/*"
```

Use a custom Flake8 config file:
```bash
./flake8_collector.py -c ./.flake8
```

## Output Format

The script generates Markdown files with clear instructions for ChatGPT to return only the fixed code. Each prompt has this structure:

```markdown
# Fix Flake8 Errors - Code Only Response

## Instructions
Fix all the Flake8 errors in the file below and return ONLY the corrected code. Do not include explanations, comments about what you changed, or any text other than the properly formatted Python code. The fixed code should be returned as a single code block.

## File: path/to/file.py
Total errors: 3

## Errors to Fix
Line 10, Column 5: E302 - Expected 2 blank lines, found 1
Line 15, Column 80: E501 - Line too long (100 > 79 characters)
Line 20, Column 1: F401 - 'module' imported but unused

## Original Code
```python
# Original file content here
```

## Return ONLY the fixed code below
```

## Workflow with ChatGPT

The workflow is simplified to focus on getting only the fixed code:

1. Run the script to generate prompts:
   ```bash
   ./flake8_collector.py -d ./your_project
   ```

2. The script will create one markdown file per Python file with errors and save them in the same directory as the script

3. Copy the contents of a prompt file and paste it into a new ChatGPT conversation

4. ChatGPT will return only the fixed code (without explanations)

5. Copy the fixed code and replace the original file with it

This workflow allows you to quickly fix all Flake8 issues in your codebase without having to sift through explanations. 