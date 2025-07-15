#!/usr/bin/env python3
"""
Run validation on all available receipts with labels.
Combines both receipt_data_with_labels and receipt_data_with_labels_full directories.
"""

import os
import sys
import asyncio
from pathlib import Path
import shutil

# Set up environment  
os.environ["OPENAI_API_KEY"] = "sk-dummy"

print("🔄 PREPARING FULL VALIDATION SET")
print("=" * 60)

# Create a combined directory
combined_dir = Path("./receipt_data_combined")
combined_dir.mkdir(exist_ok=True)

# Copy files from both directories
sources = [
    Path("./receipt_data_with_labels"),
    Path("./receipt_data_with_labels_full")
]

copied_files = set()
for source_dir in sources:
    if source_dir.exists():
        for json_file in source_dir.glob("*.json"):
            # Skip non-receipt files
            if json_file.name in ["manifest.json", "four_fields_simple_results.json", 
                                  "metadata_impact_analysis.json", "pattern_vs_labels_comparison.json",
                                  "required_fields_analysis.json", "spatial_line_item_results_labeled.json"]:
                continue
            
            # Only copy if we don't already have it
            if json_file.name not in copied_files:
                dest = combined_dir / json_file.name
                if not dest.exists():
                    shutil.copy2(json_file, dest)
                copied_files.add(json_file.name)

print(f"✅ Combined {len(copied_files)} unique receipt files")
print(f"📂 Combined directory: {combined_dir}")

# Now run the validation on the combined set
print("\n" + "=" * 60)
print("Running validation on combined dataset...")
print("=" * 60 + "\n")

# Import and run the validation
sys.path.insert(0, str(Path(__file__).parent))
from validate_spatial_accuracy_v2 import validate_all_receipts

# Temporarily change to use combined directory
import validate_spatial_accuracy_v2
original_dir = Path("./receipt_data_with_labels_full")
validate_spatial_accuracy_v2.Path = lambda x: combined_dir if x == "./receipt_data_with_labels_full" else Path(x)

# Run validation
asyncio.run(validate_all_receipts())