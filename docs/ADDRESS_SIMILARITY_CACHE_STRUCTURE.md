# Address Similarity Cache Structure

## Overview

The address similarity cache is stored in S3 at:
- **Bucket**: `chromadb-{stack}-shared-buckets-vectors` (same as ChromaDB bucket)
- **Key**: `address-similarity-cache/latest.json`
- **Update Frequency**: Every 5 minutes (via EventBridge schedule)
- **Content Type**: `application/json`

## Cache Generation Process

1. **Select Random Address Label**: Queries DynamoDB for 100 address labels, randomly selects one
2. **Load Original Receipt Context**: Gets full receipt details (receipt, lines, words, labels) for the selected address
3. **Generate Embedding**: Creates OpenAI embedding for the address line text
4. **Query ChromaDB**: Finds top 8 similar lines using vector similarity search
5. **Load Similar Receipts**: For each similar line, loads the full receipt context (filtered to address lines only)
6. **Build Response**: Constructs JSON structure with original and similar receipts
7. **Upload to S3**: Saves as `latest.json` in the cache bucket

## JSON Structure

```json
{
  "original": {
    "receipt": {
      // Receipt metadata object (from DynamoDB ReceiptDetails)
      // Includes: image_id, receipt_id, merchant_name, date, total, etc.
    },
    "lines": [
      // Array of Line objects that contain address labels
      // Each line includes: line_id, text, bounding_box, etc.
      // Includes ALL lines in the address range (min_line_id to max_line_id)
    ],
    "words": [
      // Array of Word objects within the address lines
      // Each word includes: word_id, text, line_id, bounding_box, etc.
      // Includes ALL words in lines that contain address labels
    ],
    "labels": [
      // Array of Label objects with label="address"
      // Each label includes: label, word_id, line_id, receipt_id, image_id, etc.
    ]
  },
  "similar": [
    // Array of similar receipt contexts (up to 8, deduplicated)
    {
      "receipt": {
        // Receipt metadata for similar receipt
      },
      "lines": [
        // Address lines from similar receipt (filtered to address range)
      ],
      "words": [
        // Address words from similar receipt (filtered to address range)
      ],
      "labels": [
        // Address labels from similar receipt
      ],
      "similarity_distance": 0.123  // ChromaDB distance metric (lower = more similar)
    }
  ],
  "cached_at": "2025-11-05T19:45:23.123456+00:00"  // ISO 8601 timestamp
}
```

## Data Details

### Original Receipt Context

The `original` object contains:

1. **Receipt**: Full receipt metadata
   - `image_id`: Source image identifier
   - `receipt_id`: Receipt identifier within the image
   - `merchant_name`: Merchant name (if available)
   - `date`: Transaction date
   - `total`: Total amount
   - Other receipt-level metadata

2. **Lines**: All lines that contain address labels
   - Includes lines from `min_line_id` to `max_line_id` (all address lines)
   - Each line has: `line_id`, `text`, `bounding_box`, `receipt_id`, `image_id`

3. **Words**: All words within the address lines
   - Filtered to words that belong to lines in the address range
   - Each word has: `word_id`, `text`, `line_id`, `bounding_box`, `receipt_id`, `image_id`

4. **Labels**: All address labels in the receipt
   - Only labels where `label == "address"`
   - Each label has: `label`, `word_id`, `line_id`, `receipt_id`, `image_id`

### Similar Receipts

The `similar` array contains up to 8 similar receipts (after deduplication):

- **Deduplication**: Same `(image_id, receipt_id)` pairs are skipped
- **Original Receipt Exclusion**: The original receipt is excluded from similar results
- **Address Context Only**: Only includes lines/words that contain address labels
- **Similarity Distance**: ChromaDB distance metric indicating how similar the lines are

Each similar receipt includes the same structure as `original`:
- Full receipt metadata
- Address lines only (filtered to address label range)
- Address words only (filtered to address label range)
- Address labels only
- Similarity distance (float, lower = more similar)

## Cache Key Details

- **Location**: `s3://chromadb-{stack}-shared-buckets-vectors/address-similarity-cache/latest.json`
- **Overwrites**: Each run overwrites the previous `latest.json` (no versioning)
- **Size**: Typically 500KB - 2MB depending on:
  - Number of similar receipts found (up to 8)
  - Length of address lines/words
  - Number of address labels per receipt

## Use Case

This cache is designed for:
- **Fast API Response**: The API Lambda can return cached data instantly (no ChromaDB/DynamoDB queries)
- **Next.js Website**: Display address similarity examples on the frontend
- **Visualization**: Show original address alongside similar addresses from different receipts

## Limitations

1. **Single Random Address**: Only one random address is cached at a time
2. **No Versioning**: Previous cache is overwritten (no history)
3. **5-Minute Refresh**: Data updates every 5 minutes (may be stale)
4. **Similar Receipt Limit**: Maximum 8 similar receipts (may be fewer if deduplicated)
5. **Address Context Only**: Only includes address-related lines/words, not full receipt

## Example Cache Entry

```json
{
  "original": {
    "receipt": {
      "image_id": "abc123",
      "receipt_id": 1,
      "merchant_name": "Walmart",
      "date": "2025-11-01",
      "total": 45.67
    },
    "lines": [
      {
        "line_id": 5,
        "text": "123 Main St",
        "receipt_id": 1,
        "image_id": "abc123"
      },
      {
        "line_id": 6,
        "text": "Springfield, IL 62701",
        "receipt_id": 1,
        "image_id": "abc123"
      }
    ],
    "words": [
      {"word_id": 50, "text": "123", "line_id": 5, ...},
      {"word_id": 51, "text": "Main", "line_id": 5, ...},
      {"word_id": 52, "text": "St", "line_id": 5, ...},
      {"word_id": 60, "text": "Springfield,", "line_id": 6, ...},
      {"word_id": 61, "text": "IL", "line_id": 6, ...},
      {"word_id": 62, "text": "62701", "line_id": 6, ...}
    ],
    "labels": [
      {"label": "address", "word_id": 50, "line_id": 5, ...},
      {"label": "address", "word_id": 51, "line_id": 5, ...},
      {"label": "address", "word_id": 52, "line_id": 5, ...},
      {"label": "address", "word_id": 60, "line_id": 6, ...},
      {"label": "address", "word_id": 61, "line_id": 6, ...},
      {"label": "address", "word_id": 62, "line_id": 6, ...}
    ]
  },
  "similar": [
    {
      "receipt": {
        "image_id": "def456",
        "receipt_id": 2,
        "merchant_name": "Target",
        "date": "2025-11-02",
        "total": 78.90
      },
      "lines": [
        {
          "line_id": 3,
          "text": "456 Oak Ave",
          "receipt_id": 2,
          "image_id": "def456"
        },
        {
          "line_id": 4,
          "text": "Springfield, IL 62702",
          "receipt_id": 2,
          "image_id": "def456"
        }
      ],
      "words": [
        {"word_id": 30, "text": "456", "line_id": 3, ...},
        {"word_id": 31, "text": "Oak", "line_id": 3, ...},
        {"word_id": 32, "text": "Ave", "line_id": 3, ...},
        {"word_id": 40, "text": "Springfield,", "line_id": 4, ...},
        {"word_id": 41, "text": "IL", "line_id": 4, ...},
        {"word_id": 42, "text": "62702", "line_id": 4, ...}
      ],
      "labels": [
        {"label": "address", "word_id": 30, "line_id": 3, ...},
        {"label": "address", "word_id": 31, "line_id": 3, ...},
        {"label": "address", "word_id": 32, "line_id": 3, ...},
        {"label": "address", "word_id": 40, "line_id": 4, ...},
        {"label": "address", "word_id": 41, "line_id": 4, ...},
        {"label": "address", "word_id": 42, "line_id": 4, ...}
      ],
      "similarity_distance": 0.234
    }
  ],
  "cached_at": "2025-11-05T19:45:23.123456+00:00"
}
```

