"""ETL pipeline: DynamoDB -> Neo4j.

Scans ReceiptSummaryRecord and ReceiptPlace entities from DynamoDB,
then MERGEs nodes and relationships into Neo4j in batches.

Usage:
    from neo4j import GraphDatabase
    from receipt_dynamo.data import DynamoClient
    from receipt_neo4j import ensure_schema, populate_graph

    driver = GraphDatabase.driver(uri, auth=(user, password))
    dynamo = DynamoClient(table_name="receipts")

    ensure_schema(driver)
    stats = populate_graph(driver, dynamo)
    print(stats)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from neo4j import Driver

    from receipt_dynamo.entities.receipt_place import ReceiptPlace
    from receipt_dynamo.entities.receipt_summary_record import (
        ReceiptSummaryRecord,
    )

logger = logging.getLogger(__name__)

# Day-of-week names indexed 1=Monday .. 7=Sunday (ISO)
_DAY_NAMES = [
    "",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

_MONTH_NAMES = [
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _normalize_merchant_name(name: str) -> str:
    """Normalize merchant name: uppercase, special chars to underscore."""
    normalized = name.upper()
    normalized = re.sub(r"[^A-Z0-9]+", "_", normalized)
    return normalized.strip("_")


def _category_display(name: str) -> str:
    """Convert category slug to display name.

    'grocery_store' -> 'Grocery Store'
    """
    return name.replace("_", " ").title()


def _collect_summaries(
    dynamo_client: Any,
) -> list["ReceiptSummaryRecord"]:
    """Scan all ReceiptSummaryRecord entities from DynamoDB."""
    all_records: list[Any] = []
    last_key = None
    while True:
        records, last_key = dynamo_client.list_receipt_summaries(
            limit=1000,
            last_evaluated_key=last_key,
        )
        all_records.extend(records)
        if last_key is None:
            break
    logger.info("Loaded %d receipt summaries from DynamoDB", len(all_records))
    return all_records


def _collect_places(
    dynamo_client: Any,
) -> dict[str, "ReceiptPlace"]:
    """Scan all ReceiptPlace entities keyed by image_id:receipt_id."""
    places: dict[str, Any] = {}
    last_key = None
    while True:
        records, last_key = dynamo_client.list_receipt_places(
            limit=1000,
            last_evaluated_key=last_key,
        )
        for place in records:
            key = f"{place.image_id}:{place.receipt_id:05d}"
            places[key] = place
        if last_key is None:
            break
    logger.info("Loaded %d receipt places from DynamoDB", len(places))
    return places


def _collect_line_items(
    dynamo_client: Any,
    image_id: str,
    receipt_id: int,
) -> list[dict[str, Any]]:
    """Extract line items from receipt details.

    Fetches word-level data and groups PRODUCT_NAME / LINE_TOTAL
    labels into line items.
    """
    try:
        details = dynamo_client.get_receipt_details(
            image_id, receipt_id
        )
    except Exception:
        logger.debug(
            "Could not fetch details for %s:%d",
            image_id,
            receipt_id,
        )
        return []

    # Build label lookup: (line_id, word_id) -> label
    label_lookup: dict[tuple[int, int], str] = {}
    for lbl in details.labels:
        key = (lbl.line_id, lbl.word_id)
        # Prefer VALID labels, else latest
        existing = label_lookup.get(key)
        if existing is None:
            label_lookup[key] = lbl.label
        elif (
            getattr(lbl, "validation_status", "") == "VALID"
        ):
            label_lookup[key] = lbl.label

    # Build word text lookup
    word_text: dict[tuple[int, int], str] = {}
    word_x: dict[tuple[int, int], float] = {}
    for w in details.words:
        wkey = (w.line_id, w.word_id)
        word_text[wkey] = w.text
        centroid = w.calculate_centroid()
        word_x[wkey] = centroid[0]

    # Group by visual line (line_id)
    lines: dict[int, list[tuple[int, str, str | None, float]]] = {}
    for (line_id, word_id), text in word_text.items():
        label = label_lookup.get((line_id, word_id))
        x = word_x.get((line_id, word_id), 0.0)
        lines.setdefault(line_id, []).append(
            (word_id, text, label, x)
        )

    # For each line with LINE_TOTAL, extract product + amount
    items: list[dict[str, Any]] = []
    for line_id in sorted(lines.keys()):
        words = sorted(lines[line_id], key=lambda w: w[3])
        line_total = None
        quantity = None
        unit_price = None
        product_parts: list[str] = []

        for _wid, text, label, _x in words:
            if label == "LINE_TOTAL":
                try:
                    line_total = float(
                        text.replace("$", "").replace(",", "")
                    )
                except ValueError:
                    pass
            elif label == "QUANTITY":
                try:
                    quantity = float(
                        text.replace("$", "").replace(",", "")
                    )
                except ValueError:
                    pass
            elif label == "UNIT_PRICE":
                try:
                    unit_price = float(
                        text.replace("$", "").replace(",", "")
                    )
                except ValueError:
                    pass
            elif label == "PRODUCT_NAME":
                product_parts.append(text)
            elif label is None:
                # Unlabeled word — likely part of product name
                product_parts.append(text)

        product_name = " ".join(product_parts).strip()
        if not product_name:
            continue
        if line_total is None and unit_price is None:
            continue

        items.append(
            {
                "product_name": product_name,
                "amount": line_total,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_idx": line_id,
                "text_lower": product_name.lower(),
            }
        )

    return items


def _merge_temporal(
    tx: Any,
    dt: datetime,
) -> None:
    """MERGE Date, Month, Year nodes and relationships."""
    iso_date = dt.strftime("%Y-%m-%d")
    day = dt.day
    # Python weekday: 0=Mon, isoweekday: 1=Mon
    dow = dt.isoweekday()
    day_name = _DAY_NAMES[dow]
    month = dt.month
    year = dt.year
    month_name = _MONTH_NAMES[month]

    tx.run(
        """
        MERGE (y:Year {year: $year})
        MERGE (mo:Month {year: $year, month: $month})
        SET mo.name = $month_name
        MERGE (mo)-[:IN_YEAR]->(y)
        MERGE (d:Date {date: date($iso_date)})
        SET d.day = $day,
            d.day_of_week = $dow,
            d.day_name = $day_name
        MERGE (d)-[:IN_MONTH]->(mo)
        """,
        year=year,
        month=month,
        month_name=month_name,
        iso_date=iso_date,
        day=day,
        dow=dow,
        day_name=day_name,
    )


def _merge_receipt_batch(
    tx: Any,
    batch: list[dict[str, Any]],
) -> None:
    """MERGE a batch of Receipt nodes."""
    tx.run(
        """
        UNWIND $batch AS row
        MERGE (r:Receipt {receipt_key: row.receipt_key})
        SET r.image_id = row.image_id,
            r.receipt_id = row.receipt_id,
            r.merchant_name = row.merchant_name,
            r.grand_total = row.grand_total,
            r.subtotal = row.subtotal,
            r.tax = row.tax,
            r.tip = row.tip,
            r.item_count = row.item_count,
            r.date = CASE WHEN row.date_str IS NOT NULL
                     THEN date(row.date_str) ELSE null END,
            r.date_str = row.date_str
        """,
        batch=batch,
    )


def _merge_merchant_and_rels(
    tx: Any,
    batch: list[dict[str, Any]],
) -> None:
    """MERGE Merchant, Category, Place nodes and relationships."""
    tx.run(
        """
        UNWIND $batch AS row
        MERGE (m:Merchant {normalized_name: row.normalized_name})
        SET m.name = row.merchant_name,
            m.primary_category = row.primary_category,
            m.phone_number = row.phone_number,
            m.website = row.website

        WITH m, row
        WHERE row.receipt_key IS NOT NULL
        MATCH (r:Receipt {receipt_key: row.receipt_key})
        MERGE (r)-[:SOLD_BY]->(m)

        WITH m, row
        WHERE row.primary_category IS NOT NULL
            AND row.primary_category <> ''
        MERGE (c:Category {name: row.primary_category})
        SET c.display_name = row.category_display
        MERGE (m)-[:IN_CATEGORY {is_primary: true}]->(c)

        WITH m, row
        WHERE row.place_id IS NOT NULL
            AND row.place_id <> ''
        MERGE (p:Place {place_id: row.place_id})
        SET p.formatted_address = row.formatted_address,
            p.latitude = row.latitude,
            p.longitude = row.longitude,
            p.business_status = row.business_status,
            p.phone_number = row.phone_number
        MERGE (m)-[:LOCATED_AT]->(p)
        """,
        batch=batch,
    )


def _merge_date_rels(
    tx: Any,
    batch: list[dict[str, Any]],
) -> None:
    """Create ON_DATE relationships for receipts with dates."""
    tx.run(
        """
        UNWIND $batch AS row
        MATCH (r:Receipt {receipt_key: row.receipt_key})
        MATCH (d:Date {date: date(row.date_str)})
        MERGE (r)-[:ON_DATE]->(d)
        """,
        batch=batch,
    )


def _merge_line_items(
    tx: Any,
    receipt_key: str,
    items: list[dict[str, Any]],
) -> None:
    """CREATE LineItem nodes and HAS_ITEM relationships."""
    tx.run(
        """
        MATCH (r:Receipt {receipt_key: $receipt_key})
        WITH r
        UNWIND $items AS item
        CREATE (li:LineItem {
            product_name: item.product_name,
            amount: item.amount,
            quantity: item.quantity,
            unit_price: item.unit_price,
            line_idx: item.line_idx,
            text_lower: item.text_lower
        })
        CREATE (r)-[:HAS_ITEM {position: item.line_idx}]->(li)
        """,
        receipt_key=receipt_key,
        items=items,
    )


def populate_graph(
    driver: "Driver",
    dynamo_client: Any,
    batch_size: int = 100,
    fetch_line_items: bool = True,
    database: str = "neo4j",
) -> dict[str, int]:
    """Populate Neo4j from DynamoDB data.

    Args:
        driver: Neo4j driver instance.
        dynamo_client: DynamoClient with list_receipt_summaries,
            list_receipt_places, and get_receipt_details methods.
        batch_size: Number of records per UNWIND batch.
        fetch_line_items: Whether to fetch word-level details for
            line items (slower but enables product search).
        database: Neo4j database name.

    Returns:
        Dict with counts: {receipts, merchants, places, categories,
        dates, line_items}.
    """
    summaries = _collect_summaries(dynamo_client)
    places_map = _collect_places(dynamo_client)

    stats = {
        "receipts": 0,
        "merchants": 0,
        "places": 0,
        "categories": 0,
        "dates": 0,
        "line_items": 0,
    }

    # Phase 1: MERGE temporal nodes for all unique dates
    unique_dates: set[str] = set()
    for record in summaries:
        if record.date:
            unique_dates.add(record.date.strftime("%Y-%m-%d"))

    with driver.session(database=database) as session:
        for date_str in sorted(unique_dates):
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            session.execute_write(_merge_temporal, dt)
            stats["dates"] += 1

    logger.info("Merged %d date nodes", stats["dates"])

    # Phase 2: MERGE receipts in batches
    receipt_rows: list[dict[str, Any]] = []
    for record in summaries:
        receipt_key = (
            f"{record.image_id}:{record.receipt_id:05d}"
        )
        place_key = (
            f"{record.image_id}:{record.receipt_id:05d}"
        )
        place = places_map.get(place_key)

        merchant_name = record.merchant_name
        if not merchant_name and place:
            merchant_name = place.merchant_name

        date_str = None
        if record.date:
            date_str = record.date.strftime("%Y-%m-%d")

        receipt_rows.append(
            {
                "receipt_key": receipt_key,
                "image_id": record.image_id,
                "receipt_id": record.receipt_id,
                "merchant_name": merchant_name,
                "grand_total": record.grand_total,
                "subtotal": record.subtotal,
                "tax": record.tax,
                "tip": record.tip,
                "item_count": record.item_count,
                "date_str": date_str,
            }
        )

    with driver.session(database=database) as session:
        for i in range(0, len(receipt_rows), batch_size):
            batch = receipt_rows[i : i + batch_size]
            session.execute_write(
                _merge_receipt_batch, batch
            )
            stats["receipts"] += len(batch)

    logger.info("Merged %d receipt nodes", stats["receipts"])

    # Phase 3: MERGE merchants, categories, places
    seen_merchants: set[str] = set()
    merchant_rows: list[dict[str, Any]] = []

    for record in summaries:
        receipt_key = (
            f"{record.image_id}:{record.receipt_id:05d}"
        )
        place_key = (
            f"{record.image_id}:{record.receipt_id:05d}"
        )
        place = places_map.get(place_key)

        merchant_name = record.merchant_name
        if not merchant_name and place:
            merchant_name = place.merchant_name
        if not merchant_name:
            continue

        normalized = _normalize_merchant_name(merchant_name)
        if not normalized:
            continue

        primary_category = ""
        if place and place.merchant_category:
            primary_category = place.merchant_category

        row = {
            "receipt_key": receipt_key,
            "merchant_name": merchant_name,
            "normalized_name": normalized,
            "primary_category": primary_category,
            "category_display": (
                _category_display(primary_category)
                if primary_category
                else ""
            ),
            "place_id": place.place_id if place else "",
            "formatted_address": (
                place.formatted_address if place else ""
            ),
            "latitude": place.latitude if place else None,
            "longitude": place.longitude if place else None,
            "business_status": (
                place.business_status if place else ""
            ),
            "phone_number": (
                place.phone_number if place else ""
            ),
            "website": place.website if place else "",
        }
        merchant_rows.append(row)

        if normalized not in seen_merchants:
            seen_merchants.add(normalized)
            stats["merchants"] += 1
            if primary_category:
                stats["categories"] += 1
            if place and place.place_id:
                stats["places"] += 1

    with driver.session(database=database) as session:
        for i in range(0, len(merchant_rows), batch_size):
            batch = merchant_rows[i : i + batch_size]
            session.execute_write(
                _merge_merchant_and_rels, batch
            )

    logger.info(
        "Merged %d merchants, %d categories, %d places",
        stats["merchants"],
        stats["categories"],
        stats["places"],
    )

    # Phase 4: ON_DATE relationships
    date_rels = [
        r for r in receipt_rows if r["date_str"] is not None
    ]
    with driver.session(database=database) as session:
        for i in range(0, len(date_rels), batch_size):
            batch = date_rels[i : i + batch_size]
            session.execute_write(_merge_date_rels, batch)

    logger.info(
        "Created %d ON_DATE relationships", len(date_rels)
    )

    # Phase 5: Line items (optional, slower)
    if fetch_line_items:
        logger.info(
            "Fetching line items for %d receipts...",
            len(summaries),
        )
        for idx, record in enumerate(summaries):
            receipt_key = (
                f"{record.image_id}:{record.receipt_id:05d}"
            )
            items = _collect_line_items(
                dynamo_client,
                record.image_id,
                record.receipt_id,
            )
            if items:
                with driver.session(
                    database=database
                ) as session:
                    session.execute_write(
                        _merge_line_items,
                        receipt_key,
                        items,
                    )
                stats["line_items"] += len(items)

            if (idx + 1) % 100 == 0:
                logger.info(
                    "Processed %d/%d receipts (%d items)",
                    idx + 1,
                    len(summaries),
                    stats["line_items"],
                )

    logger.info(
        "ETL complete: %s",
        ", ".join(f"{k}={v}" for k, v in stats.items()),
    )
    return stats
