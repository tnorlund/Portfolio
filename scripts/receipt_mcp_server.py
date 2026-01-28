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
  Line 0: TRADER[MERCHANT_NAME] JOE'S[MERCHANT_NAME]
  Line 5: ORGANIC[PRODUCT_NAME] COFFEE[PRODUCT_NAME] 12.99[LINE_TOTAL]
  Line 8: TAX 0.84[TAX]
  Line 9: TOTAL 13.83[GRAND_TOTAL]

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

        # Format as text with inline labels
        formatted_lines = []
        for i, line in enumerate(visual_lines):
            line_parts = []
            for w in line:
                if w["label"]:
                    line_parts.append(f"{w['text']}[{w['label']}]")
                else:
                    line_parts.append(w["text"])
            formatted_lines.append(f"Line {i}: {' '.join(line_parts)}")

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
