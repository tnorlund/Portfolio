#!/usr/bin/env python3

import subprocess
import os
import re
import argparse
from collections import defaultdict
import glob
import sys
import json
import time
import importlib.metadata

# Check for required packages
try:
    import openai
except ImportError:
    print("Error: OpenAI package is not installed. Please install it with:")
    print("  pip install openai")
    sys.exit(1)

ANTHROPIC_AVAILABLE = True
try:
    import anthropic
except ImportError:
    ANTHROPIC_AVAILABLE = False

from getpass import getpass


def check_openai_version():
    """
    Check that the installed OpenAI package version is compatible.
    
    Returns:
        bool: True if compatible, False otherwise
    """
    try:
        version = importlib.metadata.version('openai')
        major_version = int(version.split('.')[0])
        
        if major_version >= 1:
            return True
        else:
            print("Warning: You are using an older version of the OpenAI package (v{}).".format(version))
            print("This script requires openai>=1.0.0.")
            print("Please upgrade with: pip install --upgrade openai")
            print("Or you can run: openai migrate")
            return False
    except Exception as e:
        print(f"Warning: Could not check OpenAI package version: {e}")
        return True  # Assume it's compatible


def run_flake8(file_path='.', exclude=None, config_file=None):
    """
    Run flake8 on the specified file or directory and return the output.
    
    Args:
        file_path (str): File or directory to run flake8 on
        exclude (list): List of patterns to exclude
        config_file (str): Path to flake8 config file
    
    Returns:
        str: Output from flake8
    """
    cmd = ['flake8']
    
    # Add file path
    cmd.append(file_path)
    
    # Add exclude patterns if specified
    if exclude:
        exclude_arg = '--exclude=' + ','.join(exclude)
        cmd.append(exclude_arg)
    
    # Add config file if specified
    if config_file:
        cmd.extend(['--config', config_file])
    
    # Run flake8 and capture output
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False  # Don't raise exception on flake8 errors
        )
        return result.stdout
    except Exception as e:
        print(f"Error running flake8: {e}")
        return ""


def parse_flake8_output(output):
    """
    Parse flake8 output and group by file.
    
    Args:
        output (str): Output from flake8
    
    Returns:
        dict: Dictionary with file paths as keys and lists of errors as values
    """
    errors_by_file = defaultdict(list)
    
    # Regular expression to parse flake8 output lines
    # Format: file_path:line:column: error_code error_message
    pattern = r'^(.+?):(\d+):(\d+): ([A-Z]\d+) (.+)$'
    
    for line in output.strip().split('\n'):
        if not line:
            continue
            
        match = re.match(pattern, line)
        if match:
            file_path, line_num, col_num, error_code, error_msg = match.groups()
            
            error_info = {
                'line': int(line_num),
                'column': int(col_num),
                'code': error_code,
                'message': error_msg
            }
            
            errors_by_file[file_path].append(error_info)
    
    return errors_by_file


def format_errors_for_chatgpt_prompt(file_path, errors, file_content=None):
    """
    Format errors for a single file as a prompt that will return only the fixed code.
    
    Args:
        file_path (str): Path to the file
        errors (list): List of errors for the file
        file_content (str, optional): Content of the file
        
    Returns:
        str: Formatted string for prompt
    """
    output_lines = []
    
    # Add clear instructions
    output_lines.append("You are a Python code reviewer specialized in fixing PEP8 and Flake8 errors.")
    output_lines.append("Fix all the Flake8 errors in the Python code below and return ONLY the corrected code.")
    output_lines.append("Do not include explanations, comments about what you changed, or any text other than the properly formatted Python code.")
    output_lines.append("")
    
    # Add file info and errors
    output_lines.append(f"File: {file_path}")
    output_lines.append(f"Found {len(errors)} Flake8 errors to fix:")
    
    # Add error list
    for error in sorted(errors, key=lambda e: (e['line'], e['column'])):
        output_lines.append(f"Line {error['line']}, Column {error['column']}: {error['code']} - {error['message']}")
    output_lines.append("")
    
    # Add file content (required for this use case)
    if file_content:
        output_lines.append("Here is the code with errors:")
        output_lines.append("```python")
        output_lines.append(file_content)
        output_lines.append("```")
        output_lines.append("")
        output_lines.append("Return ONLY the fixed code without explanations, and without the ```python and ``` markers.")
    else:
        output_lines.append("ERROR: File content is required to fix Flake8 errors.")
    
    return "\n".join(output_lines)


def read_file_content(file_path):
    """
    Read the content of a file.
    
    Args:
        file_path (str): Path to the file
        
    Returns:
        str: Content of the file or None if file doesn't exist
    """
    try:
        with open(file_path, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"Warning: Could not read file {file_path}: {e}")
        return None


def get_python_files(directory):
    """
    Get all Python files in a directory (recursively).
    
    Args:
        directory (str): Directory to search in
        
    Returns:
        list: List of Python file paths
    """
    if os.path.isfile(directory) and directory.endswith('.py'):
        return [directory]
    
    python_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    
    return python_files


def get_script_directory():
    """
    Get the directory where this script is located.
    
    Returns:
        str: Directory path
    """
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def fix_with_openai(prompt, api_key, model="gpt-3.5-turbo", retries=3, delay=2):
    """
    Use the OpenAI API to fix flake8 errors.
    
    Args:
        prompt (str): The prompt to send to the OpenAI API
        api_key (str): OpenAI API key
        model (str): OpenAI model to use (default: gpt-3.5-turbo)
        retries (int): Number of retries on failure
        delay (int): Delay between retries in seconds
        
    Returns:
        str: Fixed code from OpenAI or None if there was an error
    """
    client = openai.OpenAI(api_key=api_key)
    
    messages = [
        {"role": "system", "content": "You are a Python code reviewer specialized in fixing PEP8 and Flake8 errors. Return only the fixed code without any explanations."},
        {"role": "user", "content": prompt}
    ]
    
    for attempt in range(retries):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,  # Lower temperature for more precise code corrections
                max_tokens=4096
            )
            
            response = completion.choices[0].message.content.strip()
            
            # Extract code block if response has markdown formatting
            if "```python" in response:
                code_block_match = re.search(r'```python\n(.*?)\n```', response, re.DOTALL)
                if code_block_match:
                    return code_block_match.group(1).strip()
                else:
                    # Try without language specifier
                    code_block_match = re.search(r'```\n(.*?)\n```', response, re.DOTALL)
                    if code_block_match:
                        return code_block_match.group(1).strip()
            
            # If no code block markers, assume the entire response is code
            return response
            
        except openai.APIError as e:
            if "model not found" in str(e).lower():
                print(f"Error: Model '{model}' not found or not available to your account.")
                print("Available models may include: gpt-3.5-turbo, gpt-4, gpt-4o, etc.")
                print("Check available models in your OpenAI account.")
                return None
            elif "permission" in str(e).lower() or "access" in str(e).lower():
                print(f"Error: You don't have permission to use the model '{model}'.")
                print("Please check your OpenAI account for available models.")
                return None
            else:
                print(f"Error calling OpenAI API (attempt {attempt+1}/{retries}): {e}")
                if attempt < retries - 1:
                    print(f"Retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    print("Max retries reached. Could not get response from OpenAI.")
                    return None
        except Exception as e:
            print(f"Error calling OpenAI API (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print("Max retries reached. Could not get response from OpenAI.")
                return None


def fix_with_anthropic(prompt, api_key, model="claude-3-haiku-20240307", retries=3, delay=2):
    """
    Use the Anthropic API to fix flake8 errors.
    
    Args:
        prompt (str): The prompt to send to the Anthropic API
        api_key (str): Anthropic API key
        model (str): Anthropic model to use (default: claude-3-haiku-20240307)
        retries (int): Number of retries on failure
        delay (int): Delay between retries in seconds
        
    Returns:
        str: Fixed code from Anthropic or None if there was an error
    """
    if not ANTHROPIC_AVAILABLE:
        print("Error: The anthropic package is not installed. Please install it with:")
        print("  pip install anthropic")
        return None

    client = anthropic.Anthropic(api_key=api_key)
    
    system_prompt = "You are a Python code reviewer specialized in fixing PEP8 and Flake8 errors. Return only the fixed code without any explanations."
    
    for attempt in range(retries):
        try:
            completion = client.messages.create(
                model=model,
                max_tokens=4096,
                temperature=0.2,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            
            response = completion.content[0].text.strip()
            
            # Extract code block if response has markdown formatting
            if "```python" in response:
                code_block_match = re.search(r'```python\n(.*?)\n```', response, re.DOTALL)
                if code_block_match:
                    return code_block_match.group(1).strip()
                else:
                    # Try without language specifier
                    code_block_match = re.search(r'```\n(.*?)\n```', response, re.DOTALL)
                    if code_block_match:
                        return code_block_match.group(1).strip()
            
            # If no code block markers, assume the entire response is code
            return response
            
        except Exception as e:
            print(f"Error calling Anthropic API (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print("Max retries reached. Could not get response from Anthropic.")
                return None


def is_anthropic_model(model_name):
    """
    Check if the model name appears to be an Anthropic model.
    
    Args:
        model_name (str): Name of the model
        
    Returns:
        bool: True if it's likely an Anthropic model, False otherwise
    """
    anthropic_patterns = [
        'claude', 'opus', 'sonnet', 'haiku', 'o3', 'anthropic'
    ]
    
    model_lower = model_name.lower()
    return any(pattern in model_lower for pattern in anthropic_patterns)


def write_fixed_code(file_path, fixed_code, backup=True, suffix='_fixed'):
    """
    Write fixed code to a file.
    
    Args:
        file_path (str): Original file path
        fixed_code (str): Fixed code to write
        backup (bool): Whether to write to a new file or overwrite the original
        suffix (str): Suffix to add to new filename if backup is True
        
    Returns:
        str: Path to the file containing the fixed code
    """
    if backup:
        # Create a new file with the suffix
        base_name, ext = os.path.splitext(file_path)
        new_file_path = f"{base_name}{suffix}{ext}"
    else:
        new_file_path = file_path
    
    try:
        with open(new_file_path, 'w') as f:
            f.write(fixed_code)
        return new_file_path
    except Exception as e:
        print(f"Error writing fixed code to {new_file_path}: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description='Fix Flake8 errors using AI (OpenAI or Anthropic) or generate prompts for manual fixing')
    parser.add_argument('-d', '--directory', default='.', help='Directory to scan for Python files')
    parser.add_argument('-e', '--exclude', nargs='*', help='Patterns to exclude')
    parser.add_argument('-c', '--config', help='Path to flake8 config file')
    parser.add_argument('-f', '--file_list', help='Path to a file containing a list of files to check, one per line')
    parser.add_argument('-o', '--output_dir', help='Output directory for ChatGPT prompts when using prompt mode (default: same directory as this script)')
    parser.add_argument('-s', '--suffix', default='_fix', help='Suffix to add to filenames (default: _fix)')
    parser.add_argument('-a', '--api_key', help='OpenAI or Anthropic API key (automatically detected based on model)')
    parser.add_argument('--openai_key', help='OpenAI API key (overrides -a/--api_key for OpenAI models)')
    parser.add_argument('--anthropic_key', help='Anthropic API key (overrides -a/--api_key for Anthropic models)')
    parser.add_argument('-m', '--model', default='gpt-3.5-turbo', help='AI model to use (default: gpt-3.5-turbo)')
    parser.add_argument('--prompt_only', action='store_true', help='Generate prompts only, do not call AI APIs')
    parser.add_argument('--backup', action='store_true', help='Create a new file with fixed code instead of overwriting the original')
    parser.add_argument('--test_api', action='store_true', help='Test API connection and exit')
    parser.add_argument('--list_models', action='store_true', help='List recommended models and exit')
    
    args = parser.parse_args()
    
    print("🔍 Flake8 Fixer v3.0 - Now with OpenAI and Anthropic Claude integration!")
    
    # List recommended models if requested
    if args.list_models:
        print("\nRecommended OpenAI models:")
        print(" - gpt-3.5-turbo (default, good for most cases)")
        print(" - gpt-4 (more capable but more expensive)")
        print(" - gpt-4o (newest model, very capable)")
        
        print("\nRecommended Anthropic Claude models:")
        print(" - claude-3-haiku-20240307 (fast, cost-effective)")
        print(" - claude-3-sonnet-20240229 (better quality)")
        print(" - claude-3-opus-20240229 (highest quality)")
        print("\nNote: For Claude models, you need to install the anthropic package:")
        print("  pip install anthropic")
        sys.exit(0)
    
    print(f"Using model: {args.model}")
    
    # Check if the model is likely an Anthropic model
    using_anthropic = is_anthropic_model(args.model)
    if using_anthropic:
        if not ANTHROPIC_AVAILABLE:
            print("Error: You're trying to use an Anthropic Claude model but the anthropic package is not installed.")
            print("Please install it with:")
            print("  pip install anthropic")
            print("\nAlternatively, use an OpenAI model like gpt-3.5-turbo or gpt-4o")
            sys.exit(1)
        print("Detected Anthropic Claude model")
    else:
        print("Detected OpenAI model")
    
    # Determine if we're using an AI API or just generating prompts
    using_api = not args.prompt_only
    
    # Check OpenAI version compatibility if using OpenAI
    if using_api and not using_anthropic and not check_openai_version():
        sys.exit(1)
    
    # Get appropriate API key if using an API
    api_key = None
    if using_api:
        if using_anthropic:
            # For Anthropic models
            api_key = args.anthropic_key or args.api_key
            if not api_key:
                # Try to get from environment variable
                api_key = os.environ.get('ANTHROPIC_API_KEY')
                if not api_key:
                    # Prompt for API key
                    api_key = getpass("Enter your Anthropic API key (starts with 'sk-ant-'): ")
        else:
            # For OpenAI models
            api_key = args.openai_key or args.api_key
            if not api_key:
                # Try to get from environment variable
                api_key = os.environ.get('OPENAI_API_KEY')
                if not api_key:
                    # Prompt for API key
                    api_key = getpass("Enter your OpenAI API key: ")
    
    # Test API connection if requested
    if using_api and args.test_api:
        print("Testing API connection...")
        try:
            if using_anthropic:
                if not ANTHROPIC_AVAILABLE:
                    print("Error: The anthropic package is not installed.")
                    sys.exit(1)
                
                client = anthropic.Anthropic(api_key=api_key)
                # Just check if we can get models - if API key is wrong, this will fail
                print("✅ Anthropic API connection successful!")
                print("Available Claude models include:")
                print(" - claude-3-opus-20240229 (highest quality)")
                print(" - claude-3-sonnet-20240229 (balanced)")
                print(" - claude-3-haiku-20240307 (fastest)")
                
                specified_model = args.model
                print(f"Note: Anthropic doesn't provide a model list via API, so we can't verify if '{specified_model}' is available.")
                if not is_anthropic_model(specified_model):
                    print(f"⚠️ Warning: '{specified_model}' doesn't appear to be an Anthropic model name.")
            else:
                client = openai.OpenAI(api_key=api_key)
                # Make a small request to ensure API key works
                models = client.models.list()
                print("✅ OpenAI API connection successful! Available models include:")
                for model in models.data:
                    print(f" - {model.id}")
                # Try to find if the specified model is available
                specified_model = args.model
                model_found = False
                for model in models.data:
                    if model.id.lower() == specified_model.lower():
                        model_found = True
                        break
                if model_found:
                    print(f"✅ Specified model '{specified_model}' is available.")
                else:
                    print(f"⚠️ Specified model '{specified_model}' was not found in the list of available models.")
                    print("   The model may still be available but not listed, or you may need to use a different model.")
            sys.exit(0)
        except Exception as e:
            print(f"❌ API connection failed: {e}")
            sys.exit(1)
    
    # Determine files to check
    files_to_check = []
    if args.file_list:
        try:
            with open(args.file_list, 'r') as f:
                files_to_check = [line.strip() for line in f if line.strip()]
        except Exception as e:
            print(f"Error reading file list: {e}")
            return
    else:
        # Get all Python files in the directory
        files_to_check = get_python_files(args.directory)
    
    if not files_to_check:
        print(f"No Python files found in {args.directory}")
        return
    
    print(f"Checking {len(files_to_check)} Python files for Flake8 errors...")
    
    # Determine output directory for prompt mode
    output_dir = None
    if args.prompt_only:
        if args.output_dir:
            output_dir = args.output_dir
        else:
            # Use the directory where this script is located
            output_dir = get_script_directory()
        
        # Create output directory if needed
        os.makedirs(output_dir, exist_ok=True)
    
    # Process each file individually
    files_with_errors = 0
    total_errors = 0
    files_fixed = 0
    
    for file_path in files_to_check:
        flake8_output = run_flake8(file_path, args.exclude, args.config)
        
        if not flake8_output:
            continue  # No errors for this file
        
        errors_by_file = parse_flake8_output(flake8_output)
        
        if not errors_by_file or file_path not in errors_by_file:
            continue  # No errors for this file
        
        errors = errors_by_file[file_path]
        
        # Explicitly read the file content
        file_content = read_file_content(file_path)
        
        if not file_content:
            print(f"Warning: Could not read content of {file_path}, skipping...")
            continue
        
        # Create the prompt with the file content
        prompt = format_errors_for_chatgpt_prompt(file_path, errors, file_content)
        
        files_with_errors += 1
        total_errors += len(errors)
        
        if using_api:
            # Use AI to fix the code
            ai_provider = "Anthropic" if using_anthropic else "OpenAI"
            print(f"Fixing {file_path} with {len(errors)} errors using {ai_provider} {args.model}...")
            
            if using_anthropic:
                fixed_code = fix_with_anthropic(prompt, api_key, args.model)
            else:
                fixed_code = fix_with_openai(prompt, api_key, args.model)
            
            if fixed_code:
                # Write the fixed code to file
                output_file = write_fixed_code(file_path, fixed_code, args.backup, args.suffix)
                if output_file:
                    print(f"✅ Fixed {file_path} -> {output_file}")
                    files_fixed += 1
                else:
                    print(f"❌ Failed to write fixed code for {file_path}")
            else:
                print(f"❌ Failed to fix {file_path}")
        else:
            # Write the prompt to the output file
            file_name = os.path.basename(file_path)
            base_name, _ = os.path.splitext(file_name)
            output_file = os.path.join(output_dir, f"{base_name}{args.suffix}.md")
            
            with open(output_file, 'w') as f:
                f.write(prompt)
            
            print(f"Created prompt for {file_path} with {len(errors)} errors -> {output_file}")
    
    if files_with_errors == 0:
        print("No Flake8 errors found in any files!")
    else:
        if using_api:
            print(f"Found {files_with_errors} files with a total of {total_errors} errors.")
            print(f"Successfully fixed {files_fixed} files.")
        else:
            print(f"Created prompts for {files_with_errors} files with a total of {total_errors} errors.")
            print(f"All prompts are saved in: {output_dir}")


if __name__ == '__main__':
    main() 