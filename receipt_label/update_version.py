#!/usr/bin/env python3
"""
Script to update version numbers in all package files.
Run this whenever you change the version in receipt_label/version.py.
"""

import os
import re

# Get the version from version.py
version_file = os.path.join("receipt_label", "version.py")
with open(version_file, "r") as f:
    version_content = f.read()

# Extract version using regex
version_match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', version_content)
if not version_match:
    raise RuntimeError(f"Unable to find version string in {version_file}")
version = version_match.group(1)
print(f"Found version: {version}")

# Update pyproject.toml
pyproject_file = "pyproject.toml"
with open(pyproject_file, "r") as f:
    pyproject_content = f.read()

pyproject_content = re.sub(
    r'(version\s*=\s*["\']).+(["\'])', rf"\1{version}\2", pyproject_content
)

with open(pyproject_file, "w") as f:
    f.write(pyproject_content)
print(f"Updated {pyproject_file}")

print("Version update complete!")
print("Single source of truth maintained in receipt_label/version.py")
