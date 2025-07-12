# Receipt Entity Structure in PROD DynamoDB Table

## Confirmed Structure

Based on querying the production DynamoDB table (`ReceiptsTable-d7ff76a`), Receipt entities have the following structure:

### Primary Key Structure
- **PK (Partition Key)**: `IMAGE#{image_id}`
  - Example: `IMAGE#2c9b770c-9407-4cdc-b0eb-3a5b27f0af15`
  - Receipt entities are stored under their parent Image's partition
  - **NOT** `RECEIPT#{receipt_id}` as might be expected

- **SK (Sort Key)**: `RECEIPT#{receipt_number}`
  - Example: `RECEIPT#00001`
  - Receipt number is a 5-digit zero-padded number
  - Multiple receipts per image use sequential numbers: `RECEIPT#00001`, `RECEIPT#00002`, etc.

- **TYPE**: `RECEIPT`
  - Used for GSI queries and entity type identification

### Key Findings

1. **Receipt entities are children of Image entities**, not standalone entities
2. **No Receipt entities exist with PK starting with `RECEIPT#`** - all use `IMAGE#` prefix
3. **Hierarchical structure** under each Receipt:
   - `RECEIPT#00001` - The main Receipt entity
   - `RECEIPT#00001#LINE#00001` - Receipt lines (TYPE: RECEIPT_LINE)
   - `RECEIPT#00001#LINE#00001#WORD#00001` - Receipt words (TYPE: RECEIPT_WORD)
   - `RECEIPT#00001#LINE#00001#WORD#00001#LETTER#00001` - Receipt letters (TYPE: RECEIPT_LETTER)

### Query Patterns

To query Receipt entities for a specific image:
```python
# Query all receipts for an image
response = table.query(
    KeyConditionExpression=Key('PK').eq(f'IMAGE#{image_id}') & Key('SK').begins_with('RECEIPT#')
)

# Query a specific receipt
response = table.get_item(
    Key={
        'PK': f'IMAGE#{image_id}',
        'SK': f'RECEIPT#{receipt_number}'
    }
)

# Query all Receipt entities across all images (using GSI)
response = table.query(
    IndexName='GSITYPE',
    KeyConditionExpression=Key('TYPE').eq('RECEIPT')
)
```

### Important Notes

1. This structure means that to access a Receipt, you must know its parent Image ID
2. Receipt entities cannot be queried directly by receipt ID alone without using a GSI
3. The hierarchical structure allows efficient querying of all data related to an image in a single partition

## Verification Details

- **Date Verified**: 2025-07-12
- **Table Name**: ReceiptsTable-d7ff76a
- **Stack**: tnorlund/portfolio/prod
- **Sample Size**: Queried multiple Receipt entities and verified no alternative patterns exist