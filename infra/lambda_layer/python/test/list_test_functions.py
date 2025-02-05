import os
import ast

def find_test_functions_in_file(file_path: str) -> list[str]:
    """Return a list of all function names that start with `test_` in the given file."""
    test_functions = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                test_functions.append(node.name)
    except (SyntaxError, UnicodeDecodeError):
        # Skip files that cannot be parsed or read properly
        pass
    return test_functions

def list_all_test_functions(base_dir: str):
    """Walk through the given directory (recursively) and list all test functions."""
    for root, dirs, files in os.walk(base_dir):
        for filename in files:
            if filename.endswith(".py"):
                file_path = os.path.join(root, filename)
                tests = find_test_functions_in_file(file_path)
                if tests:
                    print(f"File: {file_path}")
                    for test_name in tests:
                        print(f"  - {test_name}")

if __name__ == "__main__":
    # Adjust 'unit' to your preferred directory if needed.
    UNIT_TEST_DIR = "unit"
    list_all_test_functions(UNIT_TEST_DIR)