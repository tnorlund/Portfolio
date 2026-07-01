# DynamoDB & S3 Access for Image and Receipt Records

## Overview

Your Portfolio project stores receipt data in two places:

1. **DynamoDB** - `ReceiptsTable` with receipt metadata, words, labels, and images
2. **S3** - Multiple buckets with images and OCR results

You can access this data through:
- **receipt-tools MCP Server** (recommended) - Higher-level queries
- **AWS SDKs** (boto3) - Direct DynamoDB/S3 access  
- **Receipt entity classes** - Type-safe Python objects

---

## 1. DynamoDB Table Structure

### Table: `ReceiptsTable`

**Primary Key:**
- `PK` (String, Partition Key): `IMAGE#<uuid>` or `RECEIPT#<id>` or `WORD#...` etc.
- `SK` (String, Sort Key): `METADATA` or `WORD#<line_id>#<position>` or `LABEL#<word_id>`

**Global Secondary Indexes:**
- **GSI1** (GSI1PK / GSI1SK): Type and relationship queries
- **GSI2** (GSI2PK / GSI2SK): Merchant and category queries
- **GSI3** (GSI3PK / GSI3SK): Temporal queries (by date)
- **GSI4** (GSI4PK / GSI4SK): Batch operations
- **GSITYPE** (TYPE / -): Query all items of a type

**Configuration:**
- Billing: `PAY_PER_REQUEST` (serverless, no capacity planning)
- TTL: Enabled on `time_to_live` attribute
- Point-in-Time Recovery: Enabled
- Streams: Enabled with `NEW_AND_OLD_IMAGES` view type

---

## 2. Entity Types in DynamoDB

### Image Records
```
PK: IMAGE#<uuid>
SK: METADATA
TYPE: IMAGE

Attributes:
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "upload_timestamp": "2025-12-15T14:30:00+00:00",
  "type": "PHOTO",                    # SCAN, PHOTO, or NATIVE
  "width": 1080,
  "height": 1920,
  "receipt_ids": [1, 2, 3],           # Receipts from this image
  "original_filename": "receipt.jpg",
  "mime_type": "image/jpeg",
  "s3_locations": {
    "raw": "s3://bucket/raw-receipts/<id>.jpg",
    "processed": "s3://bucket/receipts/<id>.jpg"
  },
  "cdn_variants": {
    "avif": "s3://bucket/receipts/<id>.avif",
    "webp": "s3://bucket/receipts/<id>.webp",
    "jpeg": "s3://bucket/receipts/<id>.jpg"
  }
}
```

**Python Entity:** `receipt_dynamo.Image`

---

### Receipt Records
```
PK: RECEIPT#<id>
SK: METADATA
TYPE: RECEIPT

Attributes:
{
  "id": 42,
  "image_id": "550e8400-e29b-41d4-a716-446655440000",
  "merchant_name": "TRADER JOE'S",
  "total": 123.45,
  "currency": "USD",
  "upload_time": "2025-12-15T14:30:00+00:00",
  "receipt_lines": [
    {
      "line_id": 1,
      "text": "ORGANIC COFFEE",
      "amount": 12.99,
      "is_item": true
    },
    ...
  ],
  "num_items": 5,
  "num_words": 247,
  "ocr_confidence_avg": 0.94
}
```

**Python Entity:** `receipt_dynamo.Receipt`

---

### ReceiptWord Records
```
PK: RECEIPT#<receipt_id>
SK: WORD#<line_id>#<position>
TYPE: RECEIPT_WORD

Attributes:
{
  "image_id": "550e8400-e29b-41d4-a716-446655440000",
  "receipt_id": 42,
  "line_id": 1,
  "position": 0,
  "text": "ORGANIC",
  "confidence": 0.987,          # OCR confidence (0-1)
  "bounding_box": {
    "top": 150,
    "left": 50,
    "bottom": 165,
    "right": 120
  }
}
```

**Python Entity:** `receipt_dynamo.ReceiptWord`

---

### ReceiptWordLabel Records
```
PK: RECEIPT#<receipt_id>
SK: LABEL#<word_id>
TYPE: RECEIPT_WORD_LABEL

Attributes:
{
  "image_id": "550e8400-e29b-41d4-a716-446655440000",
  "receipt_id": 42,
  "word_id": "1#0",
  "label": "PRODUCT_NAME",     # Classification label
  "confidence": 0.95,          # Labeler confidence
  "label_source": "MODEL",     # HUMAN, MODEL, or AUTO
  "updated_at": "2025-12-15T15:30:00+00:00"
}

Valid Labels:
  - MERCHANT_NAME: Store name
  - DATE: Purchase date
  - ADDRESS: Store address
  - PHONE_NUMBER: Contact number
  - PRODUCT_NAME: Item description
  - PRICE: Individual item price
  - LINE_TOTAL: Line item total
  - SUBTOTAL: Subtotal before tax
  - TAX: Tax amount
  - GRAND_TOTAL: Final receipt total
  - QUANTITY: Item quantity
  - DISCOUNT: Discount applied
```

**Python Entity:** `receipt_dynamo.ReceiptWordLabel`

---

## 3. S3 Bucket Structure

### Buckets and Prefixes

**raw-receipts/**
```
raw-receipts/<image_id>.jpg
raw-receipts/<image_id>.png
```
Original uploaded images (unprocessed)

**receipts/**
```
receipts/<image_id>.avif         # Modern web format
receipts/<image_id>.webp         # Fallback web format
receipts/<image_id>.jpg          # Legacy format
```
Processed receipt images for web delivery (CDN variants)

**ocr_results/**
```
ocr_results/<image_id>.json      # Apple Vision OCR output
```
OCR results with text and bounding boxes

**coreml/**
```
coreml/<job_name>/
  ├── LayoutLM.mlpackage/        # Compiled CoreML model
  ├── vocab.txt                   # BERT tokenizer vocabulary
  ├── config.json                 # Configuration
  └── label_map.json              # Label ID → name mapping
```
Trained CoreML models for on-device inference

---

## 4. Access Methods

### Method 1: receipt-tools MCP Server (Recommended)

High-level queries with built-in access to DynamoDB and S3:

```python
# List recent uploads
mcp__receipt-tools__list_recent_uploads(limit=10)
# Returns: Image records with timestamps, types, receipts, merchant names

# List all receipts
mcp__receipt-tools__list_all_receipts(limit=50)
# Returns: Receipt records with merchants and totals

# Get receipt details
mcp__receipt-tools__get_receipt(image_id="<uuid>", receipt_id=42)
# Returns: Formatted receipt with all words and labels

# Get receipt words
mcp__receipt-tools__get_receipt_words(image_id="<uuid>", receipt_id=42)
# Returns: Individual words with labels and bounding boxes

# Search receipts
mcp__receipt-tools__search_receipts(merchant="TRADER JOES")
# Returns: Matching receipts

# Get receipt summaries
mcp__receipt-tools__get_receipt_summaries(
  merchant_filter="Costco",
  category_filter="grocery",
  start_date="2025-12-01",
  end_date="2025-12-31"
)
# Returns: Aggregated spending data

# Get receipt images
mcp__receipt-tools__get_receipt_image_url(image_id="<uuid>")
# Returns: Public S3 URL to receipt image (AVIF/WebP/JPEG)
```

**Advantages:**
- ✅ No AWS credential setup needed
- ✅ Type-safe queries
- ✅ Automatic S3 CDN variant selection
- ✅ Built-in filtering and aggregation
- ✅ Higher-level abstractions

---

### Method 2: AWS SDK (boto3) - Direct Access

For direct DynamoDB and S3 access:

```python
import boto3

# DynamoDB Client
dynamodb = boto3.client('dynamodb', region_name='us-east-1')

# Query Image Records
response = dynamodb.get_item(
  TableName='ReceiptsTable-<stack>',
  Key={
    'PK': {'S': 'IMAGE#550e8400-e29b-41d4-a716-446655440000'},
    'SK': {'S': 'METADATA'}
  }
)
image = response['Item']

# Query Receipt Records
response = dynamodb.get_item(
  TableName='ReceiptsTable-<stack>',
  Key={
    'PK': {'S': 'RECEIPT#42'},
    'SK': {'S': 'METADATA'}
  }
)
receipt = response['Item']

# Query Receipt Words
response = dynamodb.query(
  TableName='ReceiptsTable-<stack>',
  KeyConditionExpression='PK = :pk',
  ExpressionAttributeValues={
    ':pk': {'S': 'RECEIPT#42'}
  }
)
words = response['Items']

# S3 Client
s3 = boto3.client('s3', region_name='us-east-1')

# Download receipt image
s3.download_file(
  Bucket='portfolio-receipts-<stack>',
  Key='receipts/<image_id>.jpg',
  Filename='/path/to/local/image.jpg'
)

# List OCR results
response = s3.list_objects_v2(
  Bucket='portfolio-receipts-<stack>',
  Prefix='ocr_results/'
)
```

**Advantages:**
- ✅ Full DynamoDB query capabilities
- ✅ Direct S3 access
- ✅ Low-level control

**Disadvantages:**
- ❌ Need AWS credentials
- ❌ More boilerplate code
- ❌ Manual type conversion

---

### Method 3: receipt_dynamo Python Package

Type-safe entity classes:

```python
from receipt_dynamo import (
  Image, 
  Receipt, 
  ReceiptWord, 
  ReceiptWordLabel,
  DynamoClient
)

# Create DynamoDB client (auto-loads from AWS credentials)
client = DynamoClient()

# Fetch Image record
image = client.get_image(id='550e8400-e29b-41d4-a716-446655440000')
print(image.merchant_names)  # From receipts
print(image.cdn_variants)    # AVIF/WebP/JPEG URLs

# Fetch Receipt record
receipt = client.get_receipt(id=42)
print(receipt.merchant_name)
print(receipt.total)
print(receipt.receipt_lines)

# Fetch Receipt Words
words = client.get_receipt_words(receipt_id=42)
for word in words:
  print(f"{word.text} @ {word.bounding_box}")

# Fetch Labels
labels = client.get_receipt_word_labels(receipt_id=42)
for label in labels:
  print(f"{label.word_id} → {label.label} ({label.confidence})")
```

**Advantages:**
- ✅ Type-safe Python objects
- ✅ Automatic DynamoDB serialization/deserialization
- ✅ Convenient attribute access
- ✅ Built-in validation

**Disadvantages:**
- ❌ Need boto3 installed
- ❌ More verbose than MCP

---

## 5. Query Examples

### Get All Recent Receipts with Metadata

**Using MCP:**
```python
result = mcp__receipt-tools__list_recent_uploads(limit=10)
# Returns:
# {
#   "uploads": [
#     {
#       "image_id": "...",
#       "upload_timestamp": "2025-12-15T14:30:00+00:00",
#       "type": "PHOTO",
#       "receipt_count": 3,
#       "width": 1080,
#       "height": 1920,
#       "merchants": ["TRADER JOES", "WHOLE FOODS"]
#     },
#     ...
#   ]
# }
```

**Using boto3 + DynamoDB:**
```python
response = dynamodb.query(
  TableName='ReceiptsTable-dev',
  IndexName='GSI3',  # Temporal index
  KeyConditionExpression='GSI3PK = :type',
  ExpressionAttributeValues={':type': {'S': 'IMAGE'}},
  ScanIndexForward=False,  # Newest first
  Limit=10
)
```

---

### Filter Receipts by Merchant and Date

**Using MCP:**
```python
result = mcp__receipt-tools__get_receipt_summaries(
  merchant_filter="COSTCO",
  start_date="2025-12-01",
  end_date="2025-12-31"
)
# Returns aggregated spending + individual receipts
```

**Using boto3 + DynamoDB:**
```python
response = dynamodb.query(
  TableName='ReceiptsTable-dev',
  IndexName='GSI2',  # Merchant index
  KeyConditionExpression='GSI2PK = :merchant',
  ExpressionAttributeValues={':merchant': {'S': 'COSTCO'}},
  FilterExpression='#dt BETWEEN :start AND :end',
  ExpressionAttributeNames={'#dt': 'upload_time'},
  ExpressionAttributeValues={
    ':start': {'S': '2025-12-01'},
    ':end': {'S': '2025-12-31'}
  }
)
```

---

### Get Receipt Words and Labels

**Using MCP:**
```python
words = mcp__receipt-tools__get_receipt_words(
  image_id='550e8400-e29b-41d4-a716-446655440000',
  receipt_id=42
)
# Returns: [
#   {
#     "text": "ORGANIC",
#     "label": "PRODUCT_NAME",
#     "confidence": 0.987,
#     "bounding_box": {...}
#   },
#   ...
# ]
```

**Using boto3 + DynamoDB:**
```python
response = dynamodb.query(
  TableName='ReceiptsTable-dev',
  KeyConditionExpression='PK = :pk',
  ExpressionAttributeValues={':pk': {'S': 'RECEIPT#42'}},
)
words = [item for item in response['Items'] if 'WORD#' in item['SK']['S']]
labels = [item for item in response['Items'] if 'LABEL#' in item['SK']['S']]
```

---

### Download Receipt Image from S3

**Using MCP:**
```python
url = mcp__receipt-tools__get_receipt_image_url(
  image_id='550e8400-e29b-41d4-a716-446655440000'
)
# Returns: https://cdn.example.com/receipts/<id>.avif
```

**Using boto3 + S3:**
```python
# List all CDN variants
response = s3.list_objects_v2(
  Bucket='portfolio-receipts-dev',
  Prefix='receipts/<image_id>.'
)
# Download preferred variant
s3.download_file(
  Bucket='portfolio-receipts-dev',
  Key='receipts/<image_id>.avif',
  Filename='./receipt.avif'
)
```

---

## 6. Table Name Mapping

The actual DynamoDB table name includes the stack suffix:

- **Development:** `ReceiptsTable-dev`
- **Staging:** `ReceiptsTable-staging`
- **Production:** `ReceiptsTable-prod`

**Pulumi Output:** Run `pulumi stack output` to see the exact table name for your stack.

---

## 7. Best Practices

### Do's ✅

- Use **receipt-tools MCP** for high-level queries (recommended)
- Use **GSI queries** instead of table scans
- Filter by `TYPE` attribute to query specific entity types
- Use date indexes (GSI3) for temporal queries
- Batch operations for multiple items
- Cache frequently accessed data

### Don'ts ❌

- Don't scan the entire table (use Query + GSI instead)
- Don't store large files in DynamoDB (use S3 + reference)
- Don't assume table names are static (use stack outputs)
- Don't hardcode AWS credentials (use IAM roles)
- Don't store sensitive data in DynamoDB (use encryption)

---

## 8. Infrastructure Details

**From:** `/home/user/Portfolio/infra/dynamo_db.py`

Table Configuration:
```python
dynamodb_table = aws.dynamodb.Table(
    "ReceiptsTable",
    hash_key="PK",
    range_key="SK",
    billing_mode="PAY_PER_REQUEST",
    ttl=aws.dynamodb.TableTtlArgs(
        attribute_name="time_to_live",
        enabled=True,
    ),
    stream_enabled=True,
    stream_view_type="NEW_AND_OLD_IMAGES",
    point_in_time_recovery=aws.dynamodb.TablePointInTimeRecoveryArgs(
        enabled=True
    ),
    global_secondary_indexes=[
        # GSI1, GSI2, GSI3, GSI4, GSITYPE
    ],
)
```

---

## Summary

You can access image and receipt records in three ways:

1. **receipt-tools MCP** (✅ Recommended) - High-level, no setup
2. **boto3 SDK** - Direct AWS access, more control
3. **receipt_dynamo entities** - Type-safe Python objects

The data flows like this:

```
Upload → S3 (raw-receipts/) 
  ↓
OCR → S3 (ocr_results/) + DynamoDB (Image/Receipt/ReceiptWord records)
  ↓
LayoutLM Classification → DynamoDB (ReceiptWordLabel records)
  ↓
Process → S3 (receipts/ CDN variants)
  ↓
Query ← receipt-tools MCP / boto3 SDK / receipt_dynamo entities
```
