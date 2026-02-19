"""Neo4j schema definitions for receipt graph.

Creates constraints, indexes, and full-text indexes for the receipt
graph model:

    Receipt -[:SOLD_BY]-> Merchant -[:IN_CATEGORY]-> Category
    Receipt -[:ON_DATE]-> Date -[:IN_MONTH]-> Month -[:IN_YEAR]-> Year
    Receipt -[:HAS_ITEM]-> LineItem
    Merchant -[:LOCATED_AT]-> Place
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neo4j import Driver

logger = logging.getLogger(__name__)

# Uniqueness constraints (also serve as indexes)
CONSTRAINTS = [
    (
        "receipt_key",
        "CREATE CONSTRAINT receipt_key IF NOT EXISTS "
        "FOR (r:Receipt) REQUIRE r.receipt_key IS UNIQUE",
    ),
    (
        "merchant_name",
        "CREATE CONSTRAINT merchant_name IF NOT EXISTS "
        "FOR (m:Merchant) REQUIRE m.normalized_name IS UNIQUE",
    ),
    (
        "place_id",
        "CREATE CONSTRAINT place_id IF NOT EXISTS "
        "FOR (p:Place) REQUIRE p.place_id IS UNIQUE",
    ),
    (
        "category_name",
        "CREATE CONSTRAINT category_name IF NOT EXISTS "
        "FOR (c:Category) REQUIRE c.name IS UNIQUE",
    ),
    (
        "date_unique",
        "CREATE CONSTRAINT date_unique IF NOT EXISTS "
        "FOR (d:Date) REQUIRE d.date IS UNIQUE",
    ),
    (
        "month_unique",
        "CREATE CONSTRAINT month_unique IF NOT EXISTS "
        "FOR (m:Month) REQUIRE (m.year, m.month) IS UNIQUE",
    ),
    (
        "year_unique",
        "CREATE CONSTRAINT year_unique IF NOT EXISTS "
        "FOR (y:Year) REQUIRE y.year IS UNIQUE",
    ),
]

# Range indexes for filtering/sorting
RANGE_INDEXES = [
    (
        "receipt_grand_total",
        "CREATE INDEX receipt_grand_total IF NOT EXISTS "
        "FOR (r:Receipt) ON (r.grand_total)",
    ),
    (
        "receipt_date",
        "CREATE INDEX receipt_date IF NOT EXISTS "
        "FOR (r:Receipt) ON (r.date)",
    ),
    (
        "lineitem_amount",
        "CREATE INDEX lineitem_amount IF NOT EXISTS "
        "FOR (li:LineItem) ON (li.amount)",
    ),
    (
        "date_day_of_week",
        "CREATE INDEX date_day_of_week IF NOT EXISTS "
        "FOR (d:Date) ON (d.day_of_week)",
    ),
]

# Full-text indexes for product/merchant search
FULLTEXT_INDEXES = [
    (
        "product_search",
        "CREATE FULLTEXT INDEX product_search IF NOT EXISTS "
        "FOR (li:LineItem) ON EACH [li.product_name, li.text_lower]",
    ),
    (
        "merchant_search",
        "CREATE FULLTEXT INDEX merchant_search IF NOT EXISTS "
        "FOR (m:Merchant) ON EACH [m.name, m.normalized_name]",
    ),
]


def ensure_schema(driver: "Driver") -> None:
    """Create all constraints, indexes, and full-text indexes.

    Safe to call repeatedly — uses IF NOT EXISTS on all statements.

    Args:
        driver: Neo4j driver instance.
    """
    with driver.session() as session:
        for name, cypher in CONSTRAINTS:
            logger.info("Ensuring constraint: %s", name)
            session.run(cypher)

        for name, cypher in RANGE_INDEXES:
            logger.info("Ensuring index: %s", name)
            session.run(cypher)

        for name, cypher in FULLTEXT_INDEXES:
            logger.info("Ensuring full-text index: %s", name)
            session.run(cypher)

    logger.info(
        "Schema ready: %d constraints, %d indexes, %d full-text indexes",
        len(CONSTRAINTS),
        len(RANGE_INDEXES),
        len(FULLTEXT_INDEXES),
    )
