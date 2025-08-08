#!/usr/bin/env python3
"""Gather the 5 successful validations into a JSON file."""

import json
from pathlib import Path
from datetime import datetime

export_dir = Path('dev.local_export')

# Collect all results with modification times
all_results = []
for json_file in export_dir.glob('*.json'):
    mtime = json_file.stat().st_mtime
    with open(json_file) as f:
        data = json.load(f)
    
    if 'selected_metadata' in data:
        meta = data['selected_metadata']
        place_id = meta.get('place_id', '')
        
        # Only include if has valid place_id and single receipt
        if place_id and len(place_id) > 10 and place_id != 'unknown':
            all_results.append({
                'file': json_file.stem,
                'mtime': mtime,
                'metadata': meta,
                'receipt_count': len(data.get('receipts', []))
            })

# Sort by modification time and get most recent
all_results.sort(key=lambda x: x['mtime'], reverse=True)

# Filter to get the 5 most recent successful ones from single-receipt images
successful = []
for result in all_results[:12]:  # Check recent 12
    if result['receipt_count'] == 1:  # Single receipt only
        successful.append(result['metadata'])
        if len(successful) >= 5:
            break

print(f"Found {len(successful)} successful validations")

# Create output in same format as additional_metadata.json
output = {
    "generated_at": datetime.now().isoformat(),
    "total_count": len(successful),
    "metadata": successful
}

# Save to file
with open("five_successful_metadata.json", "w") as f:
    json.dump(output, f, indent=2, default=str)

print("✅ Created five_successful_metadata.json")

# Display what we found
for i, meta in enumerate(successful, 1):
    print(f"\n{i}. {meta.get('merchant_name', 'UNKNOWN')}:")
    print(f"   Image: {meta.get('image_id', 'N/A')}#{meta.get('receipt_id', 'N/A')}")
    print(f"   Place ID: {meta.get('place_id', 'N/A')[:40]}...")