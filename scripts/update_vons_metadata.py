#!/usr/bin/env python3
"""Update ReceiptMetadata for a receipt from A Tea Thing to Vons."""

import os
import sys
from datetime import datetime, timezone

# Add paths
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from receipt_dynamo import DynamoClient

def main():
    table_name = os.environ.get('DYNAMODB_TABLE_NAME', 'ReceiptsTable-dc5be22')
    dynamo = DynamoClient(table_name)

    image_id = '00ded398-af6f-4a49-86f7-c79ccb554e48'
    receipt_id = 1

    # Get existing metadata
    print(f'Getting metadata for {image_id}#{receipt_id}...')
    metadata = dynamo.get_receipt_metadata(image_id=image_id, receipt_id=receipt_id)

    print(f'Current metadata:')
    print(f'  merchant_name: {metadata.merchant_name}')
    print(f'  place_id: {metadata.place_id}')
    print(f'  address: {metadata.address}')

    # Find a Vons place_id from other receipts
    print('\nSearching for Vons receipts to get correct place_id...')
    vons_receipts, _ = dynamo.get_receipt_metadatas_by_merchant('VONS', limit=10)

    if not vons_receipts:
        print('No Vons receipts found. Searching for "Vons"...')
        vons_receipts, _ = dynamo.get_receipt_metadatas_by_merchant('Vons', limit=10)

    if vons_receipts:
        # Exclude the current receipt from the search
        vons_receipts = [m for m in vons_receipts if not (m.image_id == image_id and m.receipt_id == receipt_id)]

        if not vons_receipts:
            print('No other Vons receipts found (only this one).')
            return 1

        # Use the most common place_id or one with similar address
        target_address = 'Westlake Village'
        best_match = None

        for vons_meta in vons_receipts:
            if target_address in vons_meta.address:
                best_match = vons_meta
                break

        if not best_match:
            # Use a receipt with canonical fields set (prefer one with canonical_place_id)
            for vons_meta in vons_receipts:
                if vons_meta.canonical_place_id and vons_meta.canonical_merchant_name:
                    best_match = vons_meta
                    break

        if not best_match:
            best_match = vons_receipts[0]  # Use first one if no address match

        print(f'\nUsing Vons metadata from {best_match.image_id}#{best_match.receipt_id}:')
        print(f'  merchant_name: {best_match.merchant_name}')
        print(f'  place_id: {best_match.place_id}')
        print(f'  address: {best_match.address}')

        # Update metadata with canonical fields structure (like the reference record)
        metadata.merchant_name = best_match.merchant_name
        metadata.place_id = best_match.place_id
        # Set canonical fields - use the place_id as canonical if no canonical exists
        metadata.canonical_merchant_name = best_match.canonical_merchant_name or best_match.merchant_name
        metadata.canonical_place_id = best_match.canonical_place_id or best_match.place_id
        metadata.canonical_address = best_match.canonical_address if hasattr(best_match, 'canonical_address') and best_match.canonical_address else ""
        metadata.canonical_phone_number = best_match.canonical_phone_number if hasattr(best_match, 'canonical_phone_number') and best_match.canonical_phone_number else ""
        metadata.address = metadata.address  # Keep existing address
        metadata.matched_fields = best_match.matched_fields if hasattr(best_match, 'matched_fields') else ["name", "address"]
        metadata.validated_by = best_match.validated_by if hasattr(best_match, 'validated_by') else "TEXT_SEARCH"
        metadata.validation_status = best_match.validation_status if hasattr(best_match, 'validation_status') else "MATCHED"
        metadata.timestamp = datetime.now(timezone.utc)
        metadata.reasoning = 'Manually corrected from A Tea Thing to Vons using canonical fields structure from matching receipts'

        print('\nUpdating metadata...')
        dynamo.update_receipt_metadata(metadata)

        print('\n✅ Updated metadata:')
        print(f'  merchant_name: {metadata.merchant_name}')
        print(f'  place_id: {metadata.place_id}')
        print(f'  canonical_merchant_name: {metadata.canonical_merchant_name}')
        print(f'  address: {metadata.address}')
    else:
        print('❌ No Vons receipts found in database. Cannot update.')
        return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())

