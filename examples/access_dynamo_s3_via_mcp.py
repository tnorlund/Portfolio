#!/usr/bin/env python3
"""
Example: Access DynamoDB and S3 Image/Receipt Records via receipt-tools MCP

This demonstrates how to use the receipt-tools MCP server to access
receipt images and data stored in DynamoDB and S3.

The receipt-tools MCP provides high-level queries for:
- Listing recent image uploads (with Image metadata from DynamoDB)
- Listing all receipts (with Receipt metadata from DynamoDB)
- Getting receipt details (words, labels, amounts)
- Searching receipts by merchant or category
- Filtering by date range
- Getting receipt images from S3
"""

# Note: In a real environment, these functions are provided by the
# receipt-tools MCP server and would be called via the MCP protocol.
# Here we show the function signatures and how to use them.


def example_1_list_recent_uploads():
    """List recently uploaded images with their metadata."""
    print("=" * 60)
    print("EXAMPLE 1: List Recent Image Uploads")
    print("=" * 60)
    print()
    print("Function: mcp__receipt-tools__list_recent_uploads(limit=5)")
    print()
    print("This queries DynamoDB Image records sorted by upload_timestamp")
    print()
    print("Expected output:")
    print("""
    {
      "uploads": [
        {
          "image_id": "550e8400-e29b-41d4-a716-446655440000",
          "upload_timestamp": "2025-12-15T14:30:00+00:00",
          "type": "PHOTO",
          "receipt_count": 3,
          "width": 1080,
          "height": 1920,
          "merchants": ["TRADER JOES", "WHOLE FOODS"]
        },
        ...
      ]
    }

    This shows:
    - Image IDs that uniquely identify uploaded receipt images
    - When the image was uploaded
    - Image type (PHOTO, SCAN, or NATIVE)
    - How many receipts were extracted from this image
    - Image dimensions
    - Store names extracted from the receipts in the image
    """)
    print()


def example_2_list_all_receipts():
    """List all receipts in the database."""
    print("=" * 60)
    print("EXAMPLE 2: List All Receipts")
    print("=" * 60)
    print()
    print("Function: mcp__receipt-tools__list_all_receipts(limit=50)")
    print()
    print("This queries DynamoDB Receipt records")
    print()
    print("Expected output:")
    print("""
    {
      "receipts": [
        {
          "receipt_id": 42,
          "image_id": "550e8400-e29b-41d4-a716-446655440000",
          "merchant_name": "TRADER JOE'S",
          "total": 123.45,
          "currency": "USD",
          "upload_time": "2025-12-15T14:30:00+00:00"
        },
        {
          "receipt_id": 43,
          "image_id": "550e8400-e29b-41d4-a716-446655440000",
          "merchant_name": "WHOLE FOODS",
          "total": 87.20,
          "currency": "USD",
          "upload_time": "2025-12-15T14:30:00+00:00"
        },
        ...
      ]
    }

    This shows:
    - Receipt IDs for each receipt in the database
    - Which image the receipt came from
    - Store name extracted from the receipt
    - Total amount of the receipt
    - When the receipt was added to the system
    """)
    print()


def example_3_get_receipt_details():
    """Get full details of a specific receipt."""
    print("=" * 60)
    print("EXAMPLE 3: Get Receipt Details with Words and Labels")
    print("=" * 60)
    print()
    print("Function: mcp__receipt-tools__get_receipt(")
    print("  image_id='550e8400-e29b-41d4-a716-446655440000',")
    print("  receipt_id=42")
    print(")")
    print()
    print("This queries DynamoDB Receipt and ReceiptWord records")
    print()
    print("Expected output:")
    print("""
    {
      "receipt_id": 42,
      "image_id": "550e8400-e29b-41d4-a716-446655440000",
      "merchant_name": "TRADER JOE'S",
      "formatted_receipt": '''
        (line 0): TRADER[MERCHANT_NAME] JOE'S[MERCHANT_NAME]
        (lines 1-2): ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]
        (lines 3-4): ALMOND[PRODUCT_NAME] MILK[PRODUCT_NAME] 3.99[LINE_TOTAL]
        (line 5): SUBTOTAL 16.98[SUBTOTAL]
        (line 6): TAX 1.50[TAX]
        (line 7): TOTAL 18.48[GRAND_TOTAL]
      ''',
      "total": 123.45,
      "currency": "USD"
    }

    This shows:
    - The receipt formatted with line numbers and labels
    - Each line shows which words belong to which label category
    - Line ranges in parentheses refer to DynamoDB ReceiptWord records
    - Labels include: MERCHANT_NAME, PRODUCT_NAME, LINE_TOTAL, TAX, GRAND_TOTAL
    """)
    print()


def example_4_get_receipt_words():
    """Get individual words from a receipt with labels and bounding boxes."""
    print("=" * 60)
    print("EXAMPLE 4: Get Receipt Words with Labels and Positions")
    print("=" * 60)
    print()
    print("Function: mcp__receipt-tools__get_receipt_words(")
    print("  image_id='550e8400-e29b-41d4-a716-446655440000',")
    print("  receipt_id=42")
    print(")")
    print()
    print("This queries DynamoDB ReceiptWord and ReceiptWordLabel records")
    print()
    print("Expected output:")
    print("""
    {
      "words": [
        {
          "line_id": 0,
          "position": 0,
          "text": "TRADER",
          "label": "MERCHANT_NAME",
          "confidence": 0.989,
          "ocr_confidence": 0.987,
          "bounding_box": {
            "top": 50,
            "left": 30,
            "bottom": 65,
            "right": 100
          }
        },
        {
          "line_id": 0,
          "position": 1,
          "text": "JOE'S",
          "label": "MERCHANT_NAME",
          "confidence": 0.95,
          "ocr_confidence": 0.988,
          "bounding_box": {
            "top": 50,
            "left": 105,
            "bottom": 65,
            "right": 160
          }
        },
        {
          "line_id": 2,
          "position": 0,
          "text": "ORGANIC",
          "label": "PRODUCT_NAME",
          "confidence": 0.92,
          "ocr_confidence": 0.991,
          "bounding_box": {
            "top": 150,
            "left": 30,
            "bottom": 165,
            "right": 110
          }
        },
        ...
      ]
    }

    This shows:
    - Individual words from the receipt
    - The label assigned to each word (PRODUCT_NAME, MERCHANT_NAME, etc.)
    - How confident the labeling is (0-1 scale)
    - OCR confidence for the text
    - Bounding box position on the image (top, left, bottom, right in pixels)

    Bounding boxes can be used to:
    - Highlight text on the image
    - Spatial analysis (what's above/below/beside other text)
    - Re-OCR specific regions
    """)
    print()


def example_5_search_receipts():
    """Search receipts by merchant name."""
    print("=" * 60)
    print("EXAMPLE 5: Search Receipts by Merchant")
    print("=" * 60)
    print()
    print("Function: mcp__receipt-tools__search_receipts(")
    print("  merchant='COSTCO'")
    print(")")
    print()
    print("This queries DynamoDB using GSI2 (merchant index)")
    print()
    print("Expected output:")
    print("""
    {
      "results": [
        {
          "receipt_id": 42,
          "merchant_name": "COSTCO",
          "total": 123.45,
          "upload_time": "2025-12-15T14:30:00+00:00"
        },
        {
          "receipt_id": 67,
          "merchant_name": "COSTCO WHOLESALE",
          "total": 289.99,
          "upload_time": "2025-12-14T10:15:00+00:00"
        },
        ...
      ],
      "count": 12
    }

    This shows:
    - All receipts matching the search term
    - Merchant name matching is case-insensitive and partial
    - Sorted by upload time (newest first)
    """)
    print()


def example_6_get_receipt_summaries():
    """Get aggregated receipt summaries with filtering."""
    print("=" * 60)
    print("EXAMPLE 6: Get Receipt Summaries with Filters")
    print("=" * 60)
    print()
    print("Function: mcp__receipt-tools__get_receipt_summaries(")
    print("  merchant_filter='Costco',")
    print("  start_date='2025-12-01',")
    print("  end_date='2025-12-31'")
    print(")")
    print()
    print("This aggregates DynamoDB Receipt records with filters")
    print()
    print("Expected output:")
    print("""
    {
      "aggregates": {
        "count": 5,
        "total_spending": 1234.56,
        "total_tax": 98.45,
        "total_tip": 25.00,
        "average_receipt": 246.91
      },
      "summaries": [
        {
          "receipt_id": 42,
          "merchant_name": "COSTCO",
          "merchant_category": "warehouse_club",
          "date": "2025-12-15",
          "grand_total": 123.45,
          "tax": 9.87,
          "tip": 5.00,
          "item_count": 12
        },
        ...
      ]
    }

    This shows:
    - Aggregated spending statistics
    - Individual receipt summaries matching the filters
    - Filters support:
      * merchant_filter: Partial match on merchant name
      * category_filter: By merchant category (grocery, restaurant, gas_station)
      * start_date, end_date: Date range filtering (ISO format YYYY-MM-DD)

    Use cases:
    - "How much did I spend at Costco in December?"
    - "What's my total spending on groceries in Q4 2025?"
    - "How much tax did I pay in 2025?"
    - "What's my average grocery bill?"
    """)
    print()


def example_7_get_receipt_images():
    """Get CDN URLs for receipt images."""
    print("=" * 60)
    print("EXAMPLE 7: Get Receipt Image URLs from S3")
    print("=" * 60)
    print()
    print("Function: mcp__receipt-tools__get_receipt_image_url(")
    print("  image_id='550e8400-e29b-41d4-a716-446655440000'")
    print(")")
    print()
    print("This fetches S3 CDN URLs from DynamoDB Image records")
    print()
    print("Expected output:")
    print("""
    {
      "image_id": "550e8400-e29b-41d4-a716-446655440000",
      "url": "https://cdn.example.com/receipts/550e8400-e29b-41d4-a716-446655440000.avif",
      "formats": {
        "avif": "https://cdn.example.com/receipts/550e8400-e29b-41d4-a716-446655440000.avif",
        "webp": "https://cdn.example.com/receipts/550e8400-e29b-41d4-a716-446655440000.webp",
        "jpeg": "https://cdn.example.com/receipts/550e8400-e29b-41d4-a716-446655440000.jpg"
      },
      "dimensions": {
        "width": 1080,
        "height": 1920
      }
    }

    This shows:
    - URLs to CDN-hosted receipt images
    - Multiple formats (AVIF, WebP, JPEG) for browser compatibility
    - Image dimensions

    The URLs are public and can be used in:
    - Web applications
    - Email
    - Reports
    - Mobile apps
    """)
    print()


def example_8_dynamodb_structure():
    """Show the underlying DynamoDB structure."""
    print("=" * 60)
    print("EXAMPLE 8: DynamoDB Table Structure")
    print("=" * 60)
    print()
    print("Table Name: ReceiptsTable-<stack> (e.g., ReceiptsTable-dev)")
    print()
    print("Primary Key:")
    print("  PK (String): Partition key - e.g., 'IMAGE#<uuid>', 'RECEIPT#<id>'")
    print("  SK (String): Sort key - e.g., 'METADATA', 'WORD#<line>#<pos>'")
    print()
    print("Example records:")
    print()
    print("1. Image Record:")
    print("""
    {
      "PK": "IMAGE#550e8400-e29b-41d4-a716-446655440000",
      "SK": "METADATA",
      "TYPE": "IMAGE",
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "upload_timestamp": "2025-12-15T14:30:00+00:00",
      "type": "PHOTO",
      "width": 1080,
      "height": 1920,
      "receipt_ids": [42, 43],
      "s3_locations": {
        "raw": "s3://bucket/raw-receipts/550e8400-e29b-41d4-a716-446655440000.jpg",
        "processed": "s3://bucket/receipts/550e8400-e29b-41d4-a716-446655440000.jpg"
      }
    }
    """)
    print()
    print("2. Receipt Record:")
    print("""
    {
      "PK": "RECEIPT#42",
      "SK": "METADATA",
      "TYPE": "RECEIPT",
      "id": 42,
      "image_id": "550e8400-e29b-41d4-a716-446655440000",
      "merchant_name": "TRADER JOE'S",
      "total": 123.45,
      "currency": "USD",
      "upload_time": "2025-12-15T14:30:00+00:00"
    }
    """)
    print()
    print("3. ReceiptWord Record:")
    print("""
    {
      "PK": "RECEIPT#42",
      "SK": "WORD#0#0",
      "TYPE": "RECEIPT_WORD",
      "receipt_id": 42,
      "line_id": 0,
      "position": 0,
      "text": "TRADER",
      "confidence": 0.987,
      "bounding_box": {"top": 50, "left": 30, "bottom": 65, "right": 100}
    }
    """)
    print()
    print("4. ReceiptWordLabel Record:")
    print("""
    {
      "PK": "RECEIPT#42",
      "SK": "LABEL#0#0",
      "TYPE": "RECEIPT_WORD_LABEL",
      "receipt_id": 42,
      "word_id": "0#0",
      "label": "MERCHANT_NAME",
      "confidence": 0.95,
      "label_source": "MODEL"
    }
    """)
    print()
    print("Global Secondary Indexes:")
    print("  - GSI1 (GSI1PK/GSI1SK): Type and relationship queries")
    print("  - GSI2 (GSI2PK/GSI2SK): Merchant and category queries")
    print("  - GSI3 (GSI3PK/GSI3SK): Temporal queries (by date)")
    print("  - GSI4 (GSI4PK/GSI4SK): Batch operations")
    print("  - GSITYPE (TYPE): Query all items of a type")
    print()


def example_9_s3_structure():
    """Show the S3 bucket structure."""
    print("=" * 60)
    print("EXAMPLE 9: S3 Bucket Structure")
    print("=" * 60)
    print()
    print("Bucket: portfolio-receipts-<stack> (e.g., portfolio-receipts-dev)")
    print()
    print("Prefixes:")
    print()
    print("1. raw-receipts/")
    print("   - Original uploaded images")
    print("   - s3://bucket/raw-receipts/<image_id>.jpg")
    print("   - Formats: JPEG, PNG, WebP")
    print()
    print("2. receipts/")
    print("   - Processed receipt images for web")
    print("   - s3://bucket/receipts/<image_id>.avif")
    print("   - s3://bucket/receipts/<image_id>.webp")
    print("   - s3://bucket/receipts/<image_id>.jpg")
    print("   - CDN-optimized formats")
    print()
    print("3. ocr_results/")
    print("   - OCR output from Vision API")
    print("   - s3://bucket/ocr_results/<image_id>.json")
    print("   - Contains text and bounding boxes")
    print()
    print("4. coreml/")
    print("   - Trained LayoutLM CoreML models")
    print("   - s3://bucket/coreml/<job_name>/")
    print("   - ├── LayoutLM.mlpackage/")
    print("   - ├── vocab.txt")
    print("   - ├── config.json")
    print("   - └── label_map.json")
    print()


def example_10_use_cases():
    """Show common use cases."""
    print("=" * 60)
    print("EXAMPLE 10: Common Use Cases")
    print("=" * 60)
    print()
    print("1. Get recent receipts for review:")
    print("   mcp__receipt-tools__list_recent_uploads(limit=10)")
    print()
    print("2. Find all receipts from a specific store:")
    print("   mcp__receipt-tools__search_receipts(merchant='COSTCO')")
    print()
    print("3. Calculate spending by category:")
    print("   mcp__receipt-tools__get_receipt_summaries(")
    print("     category_filter='grocery'")
    print("   )")
    print()
    print("4. Get year-to-date spending:")
    print("   mcp__receipt-tools__get_receipt_summaries(")
    print("     start_date='2025-01-01',")
    print("     end_date='2025-12-31'")
    print("   )")
    print()
    print("5. Extract words from a specific receipt:")
    print("   mcp__receipt-tools__get_receipt_words(")
    print("     image_id='<uuid>',")
    print("     receipt_id=42")
    print("   )")
    print()
    print("6. View a receipt image:")
    print("   url = mcp__receipt-tools__get_receipt_image_url(")
    print("     image_id='<uuid>'")
    print("   )")
    print("   # Open url in browser or download")
    print()


if __name__ == "__main__":
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "  DynamoDB & S3 Access via receipt-tools MCP".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    example_1_list_recent_uploads()
    example_2_list_all_receipts()
    example_3_get_receipt_details()
    example_4_get_receipt_words()
    example_5_search_receipts()
    example_6_get_receipt_summaries()
    example_7_get_receipt_images()
    example_8_dynamodb_structure()
    example_9_s3_structure()
    example_10_use_cases()

    print("\n")
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print()
    print("DynamoDB stores:")
    print("  ✓ Image metadata (upload_timestamp, dimensions, receipt_ids)")
    print("  ✓ Receipt data (merchant_name, total, items)")
    print("  ✓ Word details (text, confidence, bounding_box)")
    print("  ✓ Labels (label type, confidence, source)")
    print()
    print("S3 stores:")
    print("  ✓ Raw receipt images (unprocessed)")
    print("  ✓ Processed images (AVIF, WebP, JPEG)")
    print("  ✓ OCR results (text + bounding boxes)")
    print("  ✓ CoreML models (for on-device inference)")
    print()
    print("Access via receipt-tools MCP for:")
    print("  ✓ High-level queries (no AWS setup needed)")
    print("  ✓ Automatic filtering and aggregation")
    print("  ✓ Built-in CDN URL selection")
    print("  ✓ Type-safe responses")
    print()
