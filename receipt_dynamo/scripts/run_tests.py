#!/usr/bin/env python
import os
import subprocess
import sys

# Change to the receipt_dynamo directory
os.chdir("/Users/tnorlund/GitHub/issue-121/receipt_dynamo")

# Run pytest
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/integration", "-v", "--tb=short"],
    capture_output=True,
    text=True,
)

# Print output
print(result.stdout)
print(result.stderr)

# Get summary
lines = result.stdout.split("\n")
for line in lines[-20:]:
    if "passed" in line or "failed" in line or "error" in line:
        print(f"SUMMARY: {line}")
