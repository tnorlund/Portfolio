#!/usr/bin/env python3
"""Review the 7 failed validations."""

import json
from pathlib import Path

export_dir = Path('dev.local_export')

# Collect all results with modification times
results = []
for json_file in export_dir.glob('*.json'):
    mtime = json_file.stat().st_mtime
    with open(json_file) as f:
        data = json.load(f)
    
    if 'selected_metadata' in data:
        meta = data['selected_metadata']
        results.append({
            'file': json_file.stem,
            'mtime': mtime,
            'image_id': meta.get('image_id', 'N/A'),
            'receipt_id': meta.get('receipt_id', 'N/A'),
            'merchant': meta.get('merchant_name', 'UNKNOWN'),
            'method': meta.get('validated_by', 'N/A'),
            'place_id': meta.get('place_id', ''),
            'address': meta.get('address', ''),
            'phone': meta.get('phone_number', ''),
            'receipts': len(data.get('receipts', [])),
            'runs': data.get('runs', [])
        })

# Sort by modification time and get the 12 most recent
results.sort(key=lambda x: x['mtime'], reverse=True)
recent_12 = results[:12]

# Find the 7 failed ones (no place_id or place_id='unknown')
failed = []
for r in recent_12:
    has_place_id = bool(r['place_id'] and len(r['place_id']) > 10 and r['place_id'] != 'unknown')
    if not has_place_id:
        failed.append(r)

print('=' * 70)
print('7 FAILED VALIDATIONS (NO PLACE ID)')
print('=' * 70)

for i, f in enumerate(failed[:7], 1):
    print(f"\n{i}. Image ID: {f['image_id']}")
    print(f"   Receipt ID: {f['receipt_id']}")
    print(f"   Merchant Name (attempted): {f['merchant']}")
    print(f"   Validation Method: {f['method']}")
    if f['address']:
        print(f"   Address (attempted): {f['address'][:50]}...")
    if f['phone']:
        print(f"   Phone (attempted): {f['phone']}")
    if f['receipts'] > 1:
        print(f"   ⚠️  MULTI-RECEIPT IMAGE: {f['receipts']} receipts")
    print('-' * 40)