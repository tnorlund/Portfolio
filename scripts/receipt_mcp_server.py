#!/usr/bin/env python3
"""
MCP Server for Receipt Tools.

Exposes receipt search and retrieval tools via Model Context Protocol,
allowing Claude to directly query the receipt database.

Usage:
    # Start the server (stdio mode for Claude Code)
    python scripts/receipt_mcp_server.py

    # Or via uv (recommended)
    uv run scripts/receipt_mcp_server.py

Environment:
    Requires Pulumi config for DynamoDB and ChromaDB credentials.
    Set PORTFOLIO_ENV=dev or PORTFOLIO_ENV=prod
"""

import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from typing import Any, Optional

# Add paths for local packages
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_agent"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_chroma"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global clients (initialized on startup)
_dynamo_client = None
_chroma_client = None
_embed_fn = None


def get_clients():
    """Get or initialize database clients."""
    global _dynamo_client, _chroma_client, _embed_fn

    if _dynamo_client is None:
        from receipt_agent.clients.factory import (
            create_chroma_client,
            create_dynamo_client,
            create_embed_fn,
        )
        from receipt_chroma.data.chroma_client import ChromaClient
        from receipt_dynamo.data._pulumi import load_env, load_secrets

        env = os.environ.get("PORTFOLIO_ENV", "dev")
        logger.info("Loading %s environment...", env)

        config = load_env(env=env)
        secrets = load_secrets(env=env)

        # Merge secrets
        for key, value in secrets.items():
            normalized_key = key.replace("portfolio:", "").lower().replace("-", "_")
            config[normalized_key] = value

        # Set up API keys
        if config.get("openai_api_key"):
            os.environ["RECEIPT_AGENT_OPENAI_API_KEY"] = config["openai_api_key"]

        # Create DynamoDB client
        _dynamo_client = create_dynamo_client(table_name=config["dynamodb_table_name"])

        # Create ChromaDB client
        chroma_cloud_api_key = config.get("chroma_cloud_api_key")
        chroma_cloud_enabled = (
            config.get("chroma_cloud_enabled", "false").lower() == "true"
        )

        if not chroma_cloud_enabled or not chroma_cloud_api_key:
            raise ValueError(
                "Chroma Cloud is required. Set chroma_cloud_enabled=true and "
                "chroma_cloud_api_key in Pulumi config."
            )

        _chroma_client = ChromaClient(
            cloud_api_key=chroma_cloud_api_key,
            cloud_tenant=config.get("chroma_cloud_tenant"),
            cloud_database=config.get("chroma_cloud_database"),
            mode="read",
        )

        _embed_fn = create_embed_fn()
        logger.info("Clients initialized successfully")

    return _dynamo_client, _chroma_client, _embed_fn


# Create MCP server
server = Server("receipt-tools")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available receipt tools."""
    return [
        Tool(
            name="search_receipts",
            description="""Search for receipts by text content, label type, or semantic similarity.

Use search_type:
- "text": Exact text match (e.g., query="COFFEE", "MILK", "COSTCO")
- "label": Search by label type (e.g., query="TAX", "GRAND_TOTAL", "MERCHANT_NAME")
- "semantic": Meaning-based similarity (e.g., query="coffee purchases", "dairy products")

Examples:
- search_receipts("COFFEE", "text") - find receipts mentioning coffee
- search_receipts("COSTCO", "text") - find Costco receipts
- search_receipts("GRAND_TOTAL", "label") - find receipts with totals
- search_receipts("breakfast items", "semantic") - semantic search""",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term - product name, label type, or natural language",
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["text", "label", "semantic"],
                        "default": "text",
                        "description": "Search method",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "Maximum results",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_receipt",
            description="""Get full receipt details with formatted text showing all words and their labels.

Returns formatted receipt like:
  (line 1): TRADER[MERCHANT_NAME] JOE'S[MERCHANT_NAME]
  (lines 12-13): ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]
  (lines 18-19): TAX 0.84[TAX]
  (line 20): TOTAL 13.83[GRAND_TOTAL]

Each line shows the DynamoDB line_id range in parentheses. Words on the same
visual row (similar Y-coordinates) are grouped together, so a single display
line may span multiple DynamoDB line_ids. Use these line_ids directly with
get_receipt_words, create_word_label, and update_word_label.

Labels mean:
- [MERCHANT_NAME]: Store name
- [PRODUCT_NAME]: Item description
- [LINE_TOTAL]: Price of that item
- [TAX]: Tax amount
- [GRAND_TOTAL]: Receipt total
- [SUBTOTAL]: Subtotal before tax""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID from search results",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID from search results",
                    },
                },
                "required": ["image_id", "receipt_id"],
            },
        ),
        Tool(
            name="list_all_receipts",
            description="""List all receipts in the database with their merchant names and totals.

Use this to get an overview of what receipts are available.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "Maximum receipts to return",
                    }
                },
            },
        ),
        Tool(
            name="list_merchants",
            description="""List all merchants with receipt counts.

Returns a list of merchants sorted by receipt count (descending).
Use this to see which stores you shop at most frequently.

Example output:
  {"merchants": [
    {"merchant": "Sprouts Farmers Market", "receipt_count": 45},
    {"merchant": "Costco Wholesale", "receipt_count": 12},
    ...
  ]}""",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_receipts_by_merchant",
            description="""Get all receipt IDs for a specific merchant.

Returns image_id and receipt_id for each receipt from the specified merchant.
Use this after list_merchants to drill down into a specific store's receipts.

Example:
  get_receipts_by_merchant("Sprouts Farmers Market") -> returns all Sprouts receipts

Returns compact format: {"merchant": "...", "count": 191, "receipts": [[image_id, receipt_id], ...]}""",
            inputSchema={
                "type": "object",
                "properties": {
                    "merchant_name": {
                        "type": "string",
                        "description": "Merchant name (use exact name from list_merchants)",
                    }
                },
                "required": ["merchant_name"],
            },
        ),
        Tool(
            name="search_product_lines",
            description="""Search for product lines and return prices for spending analysis.

Use search_type:
- "text": Exact text match (e.g., query="MILK", "COFFEE") - fast but requires exact words
- "semantic": Meaning-based similarity (e.g., query="snack foods", "cleaning supplies") - finds conceptually similar items

Use this to answer spending questions like "how much did I spend on X?"

Returns lines with:
- text: The full line text (e.g., "RAW WHOLE MILK 17.99")
- price: The price if found (regex extracted)
- similarity: Match score (semantic only)
- merchant: Store name
- image_id/receipt_id: For drilling into specific receipts

Examples:
  search_product_lines("MILK", "text") -> exact matches for MILK
  search_product_lines("dairy products", "semantic") -> milk, cheese, yogurt, etc.
  search_product_lines("cleaning supplies", "semantic") -> soap, detergent, wipes, etc.

The LLM should filter false positives before summing prices.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Product term or natural language description",
                    },
                    "search_type": {
                        "type": "string",
                        "enum": ["text", "semantic"],
                        "default": "text",
                        "description": "Search method: 'text' for exact match, 'semantic' for meaning-based",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 100,
                        "description": "Maximum results",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="get_receipt_summaries",
            description="""Get pre-computed summaries for all receipts with totals, tax, dates, tips.

Use this to answer aggregation questions like:
- "What was my total spending at Costco?" (merchant_filter="Costco")
- "How much did I spend on groceries?" (category_filter="grocery")
- "How much tax did I pay last month?" (use date filters)
- "What's my average grocery bill?"
- "What was my largest purchase this month?"
- "What's my average tip at restaurants?" (category_filter="restaurant")
- "How much did I spend in 2024?"
- "How much did I spend on gas?" (category_filter="gas_station")

DATE FILTERING EXAMPLES (use ISO format YYYY-MM-DD):
- Last month: start_date="2025-12-01", end_date="2025-12-31"
- Last quarter (Q4 2025): start_date="2025-10-01", end_date="2025-12-31"
- Past 7 days: start_date="2025-01-16" (calculate from today)
- Year 2024: start_date="2024-01-01", end_date="2024-12-31"
- This month: start_date="2025-01-01", end_date="2025-01-31"

Returns aggregates AND individual receipt summaries:
- count: Number of receipts
- total_spending: Sum of all grand_totals
- total_tax: Sum of all tax
- total_tip: Sum of all tips
- average_receipt: Average spending per receipt
- summaries: List of individual receipts with merchant_name, merchant_category, date, grand_total, tax, tip, item_count

Filter by merchant name, category (from Google Places), and/or date range.

Common categories: grocery_store, supermarket, restaurant, gas_station, pharmacy, convenience_store, coffee_shop""",
            inputSchema={
                "type": "object",
                "properties": {
                    "merchant_filter": {
                        "type": "string",
                        "description": "Filter by merchant name (partial match, case-insensitive)",
                    },
                    "category_filter": {
                        "type": "string",
                        "description": "Filter by merchant category from Google Places (e.g., 'grocery', 'restaurant', 'gas_station'). Partial match supported.",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Filter receipts on or after this date (ISO format: YYYY-MM-DD)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Filter receipts on or before this date (ISO format: YYYY-MM-DD)",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 1000,
                        "description": "Maximum receipts to return",
                    },
                },
            },
        ),
        Tool(
            name="list_categories",
            description="""List all merchant categories with receipt counts.

Returns categories from Google Places data, sorted by receipt count.
Use this to discover available categories for filtering with get_receipt_summaries.

Example output:
  {"categories": [
    {"category": "grocery_store", "receipt_count": 241},
    {"category": "restaurant", "receipt_count": 47},
    {"category": "gas_station", "receipt_count": 4},
    ...
  ]}""",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="label_validation_summary",
            description="""Get validation status counts for all core receipt labels.

Returns a compact summary showing how many words have each validation status
(VALID, INVALID, PENDING, NEEDS_REVIEW) for each of the 21 core labels.
This is the same data that powers the Label Validation bar chart.

Use this as the FIRST step to understand label quality, then drill into
specific labels with list_words_by_label.

Example output:
  {"labels": {
    "GRAND_TOTAL": {"VALID": 450, "INVALID": 12, "PENDING": 3, "NEEDS_REVIEW": 1, "total": 466},
    "PRODUCT_NAME": {"VALID": 8200, "INVALID": 45, "PENDING": 120, ...},
    ...
  }}""",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_words_by_label",
            description="""List a sample of words for a specific label, filtered by validation status.

Returns up to `sample_size` words (default 50) for a given label and optional
validation status filter. Use this AFTER label_validation_summary to drill
into a specific label's words.

Each word includes: text, validation_status, image_id, receipt_id, line_id,
word_id, merchant_name, and timestamp.

To avoid context rot, this returns a bounded sample plus the total count.
Use status_filter to focus on actionable words (e.g., "PENDING" or "NEEDS_REVIEW").

Example:
  list_words_by_label("GRAND_TOTAL", status_filter="NEEDS_REVIEW")
  -> {"label": "GRAND_TOTAL", "status_filter": "NEEDS_REVIEW", "total": 5, "sample_size": 5, "words": [...]}""",
            inputSchema={
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Core label name (e.g., GRAND_TOTAL, PRODUCT_NAME, TAX)",
                    },
                    "status_filter": {
                        "type": "string",
                        "enum": ["VALID", "INVALID", "PENDING", "NEEDS_REVIEW", "NONE"],
                        "description": "Optional: filter by validation status. Omit to get all statuses.",
                    },
                    "sample_size": {
                        "type": "integer",
                        "default": 50,
                        "description": "Max words to return (default 50). Use smaller values to reduce context.",
                    },
                },
                "required": ["label"],
            },
        ),
        Tool(
            name="validate_word_similarity",
            description="""Validate a word's label using ChromaDB similarity search.

Given a specific word (by image_id, receipt_id, line_id, word_id), runs the
same similarity search used by the label validation system:

1. Retrieves the word's embedding from Chroma Cloud
2. Queries for similar VALIDATED words where label=True (positive evidence)
3. Queries for similar VALIDATED words where label=False (negative evidence)
4. Computes weighted consensus (with same-merchant boosting)
5. Returns the evidence and a validation decision

Use this to manually review and validate individual words found via
list_words_by_label.

Returns:
  - recommended_status: VALID, INVALID, NEEDS_REVIEW, or PENDING (matches ValidationStatus enum in receipt_dynamo)
  - confidence: 0-1 score
  - evidence_for: list of similar words supporting the label
  - evidence_against: list of similar words rejecting the label
  - suggested_labels: if invalid/uncertain, top alternative label candidates

NOTE: This tool is READ-ONLY. It does NOT write to DynamoDB. It returns a
recommendation that you can review before taking any action.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the word to validate",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID of the word",
                    },
                    "line_id": {
                        "type": "integer",
                        "description": "Line ID of the word",
                    },
                    "word_id": {
                        "type": "integer",
                        "description": "Word ID within the line",
                    },
                    "label": {
                        "type": "string",
                        "description": "The label to validate (e.g., GRAND_TOTAL)",
                    },
                },
                "required": ["image_id", "receipt_id", "line_id", "word_id", "label"],
            },
        ),
        Tool(
            name="update_word_label",
            description="""Update the validation_status of a ReceiptWordLabel record in DynamoDB.

This tool fetches the existing label record, updates the validation_status,
and writes it back. The ValidationStatus enum enforces only valid values:
NONE, PENDING, VALID, INVALID, NEEDS_REVIEW.

The label_proposed_by field is set to "mcp-claude-review" for audit trail.

Use this AFTER reviewing a word with get_receipt (for context) and
validate_word_similarity (for Chroma evidence). Only update when you are
confident in the decision.

WARNING: This WRITES to DynamoDB. Double-check the word context before calling.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the word",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID of the word",
                    },
                    "line_id": {
                        "type": "integer",
                        "description": "Line ID of the word",
                    },
                    "word_id": {
                        "type": "integer",
                        "description": "Word ID within the line",
                    },
                    "label": {
                        "type": "string",
                        "description": "The label name (e.g., PRODUCT_NAME, GRAND_TOTAL)",
                    },
                    "new_status": {
                        "type": "string",
                        "enum": ["VALID", "INVALID", "PENDING", "NEEDS_REVIEW", "NONE"],
                        "description": "The new validation status to set",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why this status was chosen (e.g., 'tax flag indicator, not a product name'). Overwrites existing reasoning.",
                    },
                },
                "required": ["image_id", "receipt_id", "line_id", "word_id", "label", "new_status"],
            },
        ),
        Tool(
            name="create_word_label",
            description="""Create a NEW ReceiptWordLabel record in DynamoDB.

Use this to add a label to a word that doesn't have one yet. For example,
labeling a "40.00" word as CASH_BACK when no CASH_BACK label exists for it.

By default validation_status is VALID and label_proposed_by is "mcp-claude-review".
Pass validation_status="PENDING" for machine-propagated rows (e.g. SECTION_*
sections) that still need QA before they count as ground truth.

consolidated_from: Use this when the new label is a CORRECTION of an existing
wrong label on the same word. Set it to the old label name so we have an audit
trail (e.g., if a word was labeled PRODUCT_NAME but should be CASH_BACK, set
consolidated_from="PRODUCT_NAME"). Leave it out when adding a label to a word
that simply had no label for this concept before.

WARNING: This WRITES to DynamoDB. Will FAIL if a label with the same
image_id/receipt_id/line_id/word_id/label already exists.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the word",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID of the word",
                    },
                    "line_id": {
                        "type": "integer",
                        "description": "Line ID of the word",
                    },
                    "word_id": {
                        "type": "integer",
                        "description": "Word ID within the line",
                    },
                    "label": {
                        "type": "string",
                        "description": "The label to assign (e.g., CASH_BACK, GRAND_TOTAL)",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Why this label is being assigned",
                    },
                    "consolidated_from": {
                        "type": "string",
                        "description": "The old label name this corrects. Set when replacing a wrong label (e.g., 'PRODUCT_NAME' if that was the incorrect label). Omit when adding a brand new label with no predecessor.",
                    },
                    "validation_status": {
                        "type": "string",
                        "enum": ["PENDING", "VALID", "INVALID", "NEEDS_REVIEW"],
                        "description": "Validation status for the new row. Defaults to VALID (human-reviewed). Use PENDING for machine-propagated rows (e.g. SECTION_* sections) that still need QA.",
                    },
                },
                "required": ["image_id", "receipt_id", "line_id", "word_id", "label", "reasoning"],
            },
        ),
        Tool(
            name="merge_receipts",
            description="""Merge two receipt fragments into a single new receipt.

Invokes the merge-receipt Lambda to combine two over-segmented receipt
fragments on the same image into one receipt with proper perspective warping,
new embeddings, and cleanup of the originals.

Steps performed by the Lambda:
1. Read receipt details via GSI4
2. Transform all words to image space and calculate new bounding rect
3. Download original image and create warped receipt image
4. Upload warped image to S3 (raw + CDN variants)
5. Create new Receipt/Line/Word/Letter entities in warped space
6. Migrate labels and place data
7. Write to DynamoDB
8. Create embeddings and wait for compaction
9. Delete original receipts

Use dry_run=true first to preview what will happen without making changes.

WARNING: With dry_run=false, this WRITES to DynamoDB and S3, creates embeddings,
and DELETES the original receipts. Only proceed after verifying with dry_run.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID containing the receipt fragments",
                    },
                    "receipt_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 2,
                        "description": "Exactly 2 distinct receipt IDs to merge",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": True,
                        "description": "If true (default), preview the merge without making changes",
                    },
                },
                "required": ["image_id", "receipt_ids"],
            },
        ),
        Tool(
            name="get_receipt_words",
            description="""Get all words for a receipt with their DynamoDB coordinates and labels.

Returns every word on the receipt with its line_id, word_id, text, and any
existing labels (with validation_status). Words are sorted by line_id then
word_id.

The line_id values here are the DynamoDB keys required by create_word_label
and update_word_label. They match the line_id ranges shown in parentheses by
get_receipt (e.g., "(lines 12-13)"), so you can use get_receipt to identify
the line_id range, then filter here with that line_id for the exact word_id.

Optionally filter to a single line_id to reduce output.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the receipt",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID",
                    },
                    "line_id": {
                        "type": "integer",
                        "description": "Optional: filter to a specific line_id",
                    },
                },
                "required": ["image_id", "receipt_id"],
            },
        ),
        Tool(
            name="fix_place",
            description="""Fix an incorrect merchant/place assignment on a receipt.

Invokes the fix-place Lambda which uses an LLM agent to:
1. Read the receipt content (lines, words, labels)
2. Extract merchant hints (name, address, phone) from the receipt text
3. Search Google Places for the correct match
4. Update the ReceiptPlace record in DynamoDB

Use this when a receipt's merchant name is wrong (e.g., "Mouthful Eatery"
when the receipt clearly says "WHOLE FOODS MARKET").

Returns the old and new merchant names, the new place_id, and confidence.

WARNING: This WRITES to DynamoDB. Verify the receipt is misidentified first
using get_receipt to read its content.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the receipt to fix",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID to fix",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why the current merchant is wrong (e.g., 'Receipt says WHOLE FOODS but merchant is Mouthful Eatery')",
                    },
                },
                "required": ["image_id", "receipt_id", "reason"],
            },
        ),
        Tool(
            name="compute_reocr_region",
            description="""Compute a full-width re-OCR region from receipt line IDs.

Given line_ids, computes a full-width crop that covers the vertical range of
all words on those lines (with padding), and returns a normalized Vision-space
region {x, y, width, height} ready to pass to the trigger_reocr Lambda.

Always uses full image width (x=0, width=1) because Vision OCR produces
significantly better results with more horizontal context.

Use this after inspecting a receipt with get_receipt to identify lines with
bad OCR (e.g., garbled prices). Pass those line_ids here to get the crop region.

Example:
  compute_reocr_region("abc-123", 1, [15, 16, 17])
  -> {"region": {"x": 0.0, "y": 0.31, "width": 1.0, "height": 0.12}}""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID",
                    },
                    "line_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Line IDs to include in the region",
                        "minItems": 1,
                    },
                    "padding": {
                        "type": "number",
                        "description": "Padding (0-1) around the region. Default 0.05",
                        "default": 0.05,
                    },
                },
                "required": ["image_id", "receipt_id", "line_ids"],
            },
        ),
        Tool(
            name="trigger_reocr",
            description="""Trigger regional re-OCR for a receipt.

Creates an OCR job and sends it to the processing queue. The Swift worker
will crop the specified region, run Vision OCR, and the overlay Lambda
will update the receipt words with corrected text.

Use compute_reocr_region first to get the region from line IDs.

WARNING: This WRITES to DynamoDB and triggers async processing.
Back up the receipt with export_image before triggering.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID",
                    },
                    "reocr_region": {
                        "type": "object",
                        "description": "Normalized region {x, y, width, height}",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "width": {"type": "number"},
                            "height": {"type": "number"},
                        },
                        "required": ["x", "y", "width", "height"],
                    },
                    "reocr_reason": {
                        "type": "string",
                        "description": "Reason for re-OCR. Default: manual_trigger",
                        "default": "manual_trigger",
                    },
                },
                "required": ["image_id", "receipt_id", "reocr_region"],
            },
        ),
        Tool(
            name="list_recent_uploads",
            description="""List the most recently uploaded images, sorted newest first.

Shows image ID, upload timestamp, image type (SCAN/PHOTO/NATIVE),
receipt count, dimensions, and merchant names for each receipt.

Use this to find recently uploaded images or check processing status.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Number of recent uploads to return (max 50)",
                    },
                },
            },
        ),
        Tool(
            name="get_receipt_image_url",
            description="""Get the CDN URL for a receipt image.

Queries the Receipt record in DynamoDB and builds the URL from its cdn_s3_key.
Returns the primary JPG URL plus any available variants (WebP, AVIF, thumbnail,
small, medium).

Example response:
  {"url": "https://dev.tylernorlund.com/assets/{image_id}_RECEIPT_00002.jpg",
   "variants": {"webp": "...", "thumbnail": "..."}}

Use this to visually inspect a receipt when reviewing OCR quality or labels.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID (UUID)",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID",
                    },
                },
                "required": ["image_id", "receipt_id"],
            },
        ),
        Tool(
            name="delete_image",
            description="""Delete an image and ALL its child records from DynamoDB.

Queries every item under PK = IMAGE#{image_id} and batch-deletes them.
This removes the Image entity plus every child: receipts, lines, words,
letters, labels, places, summaries, OCR jobs, routing decisions, etc.

Returns a breakdown of entity types and counts before deleting.

By default runs in dry-run mode — set dry_run=false to actually delete.

WARNING: This is IRREVERSIBLE. Verify the image is truly unwanted first
using list_recent_uploads or get_receipt.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID (UUID) to delete",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": True,
                        "description": "If true (default), preview what would be deleted without making changes",
                    },
                },
                "required": ["image_id"],
            },
        ),
        Tool(
            name="delete_receipt",
            description="""Delete a single receipt (and its child records) from an image, keeping the rest of the image.

Use this to remove a spurious or over-segmented receipt fragment — e.g. a
detector false-positive on bag/packaging text or signage — while leaving the
other receipts on the same image intact.

Choosing the right tool:
- delete_receipt: remove ONE bogus receipt, keep the image and its other receipts.
- merge_receipts: combine TWO fragments that are halves of the SAME receipt.
- delete_image: remove the whole image and every receipt on it.

How it works: deletes the Receipt entity from DynamoDB. The enhanced compactor
then automatically removes the ChromaDB embeddings and all child records
(ReceiptLine, ReceiptWord, ReceiptLetter, ReceiptWordLabel, ReceiptPlace) via
DynamoDB streams.

Returns the receipt's merchant and a breakdown of child-record counts. By
default runs in dry-run mode — set dry_run=false to actually delete.

WARNING: This is IRREVERSIBLE. Verify the receipt is truly unwanted first
using get_receipt or get_receipt_image_url.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID (UUID) containing the receipt",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID to delete",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "default": True,
                        "description": "If true (default), preview what would be deleted without making changes",
                    },
                },
                "required": ["image_id", "receipt_id"],
            },
        ),
        Tool(
            name="analytics_traffic",
            description="""Per-day production web traffic (CloudFront logs via Athena).

One row per day (Pacific time): total requests, outside-human requests (bots
AND your own Cloudflare WARP traffic excluded), WARP requests, bot/crawler
requests, distinct human sessions, and human pageviews.

Use for "how much real traffic did the site get" over a range. Dates are
YYYY-MM-DD, inclusive, in America/Los_Angeles.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (PT, inclusive)"},
                    "end_date": {"type": "string", "description": "End date YYYY-MM-DD (PT, inclusive)"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="analytics_sessions",
            description="""Reconstructed visitor sessions from the analytics beacon (real JS browsers).

Each row is one session (grouped by beacon session id): first/last seen (PT),
client IP, distinct pages, pageview/scroll/reader-summary counts, total beacon
hits. By default excludes bots and your own WARP traffic, so what remains is
real outside humans. Ordered by activity.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (PT)"},
                    "end_date": {"type": "string", "description": "End date YYYY-MM-DD (PT)"},
                    "humans_only": {"type": "boolean", "default": True, "description": "Exclude bots + WARP (default true)"},
                    "limit": {"type": "integer", "default": 50, "description": "Max sessions to return"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="analytics_top",
            description="""Top values for a dimension over a date range (CloudFront logs via Athena).

dimension: 'page' (most-viewed pages from page_view beacons), 'referrer'
(external referrers, self-referrals excluded), or 'ip' (busiest client IPs).
Returns value + hit count + distinct sessions. Bots + WARP excluded by default.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "dimension": {"type": "string", "enum": ["page", "referrer", "ip", "country", "org"], "description": "What to rank"},
                    "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (PT)"},
                    "end_date": {"type": "string", "description": "End date YYYY-MM-DD (PT)"},
                    "limit": {"type": "integer", "default": 15, "description": "Max rows"},
                    "humans_only": {"type": "boolean", "default": True, "description": "Exclude bots + WARP (default true)"},
                },
                "required": ["dimension", "start_date", "end_date"],
            },
        ),
        Tool(
            name="analytics_ip",
            description="""All meaningful requests from a single client IP over a date range.

Page-level timeline (static assets excluded): PT time, uri, status, referrer,
parsed beacon event/path, plus is_warp/is_bot flags. Use after analytics_top or
analytics_sessions to inspect one visitor. Org/geo is NOT included here — look
that up separately (e.g. an IP-info service).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "ip": {"type": "string", "description": "Client IP (v4 or v6)"},
                    "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (PT)"},
                    "end_date": {"type": "string", "description": "End date YYYY-MM-DD (PT)"},
                },
                "required": ["ip", "start_date", "end_date"],
            },
        ),
        Tool(
            name="analytics_query",
            description="""Run a read-only Athena SQL query against the analytics database.

Escape hatch for ad-hoc analysis. Only a single SELECT/WITH statement (no
DDL/DML, no multiple statements). Table: portfolio_analytics.cloudfront_logs_prod
(raw CloudFront access logs; the analytics beacon is uri='/analytics/pixel.txt'
with event/sid/path in the url-encoded query_string). Prefer the other
analytics_* tools; use this only when they don't fit.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A single read-only SELECT/WITH query"},
                },
                "required": ["sql"],
            },
        ),
        Tool(
            name="analytics_ga",
            description="""Daily Google Analytics (GA4) metrics for the production site.

Reads the ga_daily table (extracted from the GA4 Data API): sessions, total/new
users, pageviews, engaged sessions per day (GA property timezone = Pacific).
GA applies Google's own bot filtering but is blocked by adblockers / Safari ITP,
so it UNDER-counts real humans; pair with analytics_traffic (first-party beacon)
via analytics_reconcile. Dates YYYY-MM-DD, inclusive.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="analytics_reconcile",
            description="""Reconcile GA4 vs the first-party CloudFront beacon, per day.

Side-by-side daily counts: GA sessions/engaged/pageviews vs beacon human
sessions/pageviews. GA = Google's bot-filtered but adblock-leaky view; the
beacon = complete first-party signal with bots + your own WARP traffic removed.
Gaps are meaningful: beacon > GA usually means adblock loss; large GA-only days
can mean bot leakage. Dates YYYY-MM-DD, inclusive (Pacific).""",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="analytics_github",
            description="""GitHub repo traffic: daily views/clones + top referrers/paths.

Snapshotted daily from the GitHub Traffic API, whose native window is only the
last 14 days — this tool reads the accumulated history. Use it to explain
GitHub-sourced site visits (referrers) and to see repo interest over time.
Daily views/uniques are the human signal; clones are mostly CI/bot noise.
Referrers/paths reflect the most recent 14-day snapshot. Dates YYYY-MM-DD.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
                },
                "required": ["start_date", "end_date"],
            },
        ),
        Tool(
            name="list_training_jobs",
            description="""List LayoutLM training jobs with F1 scores, hyperparameters, and status.

Returns jobs sorted newest-first with:
- name, status, created_at
- best_f1, best_epoch (from results)
- learning_rate, epochs, batch_size, merge_amounts, early_stopping_patience (from config)
- num_train_samples, num_val_samples

Use status_filter to narrow results (e.g., "succeeded", "running", "failed").""",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "description": "Maximum number of jobs to return",
                    },
                    "status_filter": {
                        "type": "string",
                        "enum": ["succeeded", "running", "failed", "pending", "cancelled", "interrupted"],
                        "description": "Filter jobs by status (optional)",
                    },
                },
            },
        ),
        Tool(
            name="get_training_metrics",
            description="""Get epoch-by-epoch training metrics for a specific training job.

Returns metrics grouped by epoch including:
- eval_f1, eval_precision, eval_recall, eval_loss
- loss, learning_rate

Use this to understand how a model's performance evolved during training.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "Name of the training job (e.g., 'layoutlm-my-experiment')",
                    },
                },
                "required": ["job_name"],
            },
        ),
        Tool(
            name="get_active_model",
            description="""Show which LayoutLM model is currently marked as active for inference.

Returns the active model's name, job_id, best_f1, best_checkpoint_s3_path, and tags.
The active model is used by the CoreML export pipeline and inference services.""",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="set_active_model",
            description="""Mark a training job as the active model for inference services.

WARNING: This WRITES to DynamoDB. It clears the active_model tag from the
currently active job and sets it on the specified job. This affects which
model is used by the CoreML export pipeline and inference services.

Only use this after confirming the job has good F1 scores and is ready for production.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_name": {
                        "type": "string",
                        "description": "Name of the training job to mark as active",
                    },
                },
                "required": ["job_name"],
            },
        ),
        Tool(
            name="get_label_distribution",
            description="""Show the distribution of word labels in the training dataset.

Returns how many words have each label type (MERCHANT_NAME, PRODUCT_NAME, etc.)
broken down by validation status (VALID, INVALID, PENDING, NEEDS_REVIEW, NONE).

Use this to understand training data balance and identify under-represented labels.""",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_receipt_sections",
            description="""List a receipt's classified sections for QA review.

Returns every ReceiptSection on the receipt with its section_type, line_ids,
confidence, model_source, and validation_status. For each section it also
includes the text of every line it covers, so you can QA a section without a
second get_receipt call.

model_source distinguishes generations of rows (e.g. "section-seed-v0" for
hand/heuristic seeds, "section-knn-v1" for KNN-propagated rows). KNN-propagated
rows land with validation_status=PENDING and need review before they count as
ground truth.

Use this to inspect sections, then update_section_status to mark each one
VALID / INVALID / NEEDS_REVIEW.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the receipt",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID",
                    },
                },
                "required": ["image_id", "receipt_id"],
            },
        ),
        Tool(
            name="update_section_status",
            description="""Update the validation_status of an existing ReceiptSection.

Sets validation_status on a section already present on the receipt. The
ValidationStatus enum enforces only valid values: NONE, PENDING, VALID,
INVALID, NEEDS_REVIEW. Errors clearly if the section does not exist.

Use this AFTER reviewing a section with get_receipt_sections. This is the QA
loop for machine-propagated (e.g. section-knn-v1) PENDING rows: promote correct
ones to VALID and reject wrong ones as INVALID.

WARNING: This WRITES to DynamoDB.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the receipt",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID",
                    },
                    "section_type": {
                        "type": "string",
                        "description": "The section_type to update (e.g. ITEMS, TOTAL_LINE, PAYMENT)",
                    },
                    "validation_status": {
                        "type": "string",
                        "enum": ["VALID", "INVALID", "NEEDS_REVIEW", "PENDING", "NONE"],
                        "description": "The new validation status to set",
                    },
                },
                "required": [
                    "image_id",
                    "receipt_id",
                    "section_type",
                    "validation_status",
                ],
            },
        ),
        Tool(
            name="create_receipt_section",
            description="""Create a NEW ReceiptSection on a receipt.

Use this to add a section that a reviewer identified but no model seeded, e.g.
marking lines 20-24 as PAYMENT. section_type must be one of the canonical
SectionType values (STOREFRONT, ADDRESS, ITEMS, SECTION_HEADER, SUMMARY,
TOTAL_LINE, PAYMENT, SURVEY, FOOTER, BARCODE).

By default validation_status is VALID (human-reviewed) and model_source is
"mcp-claude-review".

WARNING: This WRITES to DynamoDB. Will FAIL if a section with the same
section_type already exists on the receipt — use update_section_status to
change an existing section instead. Also fails if the receipt does not exist
or any line_id is not on the receipt.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the receipt",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID",
                    },
                    "section_type": {
                        "type": "string",
                        "description": "The canonical section type (e.g. ITEMS, PAYMENT, FOOTER)",
                    },
                    "line_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 1,
                        "description": "The line IDs that make up this section",
                    },
                    "validation_status": {
                        "type": "string",
                        "enum": ["VALID", "INVALID", "NEEDS_REVIEW", "PENDING", "NONE"],
                        "description": "Validation status for the new section. Defaults to VALID.",
                    },
                    "model_source": {
                        "type": "string",
                        "description": "Source tag for the row. Defaults to 'mcp-claude-review'.",
                    },
                },
                "required": ["image_id", "receipt_id", "section_type", "line_ids"],
            },
        ),
        Tool(
            name="delete_receipt_section",
            description="""Delete a single ReceiptSection row from a receipt.

Removes one section (identified by section_type) from the receipt, leaving the
rest of the receipt and its other sections intact. Use this to drop a spurious
or mis-typed section.

Errors clearly if the section does not exist.

WARNING: This WRITES to DynamoDB and is irreversible for that row.""",
            inputSchema={
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Image ID of the receipt",
                    },
                    "receipt_id": {
                        "type": "integer",
                        "description": "Receipt ID",
                    },
                    "section_type": {
                        "type": "string",
                        "description": "The section_type to delete (e.g. BARCODE, SURVEY)",
                    },
                },
                "required": ["image_id", "receipt_id", "section_type"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        dynamo_client, chroma_client, embed_fn = get_clients()

        if name == "search_receipts":
            result = await search_receipts_impl(
                chroma_client,
                embed_fn,
                query=arguments["query"],
                search_type=arguments.get("search_type", "text"),
                limit=arguments.get("limit", 20),
            )
        elif name == "get_receipt":
            result = await get_receipt_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
            )
        elif name == "list_all_receipts":
            result = await list_all_receipts_impl(
                chroma_client,
                limit=arguments.get("limit", 50),
            )
        elif name == "list_merchants":
            result = await list_merchants_impl(dynamo_client)
        elif name == "get_receipts_by_merchant":
            result = await get_receipts_by_merchant_impl(
                dynamo_client,
                merchant_name=arguments["merchant_name"],
            )
        elif name == "search_product_lines":
            result = await search_product_lines_impl(
                chroma_client,
                embed_fn,
                query=arguments["query"],
                search_type=arguments.get("search_type", "text"),
                limit=arguments.get("limit", 100),
            )
        elif name == "get_receipt_summaries":
            result = await get_receipt_summaries_impl(
                dynamo_client,
                merchant_filter=arguments.get("merchant_filter"),
                category_filter=arguments.get("category_filter"),
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                limit=arguments.get("limit", 1000),
            )
        elif name == "list_categories":
            result = await list_categories_impl(dynamo_client)
        elif name == "label_validation_summary":
            result = await label_validation_summary_impl(dynamo_client)
        elif name == "list_words_by_label":
            result = await list_words_by_label_impl(
                dynamo_client,
                label=arguments["label"],
                status_filter=arguments.get("status_filter"),
                sample_size=arguments.get("sample_size", 50),
            )
        elif name == "validate_word_similarity":
            result = await validate_word_similarity_impl(
                chroma_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                line_id=arguments["line_id"],
                word_id=arguments["word_id"],
                label=arguments["label"],
            )
        elif name == "update_word_label":
            result = await update_word_label_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                line_id=arguments["line_id"],
                word_id=arguments["word_id"],
                label=arguments["label"],
                new_status=arguments["new_status"],
                reasoning=arguments.get("reasoning"),
            )
        elif name == "merge_receipts":
            result = await merge_receipts_impl(
                image_id=arguments["image_id"],
                receipt_ids=arguments["receipt_ids"],
                dry_run=arguments.get("dry_run", True),
            )
        elif name == "get_receipt_words":
            result = await get_receipt_words_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                line_id=arguments.get("line_id"),
            )
        elif name == "fix_place":
            result = await fix_place_impl(
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                reason=arguments["reason"],
            )
        elif name == "create_word_label":
            result = await create_word_label_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                line_id=arguments["line_id"],
                word_id=arguments["word_id"],
                label=arguments["label"],
                reasoning=arguments["reasoning"],
                consolidated_from=arguments.get("consolidated_from"),
                validation_status=arguments.get(
                    "validation_status", "VALID"
                ),
            )
        elif name == "compute_reocr_region":
            result = await compute_reocr_region_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                line_ids=arguments["line_ids"],
                padding=arguments.get("padding", 0.05),
            )
        elif name == "trigger_reocr":
            result = await trigger_reocr_impl(
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                reocr_region=arguments["reocr_region"],
                reocr_reason=arguments.get("reocr_reason", "manual_trigger"),
            )
        elif name == "list_recent_uploads":
            result = await list_recent_uploads_impl(
                dynamo_client,
                limit=arguments.get("limit", 10),
            )
        elif name == "get_receipt_image_url":
            result = await get_receipt_image_url_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
            )
        elif name == "delete_image":
            result = await delete_image_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                dry_run=arguments.get("dry_run", True),
            )
        elif name == "delete_receipt":
            result = await delete_receipt_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                dry_run=arguments.get("dry_run", True),
            )
        elif name == "get_receipt_sections":
            result = await get_receipt_sections_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
            )
        elif name == "update_section_status":
            result = await update_section_status_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                section_type=arguments["section_type"],
                validation_status=arguments["validation_status"],
            )
        elif name == "create_receipt_section":
            result = await create_receipt_section_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                section_type=arguments["section_type"],
                line_ids=arguments["line_ids"],
                validation_status=arguments.get("validation_status", "VALID"),
                model_source=arguments.get(
                    "model_source", "mcp-claude-review"
                ),
            )
        elif name == "delete_receipt_section":
            result = await delete_receipt_section_impl(
                dynamo_client,
                image_id=arguments["image_id"],
                receipt_id=arguments["receipt_id"],
                section_type=arguments["section_type"],
            )
        elif name == "list_training_jobs":
            result = await list_training_jobs_impl(
                dynamo_client,
                limit=arguments.get("limit", 20),
                status_filter=arguments.get("status_filter"),
            )
        elif name == "get_training_metrics":
            result = await get_training_metrics_impl(
                dynamo_client,
                job_name=arguments["job_name"],
            )
        elif name == "get_active_model":
            result = await get_active_model_impl(dynamo_client)
        elif name == "set_active_model":
            result = await set_active_model_impl(
                dynamo_client,
                job_name=arguments["job_name"],
            )
        elif name == "get_label_distribution":
            result = await get_label_distribution_impl(dynamo_client)
        elif name == "analytics_traffic":
            result = await analytics_traffic_impl(
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
            )
        elif name == "analytics_sessions":
            result = await analytics_sessions_impl(
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
                humans_only=arguments.get("humans_only", True),
                limit=arguments.get("limit", 50),
            )
        elif name == "analytics_top":
            result = await analytics_top_impl(
                dimension=arguments["dimension"],
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
                limit=arguments.get("limit", 15),
                humans_only=arguments.get("humans_only", True),
            )
        elif name == "analytics_ip":
            result = await analytics_ip_impl(
                ip=arguments["ip"],
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
            )
        elif name == "analytics_query":
            result = await analytics_query_impl(sql=arguments["sql"])
        elif name == "analytics_ga":
            result = await analytics_ga_impl(
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
            )
        elif name == "analytics_reconcile":
            result = await analytics_reconcile_impl(
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
            )
        elif name == "analytics_github":
            result = await analytics_github_impl(
                start_date=arguments["start_date"],
                end_date=arguments["end_date"],
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except Exception as e:
        logger.exception("Tool error")
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]


async def search_receipts_impl(
    chroma_client,
    embed_fn,
    query: str,
    search_type: str,
    limit: int,
) -> dict:
    """Search for receipts."""
    try:
        if search_type == "label":
            words_collection = chroma_client.get_collection("words")
            results = words_collection.get(
                where={"label": query.upper()},
                include=["metadatas"],
            )

            unique_receipts = {}
            for meta in results["metadatas"]:
                key = (meta.get("image_id"), meta.get("receipt_id"))
                if key not in unique_receipts:
                    unique_receipts[key] = {
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                        "matched_text": meta.get("text"),
                        "matched_label": query.upper(),
                    }

            return {
                "search_type": "label",
                "query": query,
                "total_matches": len(results["ids"]),
                "unique_receipts": len(unique_receipts),
                "results": list(unique_receipts.values())[:limit],
            }

        elif search_type == "semantic":
            lines_collection = chroma_client.get_collection("lines")
            query_embeddings = embed_fn([query])

            if not query_embeddings or not query_embeddings[0]:
                return {"error": "Failed to generate embedding"}

            results = lines_collection.query(
                query_embeddings=query_embeddings,
                n_results=limit * 2,
                include=["metadatas", "distances"],
            )

            unique_receipts = {}
            if results["ids"] and results["ids"][0]:
                for idx, (id_, meta) in enumerate(
                    zip(results["ids"][0], results["metadatas"][0])
                ):
                    key = (meta.get("image_id"), meta.get("receipt_id"))
                    distance = (
                        results["distances"][0][idx] if results["distances"] else 1.0
                    )
                    similarity = max(0.0, 1.0 - distance)

                    if key not in unique_receipts:
                        unique_receipts[key] = {
                            "image_id": meta.get("image_id"),
                            "receipt_id": meta.get("receipt_id"),
                            "matched_text": meta.get("text", "")[:100],
                            "similarity": round(similarity, 3),
                        }

            sorted_results = sorted(
                unique_receipts.values(),
                key=lambda x: x.get("similarity", 0),
                reverse=True,
            )[:limit]

            return {
                "search_type": "semantic",
                "query": query,
                "unique_receipts": len(sorted_results),
                "results": sorted_results,
            }

        else:  # text search
            lines_collection = chroma_client.get_collection("lines")
            results = lines_collection.get(
                where_document={"$contains": query.upper()},
                include=["metadatas"],
            )

            unique_receipts = {}
            for meta in results["metadatas"]:
                key = (meta.get("image_id"), meta.get("receipt_id"))
                if key not in unique_receipts:
                    unique_receipts[key] = {
                        "image_id": meta.get("image_id"),
                        "receipt_id": meta.get("receipt_id"),
                        "matched_line": meta.get("text", "")[:100],
                    }

            return {
                "search_type": "text",
                "query": query,
                "total_matches": len(results["ids"]),
                "unique_receipts": len(unique_receipts),
                "results": list(unique_receipts.values())[:limit],
            }

    except Exception as e:
        return {"error": str(e)}


async def get_receipt_impl(dynamo_client, image_id: str, receipt_id: int) -> dict:
    """Get full receipt details."""
    import statistics

    try:
        details = dynamo_client.get_receipt_details(image_id, receipt_id)

        # Get merchant
        merchant = "Unknown"
        if details.place:
            merchant = details.place.merchant_name or "Unknown"

        # Build label lookup
        labels_by_word: dict[tuple[int, int], list] = defaultdict(list)
        for label in details.labels:
            key = (label.line_id, label.word_id)
            labels_by_word[key].append(label)

        def get_valid_label(line_id: int, word_id: int) -> Optional[str]:
            history = labels_by_word.get((line_id, word_id), [])
            valid = [lb for lb in history if lb.validation_status == "VALID"]
            if valid:
                valid.sort(key=lambda lb: str(lb.timestamp_added), reverse=True)
                return valid[0].label
            return None

        # Build word contexts
        word_contexts = []
        for word in details.words:
            centroid = word.calculate_centroid()
            label = get_valid_label(word.line_id, word.word_id)
            word_contexts.append(
                {
                    "word": word,
                    "label": label,
                    "y": centroid[1],
                    "x": centroid[0],
                    "text": word.text,
                }
            )

        if not word_contexts:
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant": merchant,
                "formatted_receipt": "(empty receipt)",
                "amounts": [],
            }

        # Sort by y descending (top first)
        sorted_words = sorted(word_contexts, key=lambda w: -w["y"])

        # Group into visual lines
        heights = [
            w["word"].bounding_box.get("height", 0.02)
            for w in sorted_words
            if w["word"].bounding_box.get("height")
        ]
        y_tolerance = max(0.01, statistics.median(heights) * 0.75) if heights else 0.015

        visual_lines = []
        current_line = [sorted_words[0]]
        current_y = sorted_words[0]["y"]

        for w in sorted_words[1:]:
            if abs(w["y"] - current_y) <= y_tolerance:
                current_line.append(w)
                current_y = sum(c["y"] for c in current_line) / len(current_line)
            else:
                current_line.sort(key=lambda c: c["x"])
                visual_lines.append(current_line)
                current_line = [w]
                current_y = w["y"]

        current_line.sort(key=lambda c: c["x"])
        visual_lines.append(current_line)

        # Format as text with inline labels, showing DynamoDB line_id ranges
        formatted_lines = []
        for line in visual_lines:
            line_parts = []
            for w in line:
                if w["label"]:
                    line_parts.append(f"{w['text']}[{w['label']}]")
                else:
                    line_parts.append(w["text"])
            # Show DynamoDB line_id range for this visual line
            line_ids = sorted({w["word"].line_id for w in line})
            if len(line_ids) == 1:
                prefix = f"(line {line_ids[0]})"
            elif line_ids[-1] - line_ids[0] + 1 == len(line_ids):
                prefix = f"(lines {line_ids[0]}-{line_ids[-1]})"
            else:
                prefix = f"(lines {','.join(str(lid) for lid in line_ids)})"
            formatted_lines.append(f"{prefix}: {' '.join(line_parts)}")

        formatted_receipt = "\n".join(formatted_lines)

        # Extract amounts
        amounts = []
        currency_labels = ["TAX", "SUBTOTAL", "GRAND_TOTAL", "LINE_TOTAL", "UNIT_PRICE"]
        for w in sorted_words:
            if w["label"] in currency_labels:
                try:
                    amount = float(w["text"].replace("$", "").replace(",", ""))
                    amounts.append(
                        {
                            "label": w["label"],
                            "text": w["text"],
                            "amount": amount,
                        }
                    )
                except ValueError:
                    pass

        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant": merchant,
            "formatted_receipt": formatted_receipt,
            "amounts": amounts,
        }

    except Exception as e:
        return {"error": str(e)}


async def list_all_receipts_impl(chroma_client, limit: int) -> dict:
    """List all receipts."""
    try:
        lines_collection = chroma_client.get_collection("lines")

        # Get all receipts by querying metadata (no label filtering)
        results = lines_collection.get(
            include=["metadatas"],
        )

        unique_receipts = {}
        for meta in results["metadatas"]:
            key = (meta.get("image_id"), meta.get("receipt_id"))
            if key not in unique_receipts:
                unique_receipts[key] = {
                    "image_id": meta.get("image_id"),
                    "receipt_id": meta.get("receipt_id"),
                    "sample_text": meta.get("text", "")[:50],
                }

        return {
            "total_receipts": len(unique_receipts),
            "receipts": list(unique_receipts.values())[:limit],
        }

    except Exception as e:
        return {"error": str(e)}


async def list_merchants_impl(dynamo_client) -> dict:
    """List all merchants with receipt counts."""
    try:
        # Paginate through all ReceiptPlace records
        merchant_counts: dict[str, int] = defaultdict(int)
        last_key = None

        while True:
            places, last_key = dynamo_client.list_receipt_places(
                limit=1000,
                last_evaluated_key=last_key,
            )

            for place in places:
                merchant_counts[place.merchant_name] += 1

            if last_key is None:
                break

        # Sort by count descending
        sorted_merchants = sorted(
            merchant_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        return {
            "total_merchants": len(sorted_merchants),
            "merchants": [
                {"merchant": name, "receipt_count": count}
                for name, count in sorted_merchants
            ],
        }

    except Exception as e:
        logger.exception("Error listing merchants")
        return {"error": str(e)}


async def get_receipts_by_merchant_impl(
    dynamo_client,
    merchant_name: str,
) -> dict:
    """Get all receipt IDs for a specific merchant."""
    try:
        # Paginate through all receipts for this merchant
        all_places = []
        last_key = None

        while True:
            places, last_key = dynamo_client.get_receipt_places_by_merchant(
                merchant_name=merchant_name,
                limit=1000,
                last_evaluated_key=last_key,
            )
            all_places.extend(places)

            if last_key is None:
                break

        # Return compact list - just the IDs needed to call get_receipt
        receipts = [[place.image_id, place.receipt_id] for place in all_places]

        return {
            "merchant": merchant_name,
            "count": len(receipts),
            "receipts": receipts,  # [[image_id, receipt_id], ...]
        }

    except Exception as e:
        logger.exception("Error getting receipts by merchant")
        return {"error": str(e)}


async def search_product_lines_impl(
    chroma_client,
    embed_fn,
    query: str,
    search_type: str,
    limit: int,
) -> dict:
    """Search for product lines and extract prices for spending analysis.

    Supports both text (exact match) and semantic (embedding-based) search.
    """
    import re

    try:
        lines_collection = chroma_client.get_collection("lines")

        # Extract price from text (e.g., "RAW WHOLE MILK 17.99" -> 17.99)
        def extract_price(text: str) -> Optional[float]:
            matches = re.findall(r"\d+\.\d{2}", text)
            if matches:
                return float(matches[-1])  # Last match is usually the price
            return None

        if search_type == "semantic":
            # Semantic search using embeddings
            query_embeddings = embed_fn([query])

            if not query_embeddings or not query_embeddings[0]:
                return {"error": "Failed to generate embedding"}

            results = lines_collection.query(
                query_embeddings=query_embeddings,
                n_results=limit * 3,  # Get more to filter duplicates
                include=["metadatas", "distances"],
            )

            if not results["ids"] or not results["ids"][0]:
                return {
                    "query": query,
                    "search_type": "semantic",
                    "total_matches": 0,
                    "items": [],
                }

            # Process semantic results with similarity scores
            items = []
            seen = set()

            for idx, (id_, meta) in enumerate(
                zip(results["ids"][0], results["metadatas"][0])
            ):
                text = meta.get("text", "")
                image_id = meta.get("image_id")
                receipt_id = meta.get("receipt_id")

                # Dedupe
                key = (image_id, receipt_id, text)
                if key in seen:
                    continue
                seen.add(key)

                # Calculate similarity from distance
                distance = results["distances"][0][idx] if results["distances"] else 1.0
                similarity = max(0.0, 1.0 - distance)

                # Skip low similarity results
                if similarity < 0.25:
                    continue

                has_line_total = meta.get("label_LINE_TOTAL", False)
                price = extract_price(text)

                items.append(
                    {
                        "text": text,
                        "price": price,
                        "similarity": round(similarity, 3),
                        "has_price_label": has_line_total,
                        "merchant": meta.get("merchant_name", "Unknown"),
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                    }
                )

            # Sort by similarity descending
            items.sort(key=lambda x: -x.get("similarity", 0))
            items = items[:limit]

            # Calculate total for items that have prices
            total = sum(item["price"] for item in items if item["price"] is not None)

            return {
                "query": query,
                "search_type": "semantic",
                "total_matches": len(results["ids"][0]),
                "unique_items": len(items),
                "items": items,
                "raw_total": round(total, 2),
                "note": "Semantic search finds conceptually similar items. Review relevance before summing prices.",
            }

        else:
            # Text search (exact match)
            results = lines_collection.get(
                where_document={"$contains": query.upper()},
                include=["metadatas"],
            )

            if not results["ids"]:
                return {
                    "query": query,
                    "search_type": "text",
                    "total_matches": 0,
                    "items": [],
                }

            # Process results
            items = []
            seen = set()  # Dedupe by image_id + receipt_id + text

            for meta in results["metadatas"]:
                text = meta.get("text", "")
                image_id = meta.get("image_id")
                receipt_id = meta.get("receipt_id")

                # Dedupe
                key = (image_id, receipt_id, text)
                if key in seen:
                    continue
                seen.add(key)

                # Check if this line has a LINE_TOTAL label (from ML model)
                has_line_total = meta.get("label_LINE_TOTAL", False)

                price = extract_price(text)

                items.append(
                    {
                        "text": text,
                        "price": price,
                        "has_price_label": has_line_total,
                        "merchant": meta.get("merchant_name", "Unknown"),
                        "image_id": image_id,
                        "receipt_id": receipt_id,
                    }
                )

            # Sort by price descending (items with prices first)
            items.sort(key=lambda x: (x["price"] is None, -(x["price"] or 0)))

            # Limit results
            items = items[:limit]

            # Calculate total for items that have prices
            total = sum(item["price"] for item in items if item["price"] is not None)

            return {
                "query": query,
                "search_type": "text",
                "total_matches": len(results["ids"]),
                "unique_items": len(items),
                "items": items,
                "raw_total": round(total, 2),
                "note": "Review items and exclude false positives (e.g., 'MILK CHOCOLATE' when searching for milk) before reporting final total.",
            }

    except Exception as e:
        logger.exception("Error searching product lines")
        return {"error": str(e)}


async def get_receipt_summaries_impl(
    dynamo_client,
    merchant_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 1000,
) -> dict:
    """Get pre-computed summaries for all receipts from DynamoDB.

    Reads ReceiptSummaryRecord from DynamoDB (pre-computed totals, tax, dates).
    Supports filtering by merchant name, category (from Google Places), and date range.
    """
    from datetime import datetime

    try:
        # Parse date filters
        start_dt = None
        end_dt = None
        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            except ValueError:
                return {
                    "error": f"Invalid start_date format: '{start_date}'. Use ISO format (e.g., 2024-01-15)."
                }
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            except ValueError:
                return {
                    "error": f"Invalid end_date format: '{end_date}'. Use ISO format (e.g., 2024-01-15)."
                }

        # Load all summaries from DynamoDB (pre-computed)
        all_summaries = []
        last_key = None
        while True:
            records, last_key = dynamo_client.list_receipt_summaries(
                limit=1000,
                last_evaluated_key=last_key,
            )
            all_summaries.extend(records)
            if last_key is None:
                break

        # Load ReceiptPlace records to get merchant categories (always needed for output)
        places_by_key: dict[str, Any] = {}
        last_key = None
        while True:
            places, last_key = dynamo_client.list_receipt_places(
                limit=1000,
                last_evaluated_key=last_key,
            )
            for place in places:
                key = f"{place.image_id}_{place.receipt_id}"
                places_by_key[key] = place
            if last_key is None:
                break

        # Filter in memory
        filtered = []
        for record in all_summaries:
            # Get place info for this receipt
            key = f"{record.image_id}_{record.receipt_id}"
            place = places_by_key.get(key)
            merchant_category = place.merchant_category if place else ""

            # Merchant filter
            if merchant_filter:
                if not record.merchant_name:
                    continue
                if merchant_filter.lower() not in record.merchant_name.lower():
                    continue

            # Category filter (partial match on category or types)
            if category_filter:
                category_match = False
                if (
                    merchant_category
                    and category_filter.lower() in merchant_category.lower()
                ):
                    category_match = True
                # Also check merchant_types list
                if place and place.merchant_types:
                    for t in place.merchant_types:
                        if category_filter.lower() in t.lower():
                            category_match = True
                            break
                if not category_match:
                    continue

            # Date filter
            if start_dt and record.date:
                if record.date < start_dt:
                    continue
            if end_dt and record.date:
                if record.date > end_dt:
                    continue

            # Build output dict with category info
            summary_dict = record.to_dict()
            summary_dict["merchant_category"] = merchant_category
            filtered.append(summary_dict)

            if len(filtered) >= limit:
                break

        # Calculate aggregates
        total_spending = sum(s["grand_total"] or 0 for s in filtered)
        total_tax = sum(s["tax"] or 0 for s in filtered)
        total_tip = sum(s["tip"] or 0 for s in filtered)
        receipts_with_totals = sum(1 for s in filtered if s["grand_total"])

        return {
            "count": len(filtered),
            "total_spending": round(total_spending, 2),
            "total_tax": round(total_tax, 2),
            "total_tip": round(total_tip, 2),
            "receipts_with_totals": receipts_with_totals,
            "average_receipt": (
                round(total_spending / receipts_with_totals, 2)
                if receipts_with_totals > 0
                else None
            ),
            "filters": {
                "merchant": merchant_filter,
                "category": category_filter,
                "start_date": start_date,
                "end_date": end_date,
            },
            "summaries": filtered,
        }

    except Exception as e:
        logger.exception("Error getting receipt summaries")
        return {"error": str(e)}


async def list_categories_impl(dynamo_client) -> dict:
    """List all merchant categories with receipt counts."""
    try:
        # Paginate through all ReceiptPlace records
        category_counts: dict[str, int] = defaultdict(int)
        last_key = None

        while True:
            places, last_key = dynamo_client.list_receipt_places(
                limit=1000,
                last_evaluated_key=last_key,
            )

            for place in places:
                if place.merchant_category:
                    category_counts[place.merchant_category] += 1

            if last_key is None:
                break

        # Sort by count descending
        sorted_categories = sorted(
            category_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        return {
            "total_categories": len(sorted_categories),
            "categories": [
                {"category": cat, "receipt_count": count}
                for cat, count in sorted_categories
            ],
        }

    except Exception as e:
        logger.exception("Error listing categories")
        return {"error": str(e)}


async def label_validation_summary_impl(dynamo_client) -> dict:
    """Get validation status counts for all core labels."""
    from receipt_dynamo.constants import CORE_LABELS

    try:
        summary: dict[str, dict[str, int]] = {}

        for label in CORE_LABELS:
            counts: dict[str, int] = defaultdict(int)
            last_key = None

            while True:
                records, last_key = dynamo_client.get_receipt_word_labels_by_label(
                    label=label,
                    limit=1000,
                    last_evaluated_key=last_key,
                )

                for record in records:
                    status = getattr(record, "validation_status", None) or "NONE"
                    counts[status] += 1

                if last_key is None:
                    break

            total = sum(counts.values())
            summary[label] = {
                "VALID": counts.get("VALID", 0),
                "INVALID": counts.get("INVALID", 0),
                "PENDING": counts.get("PENDING", 0),
                "NEEDS_REVIEW": counts.get("NEEDS_REVIEW", 0),
                "NONE": counts.get("NONE", 0),
                "total": total,
            }

        return {"labels": summary}

    except Exception as e:
        logger.exception("Error getting label validation summary")
        return {"error": str(e)}


async def list_words_by_label_impl(
    dynamo_client,
    label: str,
    status_filter: Optional[str] = None,
    sample_size: int = 50,
) -> dict:
    """List a sample of words for a specific label, optionally filtered by status."""
    try:
        all_words = []
        total = 0
        last_key = None

        while True:
            records, last_key = dynamo_client.get_receipt_word_labels_by_label(
                label=label,
                limit=1000,
                last_evaluated_key=last_key,
            )

            for record in records:
                status = getattr(record, "validation_status", None) or "NONE"
                if status_filter and status != status_filter:
                    continue
                total += 1
                if len(all_words) < sample_size:
                    all_words.append(record)

            if last_key is None:
                break

        sample = all_words

        words = []
        for record in sample:
            words.append({
                "text": getattr(record, "text", ""),
                "validation_status": getattr(record, "validation_status", None) or "NONE",
                "image_id": record.image_id,
                "receipt_id": record.receipt_id,
                "line_id": record.line_id,
                "word_id": record.word_id,
                "label_proposed_by": getattr(record, "label_proposed_by", None),
            })

        return {
            "label": label,
            "status_filter": status_filter,
            "total": total,
            "sample_size": len(words),
            "words": words,
        }

    except Exception as e:
        logger.exception("Error listing words by label")
        return {"error": str(e)}


async def validate_word_similarity_impl(
    chroma_client,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
) -> dict:
    """Validate a word's label using ChromaDB similarity search."""
    from receipt_dynamo.constants import CORE_LABELS

    MIN_SIMILARITY = 0.80
    MIN_MATCHES = 3
    CONSENSUS_THRESHOLD = 0.80

    try:
        # Build the word's ChromaDB ID
        chroma_id = (
            f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"
            f"#LINE#{line_id:05d}#WORD#{word_id:05d}"
        )

        # Get the word's embedding from Chroma
        words_collection = chroma_client.get_collection("words")
        result = words_collection.get(
            ids=[chroma_id],
            include=["embeddings", "metadatas"],
        )

        if not result["ids"]:
            return {"error": f"Word not found in Chroma: {chroma_id}"}

        embeddings = result.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            return {"error": "Word has no embedding"}

        embedding = embeddings[0]
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()

        word_meta = result["metadatas"][0] if result["metadatas"] else {}
        word_text = word_meta.get("text", "")
        merchant_name = word_meta.get("merchant_name", "")

        # Distance to similarity conversion (L2)
        def dist_to_sim(distance: float) -> float:
            return max(0.0, 1.0 - (distance / 2.0))

        # Query for positive evidence (validated words WITH this label)
        label_field = f"label_{label}"

        positive_results = words_collection.query(
            query_embeddings=[embedding],
            n_results=10,
            where={
                "$and": [
                    {"label_status": "validated"},
                    {label_field: True},
                ]
            },
            include=["metadatas", "distances"],
        )

        # Query for negative evidence (validated words WITHOUT this label)
        negative_results = words_collection.query(
            query_embeddings=[embedding],
            n_results=10,
            where={
                "$and": [
                    {"label_status": "validated"},
                    {label_field: False},
                ]
            },
            include=["metadatas", "distances"],
        )

        # Process results
        evidence_for = []
        evidence_against = []

        for metas, dists in [(positive_results.get("metadatas", [[]]), positive_results.get("distances", [[]]))]:
            for meta, dist in zip(metas[0] if metas else [], dists[0] if dists else []):
                sim = dist_to_sim(dist)
                if sim < MIN_SIMILARITY:
                    continue
                # Skip self
                rid = f"IMAGE#{meta.get('image_id', '')}#RECEIPT#{meta.get('receipt_id', 0):05d}#LINE#{meta.get('line_id', 0):05d}#WORD#{meta.get('word_id', 0):05d}"
                if rid == chroma_id:
                    continue
                evidence_for.append({
                    "text": meta.get("text", ""),
                    "similarity": round(sim, 3),
                    "merchant": meta.get("merchant_name", ""),
                    "same_merchant": meta.get("merchant_name", "") == merchant_name,
                })

        for metas, dists in [(negative_results.get("metadatas", [[]]), negative_results.get("distances", [[]]))]:
            for meta, dist in zip(metas[0] if metas else [], dists[0] if dists else []):
                sim = dist_to_sim(dist)
                if sim < MIN_SIMILARITY:
                    continue
                rid = f"IMAGE#{meta.get('image_id', '')}#RECEIPT#{meta.get('receipt_id', 0):05d}#LINE#{meta.get('line_id', 0):05d}#WORD#{meta.get('word_id', 0):05d}"
                if rid == chroma_id:
                    continue
                evidence_against.append({
                    "text": meta.get("text", ""),
                    "similarity": round(sim, 3),
                    "merchant": meta.get("merchant_name", ""),
                    "same_merchant": meta.get("merchant_name", "") == merchant_name,
                })

        total_matches = len(evidence_for) + len(evidence_against)

        if total_matches == 0:
            return {
                "word_text": word_text,
                "label": label,
                "recommended_status": "PENDING",
                "confidence": 0.0,
                "reason": f"No similar validated words found for {label}",
                "evidence_for": [],
                "evidence_against": [],
                "suggested_labels": [],
            }

        if total_matches < MIN_MATCHES:
            return {
                "word_text": word_text,
                "label": label,
                "recommended_status": "PENDING",
                "confidence": 0.0,
                "reason": f"Only {total_matches} matches (need {MIN_MATCHES})",
                "evidence_for": evidence_for,
                "evidence_against": evidence_against,
                "suggested_labels": [],
            }

        # Weighted consensus voting
        SAME_MERCHANT_BOOST = 0.10
        votes_for = 0.0
        votes_against = 0.0

        for ev in evidence_for:
            weight = ev["similarity"]
            if ev["same_merchant"]:
                weight = min(1.0, weight + SAME_MERCHANT_BOOST)
            votes_for += weight

        for ev in evidence_against:
            weight = ev["similarity"]
            if ev["same_merchant"]:
                weight = min(1.0, weight + SAME_MERCHANT_BOOST)
            votes_against += weight

        total_votes = votes_for + votes_against
        confidence = votes_for / total_votes if total_votes > 0 else 0.0

        # Map to ValidationStatus enum values (VALID, INVALID, NEEDS_REVIEW, PENDING)
        if confidence >= CONSENSUS_THRESHOLD:
            recommended_status = "VALID"
            reason = f"{confidence:.0%} of similar words validated as {label}"
        elif confidence <= (1.0 - CONSENSUS_THRESHOLD):
            recommended_status = "INVALID"
            reason = f"{1.0 - confidence:.0%} of similar words rejected {label}"
        else:
            recommended_status = "NEEDS_REVIEW"
            reason = f"Mixed evidence: {confidence:.0%} for, {1.0 - confidence:.0%} against"

        # Find suggested labels if invalid or uncertain
        suggested_labels = []
        if recommended_status in ("INVALID", "NEEDS_REVIEW"):
            for candidate_label in CORE_LABELS:
                if candidate_label == label:
                    continue
                try:
                    cand_field = f"label_{candidate_label}"
                    cand_results = words_collection.query(
                        query_embeddings=[embedding],
                        n_results=10,
                        where={
                            "$and": [
                                {"label_status": "validated"},
                                {cand_field: True},
                            ]
                        },
                        include=["distances"],
                    )
                    cand_dists = cand_results.get("distances", [[]])[0] if cand_results.get("distances") else []
                    cand_sims = [dist_to_sim(d) for d in cand_dists if dist_to_sim(d) >= MIN_SIMILARITY]
                    if cand_sims:
                        avg_sim = sum(cand_sims) / len(cand_sims)
                        score = len(cand_sims) * avg_sim
                        suggested_labels.append({
                            "label": candidate_label,
                            "match_count": len(cand_sims),
                            "avg_similarity": round(avg_sim, 3),
                            "score": round(score, 3),
                        })
                except Exception as e:
                    logger.warning("Error querying label %s (%s): %s", candidate_label, cand_field, e)
                    continue

            suggested_labels.sort(key=lambda x: x["score"], reverse=True)
            suggested_labels = suggested_labels[:5]

        return {
            "word_text": word_text,
            "label": label,
            "recommended_status": recommended_status,
            "confidence": round(confidence, 3),
            "reason": reason,
            "votes_for": round(votes_for, 3),
            "votes_against": round(votes_against, 3),
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
            "suggested_labels": suggested_labels,
        }

    except Exception as e:
        logger.exception("Error validating word similarity")
        return {"error": str(e)}


async def update_word_label_impl(
    dynamo_client,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    new_status: str,
    reasoning: Optional[str] = None,
) -> dict:
    """Update the validation_status of a ReceiptWordLabel record."""
    from receipt_dynamo.constants import ValidationStatus
    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

    try:
        # Validate new_status against the enum
        try:
            ValidationStatus(new_status)
        except ValueError:
            valid = [s.value for s in ValidationStatus]
            return {"error": f"Invalid status '{new_status}'. Must be one of: {valid}"}

        # Fetch existing record
        existing = dynamo_client.get_receipt_word_label(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=label,
        )

        # Use provided reasoning or keep existing
        final_reasoning = reasoning if reasoning is not None else existing.reasoning

        # Build updated entity (triggers __post_init__ validation)
        updated = ReceiptWordLabel(
            image_id=existing.image_id,
            receipt_id=existing.receipt_id,
            line_id=existing.line_id,
            word_id=existing.word_id,
            label=existing.label,
            reasoning=final_reasoning,
            timestamp_added=existing.timestamp_added,
            validation_status=new_status,
            label_proposed_by="mcp-claude-review",
            label_consolidated_from=existing.label_consolidated_from,
        )

        # Write back (condition: record must already exist)
        dynamo_client.update_receipt_word_label(updated)

        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "line_id": line_id,
            "word_id": word_id,
            "label": label,
            "old_status": getattr(existing, "validation_status", None) or "NONE",
            "new_status": new_status,
            "reasoning": final_reasoning,
            "label_proposed_by": "mcp-claude-review",
        }

    except Exception as e:
        logger.exception("Error updating word label")
        return {"error": str(e)}


async def get_receipt_words_impl(
    dynamo_client,
    image_id: str,
    receipt_id: int,
    line_id: Optional[int] = None,
) -> dict:
    """Get all words for a receipt with their coordinates and labels."""
    try:
        details = dynamo_client.get_receipt_details(image_id, receipt_id)

        # Build label lookup: (line_id, word_id) -> list of labels
        labels_by_word: dict[tuple[int, int], list] = defaultdict(list)
        for label in details.labels:
            labels_by_word[(label.line_id, label.word_id)].append(
                {
                    "label": label.label,
                    "validation_status": getattr(label, "validation_status", None) or "NONE",
                    "label_proposed_by": getattr(label, "label_proposed_by", None),
                }
            )

        # Build word list
        words = []
        for word in sorted(details.words, key=lambda w: (w.line_id, w.word_id)):
            if line_id is not None and word.line_id != line_id:
                continue
            word_labels = labels_by_word.get((word.line_id, word.word_id), [])
            words.append(
                {
                    "line_id": word.line_id,
                    "word_id": word.word_id,
                    "text": word.text,
                    "labels": word_labels if word_labels else None,
                }
            )

        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "line_id_filter": line_id,
            "word_count": len(words),
            "words": words,
        }

    except Exception as e:
        logger.exception("Error getting receipt words")
        return {"error": str(e)}


async def create_word_label_impl(
    dynamo_client,
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    label: str,
    reasoning: str,
    consolidated_from: Optional[str] = None,
    validation_status: str = "VALID",
) -> dict:
    """Create a new ReceiptWordLabel record in DynamoDB.

    validation_status defaults to VALID (a human-reviewed label via MCP). Pass
    PENDING for machine-propagated rows (e.g. SECTION_* sections) that still
    need QA before they count as ground truth.
    """
    from datetime import datetime, timezone

    from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

    try:
        normalized_label = label.upper()
        # Only the four workflow statuses are valid on create; a null/omitted
        # arg falls back to VALID. NONE is deliberately excluded so a stray
        # null can't silently persist a non-ground-truth row.
        normalized_status = str(validation_status or "VALID").upper()
        allowed = {"PENDING", "VALID", "INVALID", "NEEDS_REVIEW"}
        if normalized_status not in allowed:
            return {
                "error": (
                    f"Invalid validation_status {validation_status!r}; "
                    f"expected one of {sorted(allowed)}"
                )
            }
        new_label = ReceiptWordLabel(
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            label=normalized_label,
            reasoning=reasoning,
            timestamp_added=datetime.now(timezone.utc).isoformat(),
            validation_status=normalized_status,
            label_proposed_by="mcp-claude-review",
            label_consolidated_from=consolidated_from,
        )

        dynamo_client.add_receipt_word_label(new_label)

        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "line_id": line_id,
            "word_id": word_id,
            "label": normalized_label,
            "validation_status": normalized_status,
            "reasoning": reasoning,
            "label_proposed_by": "mcp-claude-review",
        }

    except Exception as e:
        logger.exception("Error creating word label")
        return {"error": str(e)}


async def list_recent_uploads_impl(dynamo_client, limit: int = 10) -> dict:
    """List the most recently uploaded images, sorted newest first."""
    from datetime import datetime, timezone

    def _to_utc_dt(ts) -> datetime:
        """Coerce timestamp to aware UTC datetime."""
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    try:
        limit = min(limit, 50)

        # Paginate through all Image entities
        all_images = []
        last_key = None
        while True:
            images, last_key = dynamo_client.list_images(
                limit=1000,
                last_evaluated_key=last_key,
            )
            all_images.extend(images)
            if last_key is None:
                break

        # Sort by timestamp_added descending
        all_images.sort(
            key=lambda img: _to_utc_dt(img.timestamp_added),
            reverse=True,
        )

        # Slice to limit
        recent = all_images[:limit]

        # For each recent image, get actual receipt IDs via GSI query
        all_indices = []
        receipts_by_image: dict[str, list[int]] = {}
        for img in recent:
            receipts = dynamo_client.get_receipts_from_image(img.image_id)
            receipt_ids = [r.receipt_id for r in receipts]
            receipts_by_image[img.image_id] = receipt_ids
            for rid in receipt_ids:
                all_indices.append((img.image_id, rid))

        # Batch-get all ReceiptPlace records in one call
        places_by_key: dict[str, str] = {}
        if all_indices:
            places = dynamo_client.get_receipt_places_by_indices(all_indices)
            for place in places:
                key = f"{place.image_id}_{place.receipt_id}"
                places_by_key[key] = place.merchant_name

        # Build results
        results = []
        for img in recent:
            receipt_ids = receipts_by_image.get(img.image_id, [])
            receipts_info = []
            for rid in receipt_ids:
                key = f"{img.image_id}_{rid}"
                receipts_info.append({
                    "receipt_id": rid,
                    "merchant_name": places_by_key.get(key),
                })

            results.append({
                "image_id": img.image_id,
                "timestamp_added": _to_utc_dt(img.timestamp_added).isoformat(
                    timespec="milliseconds"
                ),
                "image_type": img.image_type,
                "receipt_count": len(receipts_info),
                "width": img.width,
                "height": img.height,
                "receipts": receipts_info,
            })

        return {
            "total_images": len(all_images),
            "showing": len(results),
            "uploads": results,
        }

    except Exception as e:
        logger.exception("Error listing recent uploads")
        return {"error": str(e)}


async def _invoke_lambda(function_name: str, payload: dict) -> dict:
    """Invoke a Lambda function and return the parsed response payload."""
    import boto3

    logger.info(
        "Invoking %s with payload: %s",
        function_name,
        json.dumps(payload),
    )

    def _invoke():
        client = boto3.client("lambda", region_name="us-east-1")
        return client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )

    response = await asyncio.to_thread(_invoke)
    response_payload = json.loads(response["Payload"].read())

    if "FunctionError" in response:
        return {
            "error": "Lambda execution failed",
            "function_error": response["FunctionError"],
            "details": response_payload,
        }

    return response_payload


async def fix_place_impl(
    image_id: str,
    receipt_id: int,
    reason: str,
) -> dict:
    """Invoke the fix-place Lambda to correct a receipt's merchant/place."""
    try:
        env = os.environ.get("PORTFOLIO_ENV", "dev")
        return await _invoke_lambda(
            f"fix-place-{env}-fix-place",
            {"image_id": image_id, "receipt_id": receipt_id, "reason": reason},
        )
    except Exception as e:
        logger.exception("Error invoking fix-place Lambda")
        return {"error": str(e)}


async def merge_receipts_impl(
    image_id: str,
    receipt_ids: list[int],
    dry_run: bool = True,
) -> dict:
    """Invoke the merge-receipt Lambda to combine two receipt fragments."""
    try:
        env = os.environ.get("PORTFOLIO_ENV", "dev")
        return await _invoke_lambda(
            f"merge-receipt-{env}-merge-receipt",
            {"image_id": image_id, "receipt_ids": receipt_ids, "dry_run": dry_run},
        )
    except Exception as e:
        logger.exception("Error invoking merge-receipt Lambda")
        return {"error": str(e)}


def _receipt_point_to_image(
    rx: float, ry: float,
    coeffs: list[float],
    receipt_width: int, receipt_height: int,
    image_width: int, image_height: int,
) -> tuple[float, float]:
    """Projective forward transform: receipt-relative → full-image Vision.

    Uses the perspective coefficients (receipt-PIL → image-PIL) computed by
    ``find_perspective_coeffs``, matching the exact transform that the
    FIRST_PASS warp used.
    """
    # Receipt OCR normalised → PIL pixel
    x_rct = rx * (receipt_width - 1)
    y_rct = (1.0 - ry) * (receipt_height - 1)

    a, b, c, d, e, f, g, h = coeffs
    denom = 1.0 + g * x_rct + h * y_rct
    if abs(denom) < 1e-12:
        return 0.5, 0.5

    x_img = (a * x_rct + b * y_rct + c) / denom
    y_img = (d * x_rct + e * y_rct + f) / denom

    # Image PIL pixel → OCR normalised
    ix = x_img / image_width
    iy = 1.0 - (y_img / image_height)
    return max(0.0, min(1.0, ix)), max(0.0, min(1.0, iy))


async def compute_reocr_region_impl(
    dynamo_client,
    image_id: str,
    receipt_id: int,
    line_ids: list[int],
    padding: float = 0.05,
) -> dict:
    """Compute axis-aligned bounding box from receipt line IDs.

    Word bounding boxes are in receipt-relative space (normalised to the
    warped receipt paper).  The Swift crop needs full-image Vision
    coordinates, so we transform via the Receipt entity's four-corner
    perspective bounds.
    """
    try:
        if not isinstance(padding, (int, float)) or padding < 0 or padding > 1:
            return {"error": f"padding must be between 0 and 1, got {padding}"}

        details = dynamo_client.get_receipt_details(image_id, receipt_id)

        # Filter words to only those on the requested lines
        target_line_ids = set(line_ids)
        words_in_region = [
            w for w in details.words if w.line_id in target_line_ids
        ]

        if not words_in_region:
            return {
                "error": f"No words found on line_ids {line_ids}",
                "available_line_ids": sorted({w.line_id for w in details.words}),
            }

        # Compute axis-aligned bounding box in receipt-relative space
        min_x = min(w.bounding_box["x"] for w in words_in_region)
        min_y = min(w.bounding_box["y"] for w in words_in_region)
        max_x = max(
            w.bounding_box["x"] + w.bounding_box["width"]
            for w in words_in_region
        )
        max_y = max(
            w.bounding_box["y"] + w.bounding_box["height"]
            for w in words_in_region
        )

        # Always use full width — Vision OCR produces better results with
        # more horizontal context. Only constrain the vertical range.
        padded_x = 0.0
        padded_y = max(0.0, min_y - padding)
        padded_right = 1.0
        padded_top = min(1.0, max_y + padding)

        # Transform the four corners of the padded region from
        # receipt-relative space to full-image Vision coordinates using
        # the same projective (homography) transform that the FIRST_PASS
        # warp used.
        from receipt_upload.geometry.transformations import find_perspective_coeffs

        receipt = details.receipt
        image = dynamo_client.get_image(image_id)

        src_points = [
            (receipt.top_left["x"] * image.width,
             (1.0 - receipt.top_left["y"]) * image.height),
            (receipt.top_right["x"] * image.width,
             (1.0 - receipt.top_right["y"]) * image.height),
            (receipt.bottom_right["x"] * image.width,
             (1.0 - receipt.bottom_right["y"]) * image.height),
            (receipt.bottom_left["x"] * image.width,
             (1.0 - receipt.bottom_left["y"]) * image.height),
        ]
        dst_points = [
            (0.0, 0.0),
            (float(receipt.width - 1), 0.0),
            (float(receipt.width - 1), float(receipt.height - 1)),
            (0.0, float(receipt.height - 1)),
        ]
        coeffs = find_perspective_coeffs(src_points, dst_points)

        _pt = _receipt_point_to_image
        img_corners = [
            _pt(padded_x, padded_y, coeffs,
                receipt.width, receipt.height, image.width, image.height),
            _pt(padded_right, padded_y, coeffs,
                receipt.width, receipt.height, image.width, image.height),
            _pt(padded_x, padded_top, coeffs,
                receipt.width, receipt.height, image.width, image.height),
            _pt(padded_right, padded_top, coeffs,
                receipt.width, receipt.height, image.width, image.height),
        ]

        img_min_x = max(0.0, min(c[0] for c in img_corners))
        img_min_y = max(0.0, min(c[1] for c in img_corners))
        img_max_x = min(1.0, max(c[0] for c in img_corners))
        img_max_y = min(1.0, max(c[1] for c in img_corners))

        if img_max_x <= img_min_x or img_max_y <= img_min_y:
            return {
                "error": "Computed re-OCR region is empty after transform",
                "image_id": image_id,
                "receipt_id": receipt_id,
            }

        region = {
            "x": round(img_min_x, 6),
            "y": round(img_min_y, 6),
            "width": round(img_max_x - img_min_x, 6),
            "height": round(img_max_y - img_min_y, 6),
        }

        # Include context: which lines were found, word count
        found_line_ids = sorted(
            target_line_ids & {w.line_id for w in details.words}
        )
        missing_line_ids = sorted(
            target_line_ids - {w.line_id for w in details.words}
        )

        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "region": region,
            "lines_included": found_line_ids,
            "lines_missing": missing_line_ids,
            "word_count": len(words_in_region),
        }

    except Exception as e:
        logger.exception("Error computing re-OCR region")
        return {"error": str(e)}


async def trigger_reocr_impl(
    image_id: str,
    receipt_id: int,
    reocr_region: dict,
    reocr_reason: str = "manual_trigger",
) -> dict:
    """Invoke the trigger-reocr Lambda to start regional re-OCR."""
    try:
        env = os.environ.get("PORTFOLIO_ENV", "dev")
        return await _invoke_lambda(
            f"trigger-reocr-{env}-trigger-reocr",
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "reocr_region": reocr_region,
                "reocr_reason": reocr_reason,
            },
        )
    except Exception as e:
        logger.exception("Error invoking trigger-reocr Lambda")
        return {"error": str(e)}


async def get_receipt_image_url_impl(
    dynamo_client, image_id: str, receipt_id: int
) -> dict:
    """Build the CDN URL for a receipt image from DynamoDB record."""
    try:
        details = dynamo_client.get_receipt_details(image_id, receipt_id)
        receipt = details.receipt

        env = os.environ.get("PORTFOLIO_ENV", "dev")
        domain = "dev.tylernorlund.com" if env == "dev" else "tylernorlund.com"

        result: dict[str, Any] = {
            "image_id": image_id,
            "receipt_id": receipt_id,
        }

        if receipt.cdn_s3_key:
            result["url"] = f"https://{domain}/{receipt.cdn_s3_key}"
        else:
            result["url"] = None
            result["note"] = "No cdn_s3_key on receipt record"

        # Include all available CDN variants
        variants = {}
        if receipt.cdn_webp_s3_key:
            variants["webp"] = f"https://{domain}/{receipt.cdn_webp_s3_key}"
        if receipt.cdn_avif_s3_key:
            variants["avif"] = f"https://{domain}/{receipt.cdn_avif_s3_key}"
        if receipt.cdn_thumbnail_s3_key:
            variants["thumbnail"] = f"https://{domain}/{receipt.cdn_thumbnail_s3_key}"
        if receipt.cdn_small_s3_key:
            variants["small"] = f"https://{domain}/{receipt.cdn_small_s3_key}"
        if receipt.cdn_medium_s3_key:
            variants["medium"] = f"https://{domain}/{receipt.cdn_medium_s3_key}"
        if variants:
            result["variants"] = variants

        return result

    except Exception as e:
        return {"error": str(e)}


async def delete_image_impl(
    dynamo_client, image_id: str, dry_run: bool = True
) -> dict:
    """Delete all DynamoDB records under an image partition key."""
    try:
        details = dynamo_client.get_image_details(image_id)

        # Build type counts from the structured details
        type_counts: dict[str, int] = {}
        for attr in (
            "images",
            "lines",
            "words",
            "letters",
            "receipts",
            "receipt_lines",
            "receipt_words",
            "receipt_letters",
            "receipt_word_labels",
            "receipt_places",
            "ocr_jobs",
            "ocr_routing_decisions",
        ):
            items = getattr(details, attr, [])
            if items:
                type_counts[attr.upper()] = len(items)

        total = sum(type_counts.values())

        if total == 0:
            return {"error": f"No items found for image {image_id}"}

        breakdown = [
            {"entity_type": t, "count": c}
            for t, c in sorted(type_counts.items(), key=lambda x: -x[1])
        ]

        if dry_run:
            return {
                "image_id": image_id,
                "dry_run": True,
                "total_items": total,
                "breakdown": breakdown,
                "message": "Re-run with dry_run=false to delete",
            }

        # Delegate to the DynamoClient method which handles batch delete
        counts = dynamo_client.delete_image_details(image_id)
        deleted = sum(counts.values())

        # Use the actual counts from the delete for the breakdown
        breakdown = [
            {"entity_type": t, "count": c}
            for t, c in sorted(counts.items(), key=lambda x: -x[1])
        ]

        return {
            "image_id": image_id,
            "dry_run": False,
            "deleted": deleted,
            "breakdown": breakdown,
        }

    except Exception as e:
        logger.exception("Error deleting image")
        return {"error": str(e)}


async def delete_receipt_impl(
    dynamo_client, image_id: str, receipt_id: int, dry_run: bool = True
) -> dict:
    """Delete a single receipt and its children, keeping the rest of the image.

    Only the Receipt entity is deleted here; the enhanced compactor removes the
    ChromaDB embeddings and child records (lines, words, letters, labels, place)
    asynchronously via DynamoDB streams.
    """
    try:
        from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

        try:
            details = dynamo_client.get_receipt_details(image_id, receipt_id)
        except EntityNotFoundError:
            return {
                "error": (
                    f"Receipt {receipt_id} not found for image {image_id}"
                )
            }

        place = getattr(details, "place", None)
        merchant_name = (
            getattr(place, "merchant_name", None) if place else None
        )

        # Note: ReceiptLetters are excluded from the GSI4 query that backs
        # get_receipt_details, so they are not counted here. The compactor
        # still deletes them via DynamoDB streams.
        breakdown = {
            "RECEIPT": 1,
            "RECEIPT_LINES": len(details.lines or []),
            "RECEIPT_WORDS": len(details.words or []),
            "RECEIPT_WORD_LABELS": len(details.labels or []),
            "RECEIPT_PLACES": 1 if place else 0,
        }

        if dry_run:
            return {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "merchant_name": merchant_name,
                "dry_run": True,
                "breakdown": breakdown,
                "message": (
                    "Deletes the Receipt entity; the compactor then removes "
                    "ChromaDB embeddings and all child records via DynamoDB "
                    "streams. Re-run with dry_run=false to delete."
                ),
            }

        from receipt_agent.lifecycle.receipt_manager import (
            delete_receipt as delete_receipt_fn,
        )

        deletion = delete_receipt_fn(dynamo_client, image_id, receipt_id)
        if not deletion.success:
            return {
                "error": deletion.error or "Failed to delete receipt",
                "image_id": image_id,
                "receipt_id": receipt_id,
            }

        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "dry_run": False,
            "deleted": True,
            "breakdown": breakdown,
            "message": (
                "Receipt entity deleted. The enhanced compactor will remove "
                "ChromaDB embeddings and child records (lines, words, letters, "
                "labels, place) asynchronously via DynamoDB streams."
            ),
        }

    except Exception as e:
        logger.exception("Error deleting receipt")
        return {"error": str(e)}


async def get_receipt_sections_impl(
    dynamo_client, image_id: str, receipt_id: int
) -> dict:
    """List a receipt's sections with the text of each section's lines."""
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

    try:
        try:
            details = dynamo_client.get_receipt_details(image_id, receipt_id)
        except EntityNotFoundError:
            return {
                "error": (
                    f"Receipt {receipt_id} not found for image {image_id}"
                )
            }

        # line_id -> text so a reviewer can QA sections without a second call
        line_text = {line.line_id: line.text for line in details.lines or []}

        sections = dynamo_client.get_receipt_sections_from_receipt(
            image_id, receipt_id
        )

        section_dicts = []
        for section in sorted(
            sections,
            key=lambda s: (
                min(s.line_ids) if s.line_ids else 0,
                s.section_type,
            ),
        ):
            lines = [
                {"line_id": lid, "text": line_text.get(lid, "")}
                for lid in section.line_ids
            ]
            section_dicts.append(
                {
                    "section_type": section.section_type,
                    "line_ids": section.line_ids,
                    "confidence": section.confidence,
                    "model_source": section.model_source,
                    "validation_status": section.validation_status or "NONE",
                    "lines": lines,
                }
            )

        return {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "section_count": len(section_dicts),
            "sections": section_dicts,
        }

    except Exception as e:
        logger.exception("Error getting receipt sections")
        return {"error": str(e)}


def _normalize_section_type(section_type):
    """Strip/uppercase a section_type and validate it against SectionType.

    Returns (normalized_type, None) on success, or (None, error_dict) when
    the value is not in the SectionType enum — catching typos like "ITMES"
    before they become noncanonical SKs or misleading "not found" errors.
    """
    from receipt_dynamo.constants import SectionType

    normalized = str(section_type or "").strip().upper()
    valid_types = {t.value for t in SectionType}
    if normalized not in valid_types:
        return None, {
            "error": (
                f"Invalid section_type {section_type!r}; "
                f"expected one of {sorted(valid_types)}"
            )
        }
    return normalized, None


async def update_section_status_impl(
    dynamo_client,
    image_id: str,
    receipt_id: int,
    section_type: str,
    validation_status: str,
) -> dict:
    """Set the validation_status of an existing ReceiptSection."""
    from receipt_dynamo.constants import ValidationStatus
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    try:
        normalized_type, type_error = _normalize_section_type(section_type)
        if type_error:
            return type_error
        normalized_status = str(validation_status or "").upper()
        allowed = {s.value for s in ValidationStatus}
        if normalized_status not in allowed:
            return {
                "error": (
                    f"Invalid validation_status {validation_status!r}; "
                    f"expected one of {sorted(allowed)}"
                )
            }

        try:
            existing = dynamo_client.get_receipt_section(
                receipt_id=receipt_id,
                image_id=image_id,
                section_type=normalized_type,
            )
        except EntityNotFoundError:
            return {
                "error": (
                    f"Section {normalized_type!r} not found on receipt "
                    f"{receipt_id} (image {image_id}). Use "
                    "get_receipt_sections to list existing sections."
                )
            }

        old_status = existing.validation_status or "NONE"
        updated = ReceiptSection(
            receipt_id=existing.receipt_id,
            image_id=existing.image_id,
            section_type=existing.section_type,
            line_ids=existing.line_ids,
            created_at=existing.created_at,
            confidence=existing.confidence,
            model_source=existing.model_source,
            validation_status=normalized_status,
        )
        dynamo_client.update_receipt_section(updated)

        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "section_type": normalized_type,
            "old_status": old_status,
            "new_status": normalized_status,
        }

    except Exception as e:
        logger.exception("Error updating section status")
        return {"error": str(e)}


async def create_receipt_section_impl(
    dynamo_client,
    image_id: str,
    receipt_id: int,
    section_type: str,
    line_ids: list,
    validation_status: str = "VALID",
    model_source: str = "mcp-claude-review",
) -> dict:
    """Create a new ReceiptSection; fail if the section_type already exists."""
    from datetime import datetime, timezone

    from receipt_dynamo.constants import ValidationStatus
    from receipt_dynamo.data.shared_exceptions import (
        EntityAlreadyExistsError,
        EntityNotFoundError,
    )
    from receipt_dynamo.entities.receipt_section import ReceiptSection

    try:
        normalized_type, type_error = _normalize_section_type(section_type)
        if type_error:
            return type_error
        normalized_status = str(validation_status or "VALID").upper()
        allowed = {s.value for s in ValidationStatus}
        if normalized_status not in allowed:
            return {
                "error": (
                    f"Invalid validation_status {validation_status!r}; "
                    f"expected one of {sorted(allowed)}"
                )
            }

        try:
            normalized_line_ids = [int(lid) for lid in (line_ids or [])]
        except (TypeError, ValueError):
            return {"error": "line_ids must be a list of integers"}
        if not normalized_line_ids:
            return {"error": "line_ids must be a non-empty list of integers"}

        # Verify the receipt exists and every line_id belongs to it, so a
        # typo'd image_id/receipt_id/line_id can't persist an orphan section
        # or a section pointing at lines the receipt doesn't have.
        try:
            details = dynamo_client.get_receipt_details(image_id, receipt_id)
        except EntityNotFoundError:
            return {
                "error": (
                    f"Receipt {receipt_id} not found for image {image_id}; "
                    "refusing to create a section for a nonexistent receipt"
                )
            }
        receipt_line_ids = {line.line_id for line in details.lines or []}
        unknown_line_ids = sorted(
            set(normalized_line_ids) - receipt_line_ids
        )
        if unknown_line_ids:
            return {
                "error": (
                    f"line_ids {unknown_line_ids} do not exist on receipt "
                    f"{receipt_id} (image {image_id}). Valid line_ids: "
                    f"{sorted(receipt_line_ids)}"
                )
            }

        new_section = ReceiptSection(
            receipt_id=receipt_id,
            image_id=image_id,
            section_type=normalized_type,
            line_ids=normalized_line_ids,
            created_at=datetime.now(timezone.utc),
            model_source=model_source,
            validation_status=normalized_status,
        )

        try:
            dynamo_client.add_receipt_section(new_section)
        except EntityAlreadyExistsError:
            return {
                "error": (
                    f"Section {normalized_type!r} already exists on receipt "
                    f"{receipt_id} (image {image_id}). Use "
                    "update_section_status to change it, or "
                    "delete_receipt_section first."
                )
            }

        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "section_type": normalized_type,
            "line_ids": normalized_line_ids,
            "validation_status": normalized_status,
            "model_source": model_source,
        }

    except Exception as e:
        logger.exception("Error creating receipt section")
        return {"error": str(e)}


async def delete_receipt_section_impl(
    dynamo_client, image_id: str, receipt_id: int, section_type: str
) -> dict:
    """Delete a single ReceiptSection row from a receipt."""
    from receipt_dynamo.data.shared_exceptions import EntityNotFoundError

    try:
        normalized_type, type_error = _normalize_section_type(section_type)
        if type_error:
            return type_error
        try:
            dynamo_client.delete_receipt_section(
                receipt_id=receipt_id,
                image_id=image_id,
                section_type=normalized_type,
            )
        except EntityNotFoundError:
            return {
                "error": (
                    f"Section {normalized_type!r} not found on receipt "
                    f"{receipt_id} (image {image_id}). Use "
                    "get_receipt_sections to list existing sections."
                )
            }

        return {
            "success": True,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "section_type": normalized_type,
            "deleted": True,
        }

    except Exception as e:
        logger.exception("Error deleting receipt section")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Web analytics — Athena over CloudFront access logs.
# Glue table + workgroup are created by infra/components/web_analytics.
# All beacon parsing / WARP+bot classification / PT bucketing lives here in
# SQL so the logic stays version-controlled rather than in a Glue view.
# ---------------------------------------------------------------------------
ANALYTICS_DB = "portfolio_analytics"
ANALYTICS_WORKGROUP = "portfolio_analytics"
ANALYTICS_REGION = "us-east-1"



def _analytics_check_date(d: str) -> str:
    # Athena's ExecutionParameters bind predicate placeholders as integer
    # (varchar/date params fail), so the structured tools validate inputs
    # strictly and interpolate. Constraining the shape (YYYY-MM-DD) keeps
    # interpolation injection-safe.
    import re

    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d or ""):
        raise ValueError(f"date must be YYYY-MM-DD, got {d!r}")
    return d


def _athena_run(sql: str, max_wait: int = 90, max_rows: int = 5000) -> list:
    """Run a query in the analytics workgroup; return rows as list[dict]."""
    import time

    import boto3

    ath = boto3.client("athena", region_name=ANALYTICS_REGION)
    qid = ath.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": ANALYTICS_DB},
        WorkGroup=ANALYTICS_WORKGROUP,
    )["QueryExecutionId"]
    waited = 0.0
    while True:
        status = ath.get_query_execution(QueryExecutionId=qid)[
            "QueryExecution"
        ]["Status"]
        state = status["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1.0)
        waited += 1.0
        if waited > max_wait:
            ath.stop_query_execution(QueryExecutionId=qid)
            raise TimeoutError(f"Athena query timed out after {max_wait}s")
    if state != "SUCCEEDED":
        raise RuntimeError(status.get("StateChangeReason", "query failed"))
    rows, header, token = [], None, None
    while True:
        kwargs = {"QueryExecutionId": qid, "MaxResults": 1000}
        if token:
            kwargs["NextToken"] = token
        resp = ath.get_query_results(**kwargs)
        for row in resp["ResultSet"]["Rows"]:
            vals = [cell.get("VarCharValue") for cell in row["Data"]]
            if header is None:
                header = vals
                continue
            rows.append(dict(zip(header, vals)))
        if len(rows) >= max_rows:
            break
        token = resp.get("NextToken")
        if not token:
            break
    return rows


def _analytics_base_cte(start_date: str, end_date: str) -> str:
    """Shared CTE over the curated web_events table (already parsed/classified
    by the transform Lambda). Partition-pruned by the UTC `dt` column; widened a
    day on each side so PT-day bucketing at the edges stays correct.
    """
    s = _analytics_check_date(start_date)
    e = _analytics_check_date(end_date)
    pt = "(ts_utc AT TIME ZONE 'UTC') AT TIME ZONE 'America/Los_Angeles'"
    return f"""
WITH base AS (
  SELECT
    date_format({pt}, '%Y-%m-%d') AS pt_date,
    date_format({pt}, '%Y-%m-%d %H:%i:%s') AS pt_time,
    request_ip, uri, status, referrer AS ref,
    is_beacon, event, evt_path, sid, is_warp, is_bot,
    org, city, country, is_hosting
  FROM {ANALYTICS_DB}.web_events
  WHERE dt BETWEEN date_format(date_add('day', -1, DATE '{s}'), '%Y-%m-%d')
              AND date_format(date_add('day',  1, DATE '{e}'), '%Y-%m-%d')
)"""


async def analytics_traffic_impl(start_date: str, end_date: str) -> dict:
    try:
        s = _analytics_check_date(start_date)
        e = _analytics_check_date(end_date)
        sql = _analytics_base_cte(s, e) + f"""
SELECT pt_date,
  count(*) AS requests_total,
  count_if(NOT is_bot AND NOT is_warp) AS requests_outside_human,
  count_if(is_warp) AS requests_warp,
  count_if(is_bot) AS requests_bot,
  count(DISTINCT IF(is_beacon AND NOT is_bot AND NOT is_warp AND sid <> '', sid, NULL)) AS human_sessions,
  count_if(is_beacon AND event = 'page_view' AND NOT is_bot AND NOT is_warp) AS human_pageviews
FROM base
WHERE pt_date BETWEEN '{s}' AND '{e}'
GROUP BY pt_date
ORDER BY pt_date
"""
        return {
            "start": s,
            "end": e,
            "timezone": "America/Los_Angeles",
            "days": _athena_run(sql),
        }
    except Exception as exc:
        logger.exception("analytics_traffic failed")
        return {"error": str(exc)}


async def analytics_sessions_impl(
    start_date, end_date, humans_only=True, limit=50
) -> dict:
    try:
        s = _analytics_check_date(start_date)
        e = _analytics_check_date(end_date)
        limit = max(1, min(int(limit), 500))
        flt = "AND NOT is_bot AND NOT is_warp" if humans_only else ""
        sql = _analytics_base_cte(s, e) + f"""
SELECT sid,
  min(pt_time) AS first_seen,
  max(pt_time) AS last_seen,
  arbitrary(request_ip) AS ip,
  arbitrary(org) AS org,
  arbitrary(city) AS city,
  arbitrary(country) AS country,
  bool_or(is_hosting) AS is_hosting,
  array_join(array_agg(DISTINCT evt_path) FILTER (WHERE evt_path <> ''), ', ') AS pages,
  count_if(event = 'page_view') AS pageviews,
  count_if(event = 'scroll_depth') AS scroll_events,
  count_if(event = 'reader_summary') AS reader_summaries,
  count(*) AS beacons
FROM base
WHERE is_beacon AND sid <> '' AND pt_date BETWEEN '{s}' AND '{e}' {flt}
GROUP BY sid
ORDER BY beacons DESC
LIMIT {limit}
"""
        return {
            "start": s,
            "end": e,
            "humans_only": humans_only,
            "sessions": _athena_run(sql),
        }
    except Exception as exc:
        logger.exception("analytics_sessions failed")
        return {"error": str(exc)}


async def analytics_top_impl(
    dimension, start_date, end_date, limit=15, humans_only=True
) -> dict:
    try:
        s = _analytics_check_date(start_date)
        e = _analytics_check_date(end_date)
        limit = max(1, min(int(limit), 200))
        flt = "AND NOT is_bot AND NOT is_warp" if humans_only else ""
        if dimension == "page":
            col = "evt_path"
            extra = "AND is_beacon AND event = 'page_view' AND evt_path <> ''"
        elif dimension == "referrer":
            col = "ref"
            extra = "AND ref <> '' AND ref <> '-' AND ref NOT LIKE '%tylernorlund%'"
        elif dimension == "ip":
            col = "request_ip"
            extra = ""
        elif dimension == "country":
            col = "country"
            extra = "AND country IS NOT NULL"
        elif dimension == "org":
            col = "org"
            extra = "AND org IS NOT NULL"
        else:
            return {"error": "dimension must be: page, referrer, ip, country, org"}
        sql = _analytics_base_cte(s, e) + f"""
SELECT {col} AS value, count(*) AS hits, count(DISTINCT IF(sid <> '', sid, NULL)) AS sessions
FROM base
WHERE pt_date BETWEEN '{s}' AND '{e}' {extra} {flt}
GROUP BY {col}
ORDER BY hits DESC
LIMIT {limit}
"""
        return {"dimension": dimension, "start": s, "end": e, "top": _athena_run(sql)}
    except Exception as exc:
        logger.exception("analytics_top failed")
        return {"error": str(exc)}


async def analytics_ip_impl(ip, start_date, end_date) -> dict:
    try:
        import re

        if not re.match(r"^[0-9a-fA-F:.]+$", ip or ""):
            return {"error": f"invalid ip: {ip!r}"}
        s = _analytics_check_date(start_date)
        e = _analytics_check_date(end_date)
        sql = _analytics_base_cte(s, e) + f"""
SELECT pt_time, uri, status, ref,
  IF(is_beacon, event, NULL) AS event,
  IF(is_beacon, evt_path, NULL) AS evt_path,
  is_warp, is_bot
FROM base
WHERE request_ip = '{ip}' AND pt_date BETWEEN '{s}' AND '{e}'
  AND uri NOT LIKE '/_next/%' AND uri NOT LIKE '/assets/%'
  AND NOT regexp_like(uri, '[.](js|css|woff2?|png|jpe?g|svg|ico|avif|webp|map)$')
ORDER BY pt_time
LIMIT 300
"""
        return {
            "ip": ip,
            "start": s,
            "end": e,
            "note": "org/geo not included; look up separately",
            "requests": _athena_run(sql),
        }
    except Exception as exc:
        logger.exception("analytics_ip failed")
        return {"error": str(exc)}


async def analytics_query_impl(sql: str) -> dict:
    """Run an ad-hoc read-only analytics query.

    Allowlist: a single SELECT/WITH statement. The real guarantee is least
    privilege, not string matching — the MCP role grants only read-only Athena +
    Glue on the analytics database and S3 read on the analytics buckets, so a
    query physically cannot write, run DDL, or read data outside this database
    regardless of its text.
    """
    try:
        stmt = (sql or "").strip().rstrip(";").strip()
        if ";" in stmt:
            return {"error": "only a single statement is allowed"}
        if not stmt.lower().startswith(("select", "with")):
            return {"error": "only read-only SELECT/WITH queries are allowed"}
        return {"rows": _athena_run(stmt), "sql": stmt}
    except Exception as exc:
        logger.exception("analytics_query failed")
        return {"error": str(exc)}


async def analytics_ga_impl(start_date: str, end_date: str) -> dict:
    try:
        s = _analytics_check_date(start_date)
        e = _analytics_check_date(end_date)
        sql = f"""
SELECT dt, sessions, total_users, new_users, pageviews, engaged_sessions
FROM {ANALYTICS_DB}.ga_daily
WHERE dt BETWEEN '{s}' AND '{e}'
ORDER BY dt
"""
        return {
            "start": s,
            "end": e,
            "timezone": "America/Los_Angeles (GA property TZ)",
            "days": _athena_run(sql),
        }
    except Exception as exc:
        logger.exception("analytics_ga failed")
        return {"error": str(exc)}


async def analytics_reconcile_impl(start_date: str, end_date: str) -> dict:
    try:
        s = _analytics_check_date(start_date)
        e = _analytics_check_date(end_date)
        pt = "(ts_utc AT TIME ZONE 'UTC') AT TIME ZONE 'America/Los_Angeles'"
        sql = f"""
WITH beacon AS (
  SELECT
    date_format({pt}, '%Y-%m-%d') AS pt_date,
    count(DISTINCT IF(
      is_beacon AND NOT is_bot AND NOT is_warp AND sid <> '', sid, NULL
    )) AS beacon_human_sessions,
    count_if(
      is_beacon AND event = 'page_view' AND NOT is_bot AND NOT is_warp
    ) AS beacon_pageviews
  FROM {ANALYTICS_DB}.web_events
  WHERE dt BETWEEN date_format(date_add('day', -1, DATE '{s}'), '%Y-%m-%d')
              AND date_format(date_add('day',  1, DATE '{e}'), '%Y-%m-%d')
  GROUP BY 1
)
SELECT
  coalesce(g.dt, b.pt_date) AS dt,
  g.sessions AS ga_sessions,
  g.engaged_sessions AS ga_engaged,
  g.pageviews AS ga_pageviews,
  b.beacon_human_sessions,
  b.beacon_pageviews
FROM {ANALYTICS_DB}.ga_daily g
FULL OUTER JOIN beacon b ON g.dt = b.pt_date
WHERE coalesce(g.dt, b.pt_date) BETWEEN '{s}' AND '{e}'
ORDER BY 1
"""
        return {
            "start": s,
            "end": e,
            "note": (
                "GA = Google bot-filtered but adblock-leaky; beacon = "
                "first-party humans (bots + WARP excluded)."
            ),
            "days": _athena_run(sql),
        }
    except Exception as exc:
        logger.exception("analytics_reconcile failed")
        return {"error": str(exc)}


async def analytics_github_impl(start_date: str, end_date: str) -> dict:
    """GitHub repo traffic (snapshotted past the API's 14-day window)."""
    try:
        s = _analytics_check_date(start_date)
        e = _analytics_check_date(end_date)
        daily_sql = f"""
SELECT event_day AS dt, repo,
  sum(IF(metric = 'views', cnt, 0)) AS views,
  sum(IF(metric = 'views', uniques, 0)) AS view_uniques,
  sum(IF(metric = 'clones', cnt, 0)) AS clones,
  sum(IF(metric = 'clones', uniques, 0)) AS clone_uniques
FROM {ANALYTICS_DB}.github_traffic
WHERE metric IN ('views', 'clones') AND event_day BETWEEN '{s}' AND '{e}'
GROUP BY event_day, repo
ORDER BY event_day
"""
        # Referrers/paths are 14-day aggregates; show the most recent snapshot.
        agg_sql = f"""
WITH latest AS (
  SELECT repo, metric, max(snapshot_date) AS snap
  FROM {ANALYTICS_DB}.github_traffic
  WHERE metric IN ('referrer', 'path')
  GROUP BY repo, metric
)
SELECT g.repo, g.metric, g.item, g.cnt, g.uniques, g.snapshot_date
FROM {ANALYTICS_DB}.github_traffic g
JOIN latest l
  ON g.repo = l.repo AND g.metric = l.metric AND g.snapshot_date = l.snap
WHERE g.item <> ''
ORDER BY g.metric, g.cnt DESC
"""
        agg = _athena_run(agg_sql)
        return {
            "start": s,
            "end": e,
            "note": (
                "Clones are mostly CI/bot noise; referrers + views are the "
                "human-traffic signal. Referrers/paths reflect the latest "
                "14-day snapshot."
            ),
            "daily": _athena_run(daily_sql),
            "referrers": [r for r in agg if r.get("metric") == "referrer"],
            "paths": [r for r in agg if r.get("metric") == "path"],
        }
    except Exception as exc:
        logger.exception("analytics_github failed")
        return {"error": str(exc)}


async def list_training_jobs_impl(
    dynamo_client,
    limit: int = 20,
    status_filter: Optional[str] = None,
) -> dict:
    """List LayoutLM training jobs with F1 scores, hyperparameters, and status."""
    try:
        if status_filter:
            jobs, _ = dynamo_client.list_jobs_by_status(
                status=status_filter,
            )
        else:
            jobs, _ = dynamo_client.list_jobs()

        results = []
        for job in sorted(
            jobs,
            key=lambda j: str(j.created_at) if j.created_at else "",
            reverse=True,
        ):
            r = job.results or {}
            if isinstance(r, str):
                r = json.loads(r)
            config = job.job_config or {}
            if isinstance(config, str):
                config = json.loads(config)
            results.append({
                "name": job.name,
                "status": job.status,
                "created_at": str(job.created_at) if job.created_at else None,
                "best_f1": r.get("best_f1"),
                "best_epoch": r.get("best_epoch"),
                "num_train_samples": r.get("num_train_samples"),
                "num_val_samples": r.get("num_val_samples"),
                "learning_rate": config.get("learning_rate"),
                "epochs": config.get("epochs"),
                "batch_size": config.get("batch_size"),
                "merge_amounts": config.get("merge_amounts"),
                "early_stopping_patience": config.get("early_stopping_patience"),
            })

        return {
            "total": len(results),
            "jobs": results[:limit],
        }

    except Exception as e:
        logger.exception("Error listing training jobs")
        return {"error": str(e)}


async def get_training_metrics_impl(
    dynamo_client,
    job_name: str,
) -> dict:
    """Get epoch-by-epoch training metrics for a specific training job."""
    try:
        jobs, _ = dynamo_client.get_job_by_name(job_name)
        if not jobs:
            return {"error": f"No job found with name: {job_name}"}
        job = jobs[0]

        metrics, _ = dynamo_client.list_job_metrics(job.job_id)

        # Group by epoch, filter to eval and training metrics
        by_epoch: dict[int, dict[str, Any]] = {}
        for m in metrics:
            if (
                m.metric_name.startswith("eval_")
                or m.metric_name in ("loss", "learning_rate")
            ):
                epoch = m.epoch if m.epoch is not None else 0
                if epoch not in by_epoch:
                    by_epoch[epoch] = {}
                by_epoch[epoch][m.metric_name] = m.value

        return {
            "job_name": job.name,
            "job_id": job.job_id,
            "status": job.status,
            "total_metrics": len(metrics),
            "epochs": dict(sorted(by_epoch.items())),
        }

    except Exception as e:
        logger.exception("Error getting training metrics")
        return {"error": str(e)}


async def get_active_model_impl(dynamo_client) -> dict:
    """Show which model is marked as active for inference."""
    try:
        active = dynamo_client.get_active_model_job()
        if active:
            r = active.results or {}
            if isinstance(r, str):
                r = json.loads(r)
            return {
                "name": active.name,
                "job_id": active.job_id,
                "best_f1": r.get("best_f1"),
                "best_checkpoint_s3_path": r.get("best_checkpoint_s3_path"),
                "tags": active.tags,
            }
        else:
            return {"error": "No active model job found"}

    except Exception as e:
        logger.exception("Error getting active model")
        return {"error": str(e)}


async def set_active_model_impl(
    dynamo_client,
    job_name: str,
) -> dict:
    """Mark a training job as the active model for inference services."""
    try:
        # Find the job
        jobs, _ = dynamo_client.get_job_by_name(job_name)
        if not jobs:
            return {"error": f"No job found with name: {job_name}"}
        job = jobs[0]

        # Clear old active model tag
        old_active = dynamo_client.get_active_model_job()
        if old_active:
            old_active.tags = {
                k: v
                for k, v in (old_active.tags or {}).items()
                if k != "active_model"
            }
            dynamo_client.update_job(old_active)

        # Set new active model
        job.tags = {**(job.tags or {}), "active_model": "true"}
        dynamo_client.update_job(job)

        r = job.results or {}
        if isinstance(r, str):
            r = json.loads(r)

        return {
            "success": True,
            "name": job.name,
            "job_id": job.job_id,
            "best_f1": r.get("best_f1"),
            "message": f"Set {job.name} as the active model",
        }

    except Exception as e:
        logger.exception("Error setting active model")
        return {"error": str(e)}


async def get_label_distribution_impl(dynamo_client) -> dict:
    """Show the distribution of word labels in the training dataset."""
    try:
        from receipt_dynamo.constants import CORE_LABELS

        summary: dict[str, dict[str, int]] = {}

        for label in CORE_LABELS:
            counts: dict[str, int] = defaultdict(int)
            last_key = None

            while True:
                records, last_key = dynamo_client.get_receipt_word_labels_by_label(
                    label=label,
                    limit=1000,
                    last_evaluated_key=last_key,
                )

                for record in records:
                    status = getattr(record, "validation_status", None) or "NONE"
                    counts[status] += 1

                if last_key is None:
                    break

            total = sum(counts.values())
            summary[label] = {
                "VALID": counts.get("VALID", 0),
                "INVALID": counts.get("INVALID", 0),
                "PENDING": counts.get("PENDING", 0),
                "NEEDS_REVIEW": counts.get("NEEDS_REVIEW", 0),
                "NONE": counts.get("NONE", 0),
                "total": total,
            }

        # Compute grand totals
        grand_total = sum(s["total"] for s in summary.values())
        grand_valid = sum(s["VALID"] for s in summary.values())

        return {
            "labels": summary,
            "grand_total": grand_total,
            "grand_valid": grand_valid,
            "label_count": len(summary),
        }

    except Exception as e:
        logger.exception("Error getting label distribution")
        return {"error": str(e)}


async def main():
    """Run the MCP server."""
    logger.info("Starting Receipt MCP Server...")

    # Pre-initialize clients
    try:
        get_clients()
    except Exception as e:
        logger.error("Failed to initialize clients: %s", e)
        # Continue anyway - will retry on first tool call

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
