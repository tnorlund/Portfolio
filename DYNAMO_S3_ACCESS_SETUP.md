# DynamoDB & S3 Access Setup - Summary

## ✅ Completed Tasks

I've successfully created comprehensive documentation and examples for accessing the DynamoDB table and S3 buckets containing image and receipt records.

### 1. Documentation Created

**Main Documentation:** `docs/DYNAMO_S3_ACCESS.md`
- Complete guide to DynamoDB `ReceiptsTable` structure
- Entity types: Image, Receipt, ReceiptWord, ReceiptWordLabel
- S3 bucket structure for images, OCR results, and CoreML models
- Three methods to access data (MCP, boto3, receipt_dynamo)
- Query examples and best practices
- Table name mapping for different stacks

### 2. Example Scripts Created

**1. `examples/access_dynamo_s3_via_mcp.py`** (Executable documentation)
- 10 detailed examples showing how to use receipt-tools MCP
- Shows function signatures and expected outputs
- Use cases and common queries
- DynamoDB schema and S3 structure reference

**2. `examples/test_dynamodb_s3_access.py`** (Test/Verification script)
- Tests DynamoDB connectivity and table structure
- Tests S3 bucket access
- Queries sample items from ReceiptsTable
- Lists prefixes in S3 buckets
- Verifies receipt_dynamo package availability

## 🏗 Architecture Overview

```
┌─────────────────────────────────────────┐
│        Image Upload Pipeline            │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│   S3: raw-receipts/ (Original Images)   │
└─────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│   DynamoDB: Image Records (Metadata)    │
│   - PK: IMAGE#<uuid>                    │
│   - upload_timestamp, type, dimensions  │
│   - receipt_ids, s3_locations, cdn_vars │
└─────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│   Apple Vision OCR + S3: ocr_results/    │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│  DynamoDB: Receipt Records (Metadata)    │
│  - PK: RECEIPT#<id>                      │
│  - merchant_name, total, items           │
│  - PK: RECEIPT#<id>, SK: WORD#<line>#<pos> │
│  - PK: RECEIPT#<id>, SK: LABEL#<word_id>   │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│  LayoutLM Classification (AWS SageMaker) │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│   S3: receipts/ (CDN Images - AVIF/WebP) │
│   S3: coreml/ (CoreML Models)            │
└──────────────────────────────────────────┘
```

## 📊 DynamoDB Schema

### Table: `ReceiptsTable-<stack>`

**Primary Key:**
- `PK` (String): Partition key - `IMAGE#<uuid>` | `RECEIPT#<id>` | etc.
- `SK` (String): Sort key - `METADATA` | `WORD#<line>#<pos>` | `LABEL#<word_id>`

**Entity Types:**

#### 1. Image Records
```python
PK: IMAGE#<uuid>
SK: METADATA

{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "upload_timestamp": "2025-12-15T14:30:00+00:00",
  "type": "PHOTO",              # SCAN | PHOTO | NATIVE
  "width": 1080,
  "height": 1920,
  "receipt_ids": [1, 2, 3],
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

#### 2. Receipt Records
```python
PK: RECEIPT#<id>
SK: METADATA

{
  "id": 42,
  "image_id": "550e8400-e29b-41d4-a716-446655440000",
  "merchant_name": "TRADER JOE'S",
  "total": 123.45,
  "currency": "USD",
  "upload_time": "2025-12-15T14:30:00+00:00",
  "receipt_lines": [
    {"line_id": 1, "text": "ORGANIC COFFEE", "amount": 12.99}
  ],
  "num_items": 5,
  "num_words": 247,
  "ocr_confidence_avg": 0.94
}
```

#### 3. ReceiptWord Records
```python
PK: RECEIPT#<receipt_id>
SK: WORD#<line_id>#<position>

{
  "receipt_id": 42,
  "line_id": 1,
  "position": 0,
  "text": "ORGANIC",
  "confidence": 0.987,
  "bounding_box": {
    "top": 150,
    "left": 50,
    "bottom": 165,
    "right": 120
  }
}
```

#### 4. ReceiptWordLabel Records
```python
PK: RECEIPT#<receipt_id>
SK: LABEL#<word_id>

{
  "receipt_id": 42,
  "word_id": "1#0",
  "label": "PRODUCT_NAME",       # MERCHANT_NAME | DATE | PRODUCT_NAME | etc.
  "confidence": 0.95,
  "label_source": "MODEL"        # HUMAN | MODEL | AUTO
}
```

**Global Secondary Indexes:**
- **GSI1**: GSI1PK/GSI1SK - Type and relationship queries
- **GSI2**: GSI2PK/GSI2SK - Merchant and category queries
- **GSI3**: GSI3PK/GSI3SK - Temporal queries (by date)
- **GSI4**: GSI4PK/GSI4SK - Batch operations
- **GSITYPE**: TYPE/- - Query all items of a type

## 📦 S3 Bucket Structure

**Bucket:** `portfolio-receipts-<stack>` (e.g., `portfolio-receipts-dev`)

```
s3://bucket/
├── raw-receipts/
│   ├── <image_id>.jpg          # Original uploaded images
│   ├── <image_id>.png
│   └── <image_id>.webp
├── receipts/                   # CDN-optimized variants
│   ├── <image_id>.avif         # Modern format (~40% smaller)
│   ├── <image_id>.webp         # Fallback (~20% smaller than JPEG)
│   └── <image_id>.jpg          # Legacy format
├── ocr_results/
│   └── <image_id>.json         # Apple Vision OCR output
│                                # Contains text + bounding boxes
└── coreml/
    └── <job_name>/
        ├── LayoutLM.mlpackage/  # Compiled CoreML model
        ├── vocab.txt            # BERT tokenizer
        ├── config.json          # Configuration
        └── label_map.json       # Label ID → name mapping
```

## 🔧 How to Access the Data

### Method 1: receipt-tools MCP (Recommended ⭐)

**No AWS setup required. Type-safe queries with automatic CDN handling.**

```python
# List recent uploads
result = mcp__receipt-tools__list_recent_uploads(limit=10)

# List all receipts
result = mcp__receipt-tools__list_all_receipts(limit=50)

# Get receipt details
result = mcp__receipt-tools__get_receipt(
  image_id="550e8400-e29b-41d4-a716-446655440000",
  receipt_id=42
)

# Get receipt words with labels
words = mcp__receipt-tools__get_receipt_words(
  image_id="550e8400-e29b-41d4-a716-446655440000",
  receipt_id=42
)

# Search receipts
result = mcp__receipt-tools__search_receipts(merchant="COSTCO")

# Get aggregated summaries
summaries = mcp__receipt-tools__get_receipt_summaries(
  merchant_filter="Costco",
  category_filter="grocery",
  start_date="2025-12-01",
  end_date="2025-12-31"
)

# Get receipt image URL
url = mcp__receipt-tools__get_receipt_image_url(
  image_id="550e8400-e29b-41d4-a716-446655440000"
)
```

**Advantages:**
- ✅ No AWS credential setup
- ✅ Built-in CDN variant selection
- ✅ Automatic filtering and aggregation
- ✅ Type-safe responses
- ✅ High-level abstractions

### Method 2: AWS SDK (boto3)

**Direct DynamoDB and S3 access with full control.**

```python
import boto3

# Create clients
dynamodb = boto3.client('dynamodb', region_name='us-east-1')
s3 = boto3.client('s3', region_name='us-east-1')

# Query DynamoDB
response = dynamodb.get_item(
  TableName='ReceiptsTable-dev',
  Key={
    'PK': {'S': 'RECEIPT#42'},
    'SK': {'S': 'METADATA'}
  }
)

# Query S3
response = s3.download_file(
  Bucket='portfolio-receipts-dev',
  Key='receipts/<image_id>.jpg',
  Filename='./receipt.jpg'
)
```

**Requirements:**
- AWS credentials configured (IAM role, access keys, etc.)
- boto3 installed (`pip install boto3`)

### Method 3: receipt_dynamo Package

**Type-safe Python entities for DynamoDB operations.**

```python
from receipt_dynamo import Image, Receipt, ReceiptWord, ReceiptWordLabel
from receipt_dynamo.data.dynamo_client import DynamoClient

# Create client
client = DynamoClient()

# Fetch and use entities
image = client.get_image(id='550e8400-e29b-41d4-a716-446655440000')
receipt = client.get_receipt(id=42)
words = client.get_receipt_words(receipt_id=42)
```

## 🚀 Installation & Setup

### For receipt-tools MCP (No setup needed)
Just use the MCP functions directly - they're already configured in your Claude Code environment.

### For boto3 SDK

```bash
# Create virtual environment
python3 -m venv ~/.venv

# Activate it
source ~/.venv/bin/activate

# Install boto3
pip install boto3

# Run your script
python3 examples/test_dynamodb_s3_access.py
```

### AWS Credentials
Set up AWS credentials for boto3:

```bash
# Option 1: AWS CLI
aws configure

# Option 2: Environment variables
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"

# Option 3: IAM Role (AWS Lambda, EC2, etc.)
# Automatically picks up credentials from instance profile
```

## 📝 Common Queries

### Get all receipts from a specific merchant
```python
result = mcp__receipt-tools__search_receipts(merchant="COSTCO")
```

### Calculate spending by category
```python
summaries = mcp__receipt-tools__get_receipt_summaries(
  category_filter="grocery_store"
)
print(f"Total spent: ${summaries['aggregates']['total_spending']:.2f}")
```

### Get year-to-date spending
```python
summaries = mcp__receipt-tools__get_receipt_summaries(
  start_date="2025-01-01",
  end_date="2025-12-31"
)
```

### Extract words from a receipt for training
```python
words = mcp__receipt-tools__get_receipt_words(
  image_id="<uuid>",
  receipt_id=42
)
for word in words:
  print(f"{word['text']}: {word['label']} ({word['confidence']})")
```

### Download receipt image
```python
url = mcp__receipt-tools__get_receipt_image_url(
  image_id="550e8400-e29b-41d4-a716-446655440000"
)
# Use in web app, download, or share
```

## 📚 Reference Files

| File | Purpose |
|------|---------|
| `docs/DYNAMO_S3_ACCESS.md` | Complete technical documentation |
| `examples/access_dynamo_s3_via_mcp.py` | 10 detailed usage examples |
| `examples/test_dynamodb_s3_access.py` | Connectivity test script |
| `infra/dynamo_db.py` | Pulumi infrastructure code |
| `infra/s3_website.py` | S3 bucket configuration |
| `receipt_dynamo/__init__.py` | Entity definitions |

## 🎯 Next Steps

1. **Try the examples:**
   ```bash
   # Read the MCP examples
   python3 examples/access_dynamo_s3_via_mcp.py
   
   # Run connectivity test (requires AWS credentials)
   python3 examples/test_dynamodb_s3_access.py
   ```

2. **Use in your code:**
   - Import receipt-tools MCP functions
   - Query receipt data
   - Build applications on top

3. **Extend as needed:**
   - Add more queries in your scripts
   - Filter by specific criteria
   - Aggregate data for analytics

## 💡 Key Takeaways

✅ **DynamoDB** stores all receipt metadata (images, words, labels)
✅ **S3** stores actual image files and OCR results
✅ **receipt-tools MCP** provides high-level queries (recommended)
✅ **boto3 SDK** provides direct AWS access when needed
✅ **receipt_dynamo** provides type-safe Python entities

The data pipeline: Upload → OCR → DynamoDB + S3 → Query via MCP/boto3/entities

---

**Branch:** `claude/dynamo-s3-image-receipt-access-c9xsb0`
**Documentation Location:** `docs/DYNAMO_S3_ACCESS.md`
**Examples Location:** `examples/`
