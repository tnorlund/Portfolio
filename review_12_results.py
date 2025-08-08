#!/usr/bin/env python3
"""Review the 12 most recent validation results."""

import json
from pathlib import Path
from datetime import datetime

export_dir = Path('dev.local_export')

# Collect all results with their modification times
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
            'merchant': meta.get('merchant_name', 'UNKNOWN'),
            'method': meta.get('validated_by', 'N/A'),
            'place_id': meta.get('place_id', ''),
            'address': meta.get('address', ''),
            'phone': meta.get('phone_number', ''),
            'timestamp': meta.get('timestamp', ''),
            'receipts': len(data.get('receipts', []))
        })

# Sort by modification time and get the 12 most recent
results.sort(key=lambda x: x['mtime'], reverse=True)
recent_12 = results[:12]

print('=' * 80)
print('12 MOST RECENT VALIDATION RESULTS')
print('=' * 80)

success_count = 0
failed = []

for i, r in enumerate(recent_12, 1):
    has_place_id = bool(r['place_id'] and len(r['place_id']) > 10 and r['place_id'] != 'unknown')
    
    if has_place_id:
        status = '✅'
        success_count += 1
    else:
        status = '❌'
        failed.append(r)
    
    print(f"\n{i:2}. {status} {r['merchant'] or 'UNKNOWN'}")
    print(f"    Method: {r['method']}")
    
    if has_place_id:
        print(f"    Place ID: {r['place_id'][:30]}...")
    else:
        print(f"    Place ID: NONE")
    
    if r['address']:
        print(f"    Address: {r['address'][:50]}")
    
    if r['phone']:
        print(f"    Phone: {r['phone']}")
    
    if r['receipts'] > 1:
        print(f"    ⚠️  WARNING: {r['receipts']} receipts in image (data may be mixed)")

print('\n' + '=' * 80)
print('SUMMARY OF 12 MOST RECENT:')
print('-' * 40)
print(f'✅ Successful (with Place ID): {success_count}')
print(f'❌ Failed (no Place ID): {len(failed)}')

if failed:
    print('\nFailed validations:')
    for f in failed:
        print(f"  - {f['merchant'] or 'UNKNOWN'}: {f['method']}")

# Check which merchants were found
merchants = {}
for r in recent_12:
    if r['place_id'] and len(r['place_id']) > 10:
        m = r['merchant']
        if m not in merchants:
            merchants[m] = 0
        merchants[m] += 1

if merchants:
    print('\n🏪 Successfully identified merchants:')
    for merchant, count in sorted(merchants.items()):
        print(f"  - {merchant}: {count} receipt(s)")