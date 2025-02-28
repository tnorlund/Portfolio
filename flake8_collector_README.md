# Flake8 Error Collector

This script scans Python files for Flake8 errors and can either:
1. Create prompts for ChatGPT to fix the errors manually
2. Automatically fix errors using the ChatGPT API

## Requirements

- Python 3.6+
- Flake8 (`pip install flake8`)
- Requests (`pip install requests`) - only needed for API functionality

## Usage

```bash
./flake8_collector.py [options]
```

### Basic Options

- `-d, --directory`: Directory to scan for Python files (default: current directory)
- `-e, --exclude`: Patterns to exclude (space-separated)
- `-c, --config`: Path to a Flake8 config file
- `-f, --file_list`: Path to a file containing a list of specific Python files to check, one per line
- `-o, --output_dir`: Custom output directory for ChatGPT prompts (default: same directory as this script)
- `-s, --suffix`: Suffix to add to markdown filenames (default: '_fix')

### API-related Options

- `--api`: Use ChatGPT API to automatically fix errors
- `--api-key`: OpenAI API key (if not set, uses OPENAI_API_KEY environment variable)
- `--model`: OpenAI model to use (default: "gpt-4")
- `--temperature`: Temperature for API requests (default: 0.2)
- `--apply`: Apply the fixes to the original files (requires --api)
- `--fixed-suffix`: Suffix for fixed Python files (default: '_fixed')
- `--validate`: Validate the fixed code by running flake8 on it again

## Examples

### Manual Mode (Default)

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

### API Mode (Automatic Fixing)

Fix errors using the API and save to new files:
```bash
./flake8_collector.py -d ./my_project --api --api-key your_api_key
```

Fix errors and validate the fixed code:
```bash
./flake8_collector.py -d ./my_project --api --validate
```

Fix errors and apply directly to original files (creates backups):
```bash
./flake8_collector.py -d ./my_project --api --apply
```

Use a different model:
```bash
./flake8_collector.py -d ./my_project --api --model gpt-3.5-turbo
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

## Workflows

### Manual Workflow

1. Run the script to generate prompts:
   ```bash
   ./flake8_collector.py -d ./your_project
   ```

2. The script will create one markdown file per Python file with errors and save them in the same directory as the script

3. Copy the contents of a prompt file and paste it into a new ChatGPT conversation

4. ChatGPT will return only the fixed code (without explanations)

5. Copy the fixed code and replace the original file with it

### Automatic Workflow (using API)

1. Set your OpenAI API key (either as an environment variable or using the --api-key option)

2. Run the script with API mode:
   ```bash
   # Save fixed code to new files
   ./flake8_collector.py -d ./your_project --api

   # Or apply fixes directly to original files (with backups)
   ./flake8_collector.py -d ./your_project --api --apply
   ```

3. The script will:
   - Detect Flake8 errors
   - Send each file with errors to the ChatGPT API
   - Save the fixed code to new files or apply it to the original files
   - Validate the fixes if requested (--validate)
   - Generate a summary report 