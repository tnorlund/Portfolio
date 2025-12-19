#!/usr/bin/env python3
import ast
import json
import os
import re
import sys
from typing import List

import requests

# ─── STEP 1: LISTING ALL TEST FUNCTIONS ─────────────────────────────


def find_test_functions_in_file(file_path: str) -> List[str]:
    """
    Return a list of all function names that start with `test_` in the given file.
    """
    test_functions = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                test_functions.append(node.name)
    except (SyntaxError, UnicodeDecodeError):
        pass
    return test_functions


def list_all_test_functions_to_string(base_dir: str) -> str:
    """
    Walk through the given directory (recursively) and return a string listing all test functions,
    grouped by file.
    """
    results = []
    for root, dirs, files in os.walk(base_dir):
        for filename in files:
            if filename.endswith(".py"):
                file_path = os.path.join(root, filename)
                tests = find_test_functions_in_file(file_path)
                if tests:
                    results.append(f"File: {file_path}")
                    for test_name in tests:
                        results.append(f"  - {test_name}")
    return "\n".join(results)


# ─── STEP 2: CALLING THE OPENAI API ──────────────────────────────────


def call_openai_api(prompt: str) -> dict:
    """
    Given a prompt string, calls the OpenAI API (using the chat/completions endpoint)
    and returns the JSON mapping provided by the API.

    The API is expected to return a JSON string that maps file paths to dictionaries
    mapping old test function names to new test function names.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Please set the OPENAI_API_KEY environment variable.")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that returns JSON output.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }

    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    resp_json = response.json()

    content = resp_json["choices"][0]["message"]["content"]
    try:
        mapping = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(
            "OpenAI API did not return valid JSON. Response content: " + content
        ) from e
    return mapping  # type: ignore[no-any-return]


# ─── STEP 3: APPLYING THE RENAMING ───────────────────────────────────


def rename_test_functions_in_file(file_path: str, mapping: dict):
    """
    Given a file path and a mapping (old function name -> new function name),
    read the file, perform the rename on function definitions, and write back if changes occur.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    changed = False
    new_lines = []
    # Build a regex to match function definitions that start with one of the
    # keys.
    pattern = re.compile(
        r"^(\s*)def\s+(" + "|".join(map(re.escape, mapping.keys())) + r")(\s*\(.*)?:"
    )

    for line in lines:
        match = pattern.match(line)
        if match:
            indent = match.group(1)
            old_def_name = match.group(2)
            remainder = match.group(3) if match.group(3) else "():"
            new_def_name = mapping[old_def_name]
            new_line = f"{indent}def {new_def_name}{remainder}:\n"
            new_lines.append(new_line)
            changed = True
        else:
            new_lines.append(line)

    if changed:
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"Updated {file_path}")


def rename_test_functions_in_dir(base_dir: str, rename_mapping: dict):
    """
    Walk through the given directory and, if a file's relative path (from current working dir)
    is a key in rename_mapping, apply the renames specified in its corresponding dictionary.
    """
    for root, dirs, files in os.walk(base_dir):
        for filename in files:
            if filename.endswith(".py"):
                file_path = os.path.join(root, filename)
                # Get the relative path (e.g., "unit/test_word.py")
                rel_path = os.path.relpath(file_path, os.getcwd())
                if rel_path in rename_mapping:
                    file_mapping = rename_mapping[rel_path]
                    rename_test_functions_in_file(file_path, file_mapping)


# ─── MAIN SCRIPT ─────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        print("Usage: python clean_test_names.py <unit|integration>")
        sys.exit(1)

    test_dir = sys.argv[1]
    if not os.path.isdir(test_dir):
        print(f"Directory '{test_dir}' does not exist.")
        sys.exit(1)

    # 1. List all test functions in the specified directory.
    test_functions_str = list_all_test_functions_to_string(test_dir)
    print("Found test functions:")
    print(test_functions_str)

    # 2. Build a prompt to ask OpenAI for a renaming mapping.
    prompt = (
        "I am providing you with a list of test function definitions from a codebase. "
        "Please output a JSON mapping where each key is a file path (relative to the project root) and each value is a dictionary mapping "
        "old test function names to new test function names. The new test function names should follow a consistent naming scheme. "
        'The JSON format should be like: {"unit/test_filename.py": {"old_name": "new_name", ...}, ...}.\n'
        "Here is the list:\n" + test_functions_str
    )

    try:
        rename_mapping = call_openai_api(prompt)
    except Exception as e:
        print("Error calling OpenAI API:", e)
        sys.exit(1)

    print("Received renaming mapping:")
    print(json.dumps(rename_mapping, indent=2))

    # 3. Apply the renaming mapping to files in the given directory.
    rename_test_functions_in_dir(test_dir, rename_mapping)


if __name__ == "__main__":
    main()
