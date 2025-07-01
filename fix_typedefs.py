#!/usr/bin/env python3
"""Fix TypeDef imports that are used at runtime but only imported in TYPE_CHECKING blocks."""

import os
import re
from pathlib import Path

# Directory to process
DATA_DIR = Path(
    "/Users/tnorlund/GitHub/example-issue-130/receipt_dynamo/receipt_dynamo/data"
)

# TypeDefs that are used at runtime
RUNTIME_TYPEDEFS = {
    "WriteRequestTypeDef",
    "PutRequestTypeDef",
    "DeleteRequestTypeDef",
    "TransactWriteItemTypeDef",
    "PutTypeDef",
    "DeleteTypeDef",
}


def find_runtime_usage(content):
    """Find which TypeDefs are used at runtime in the file content."""
    used_typedefs = set()

    for typedef in RUNTIME_TYPEDEFS:
        # Look for usage as constructor: TypeDef(
        pattern = rf"\b{typedef}\s*\("
        if re.search(pattern, content):
            used_typedefs.add(typedef)

    return used_typedefs


def fix_imports(file_path):
    """Fix imports in a single file."""
    with open(file_path, "r") as f:
        content = f.read()

    # Find which TypeDefs are used at runtime
    used_typedefs = find_runtime_usage(content)

    if not used_typedefs:
        return False  # No changes needed

    # Find the TYPE_CHECKING block with _base imports
    type_checking_pattern = r"if TYPE_CHECKING:\s*\n\s*from receipt_dynamo\.data\._base import \(([\s\S]*?)\)"
    match = re.search(type_checking_pattern, content)

    if not match:
        print(f"No TYPE_CHECKING block found in {file_path}")
        return False

    # Extract imports from TYPE_CHECKING block
    imports_text = match.group(1)
    imports_in_block = {imp.strip() for imp in re.findall(r"\w+TypeDef", imports_text)}

    # Determine which imports to keep in TYPE_CHECKING and which to move out
    keep_in_type_checking = imports_in_block - used_typedefs
    move_to_runtime = imports_in_block & used_typedefs

    if not move_to_runtime:
        return False  # No changes needed

    # Build new import statements
    if keep_in_type_checking:
        # Some imports stay in TYPE_CHECKING
        type_checking_imports = sorted(keep_in_type_checking)
        new_type_checking = f"    from receipt_dynamo.data._base import (\n"
        for imp in type_checking_imports[:-1]:
            new_type_checking += f"        {imp},\n"
        new_type_checking += f"        {type_checking_imports[-1]},\n    )"
    else:
        # No imports left in TYPE_CHECKING for _base
        new_type_checking = None

    # Build runtime imports
    runtime_imports = sorted(move_to_runtime)
    new_runtime = f"\n# These are used at runtime, not just for type checking\nfrom receipt_dynamo.data._base import (\n"
    for imp in runtime_imports[:-1]:
        new_runtime += f"    {imp},\n"
    new_runtime += f"    {runtime_imports[-1]},\n)"

    # Replace the old TYPE_CHECKING block
    old_block = match.group(0)

    # Find where to insert runtime imports (after TYPE_CHECKING block)
    type_checking_end = content.find(old_block) + len(old_block)

    # Build the new content
    if new_type_checking:
        # Replace the _base import in TYPE_CHECKING
        new_block = re.sub(
            r"from receipt_dynamo\.data\._base import \([^)]+\)",
            new_type_checking.strip(),
            old_block,
        )
        new_content = (
            content[: content.find(old_block)]
            + new_block
            + new_runtime
            + content[type_checking_end:]
        )
    else:
        # Remove the _base import from TYPE_CHECKING entirely
        lines = old_block.split("\n")
        new_lines = []
        skip_next = False
        for i, line in enumerate(lines):
            if "from receipt_dynamo.data._base import" in line:
                skip_next = True
                continue
            if skip_next and ")" in line:
                skip_next = False
                continue
            if not skip_next:
                new_lines.append(line)
        new_block = "\n".join(new_lines)
        new_content = (
            content[: content.find(old_block)]
            + new_block
            + new_runtime
            + content[type_checking_end:]
        )

    # Write the fixed content
    with open(file_path, "w") as f:
        f.write(new_content)

    print(
        f"Fixed {file_path.name}: moved {', '.join(sorted(move_to_runtime))} to runtime imports"
    )
    return True


def main():
    """Process all Python files in the data directory."""
    fixed_count = 0

    for file_path in DATA_DIR.glob("*.py"):
        if file_path.name == "_base.py":
            continue  # Skip the base file itself

        if fix_imports(file_path):
            fixed_count += 1

    print(f"\nFixed {fixed_count} files")


if __name__ == "__main__":
    main()
