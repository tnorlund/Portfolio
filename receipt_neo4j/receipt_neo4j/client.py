"""Neo4j graph client for receipt queries.

Provides Cypher-backed implementations of the 7 QA tools that move
from DynamoDB/ChromaDB to Neo4j:

1. list_merchants       — merchants with receipt counts
2. list_categories      — categories with receipt counts
3. get_receipts_by_merchant — receipts for a merchant
4. get_receipt_summaries — filtered/aggregated receipt data
5. search_receipts_text — full-text product search
6. aggregate_amounts    — sum line-item amounts with filters
7. search_product_lines — product lines with prices
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from neo4j import Driver

logger = logging.getLogger(__name__)


class ReceiptGraphClient:
    """Query interface for the receipt Neo4j graph.

    All methods execute read transactions and return plain dicts
    suitable for direct use as LangChain tool return values.

    Args:
        driver: Neo4j driver instance (bolt or neo4j protocol).
        database: Neo4j database name (default "neo4j").
    """

    def __init__(
        self,
        driver: Driver,
        database: str = "neo4j",
    ) -> None:
        self._driver = driver
        self._database = database

    def close(self) -> None:
        """Close the underlying driver."""
        self._driver.close()

    # ------------------------------------------------------------------
    # 1. list_merchants
    # ------------------------------------------------------------------

    def list_merchants(self) -> list[dict[str, Any]]:
        """List merchants ordered by receipt count descending.

        Returns:
            List of {name, normalized_name, primary_category,
            receipt_count}.
        """
        cypher = """
        MATCH (m:Merchant)<-[:SOLD_BY]-(r:Receipt)
        RETURN m.name AS name,
               m.normalized_name AS normalized_name,
               m.primary_category AS primary_category,
               count(r) AS receipt_count
        ORDER BY receipt_count DESC
        """
        with self._driver.session(
            database=self._database
        ) as session:
            result = session.run(cypher)
            return [dict(record) for record in result]

    # ------------------------------------------------------------------
    # 2. list_categories
    # ------------------------------------------------------------------

    def list_categories(self) -> list[dict[str, Any]]:
        """List categories ordered by receipt count descending.

        Returns:
            List of {name, display_name, receipt_count}.
        """
        cypher = """
        MATCH (c:Category)<-[:IN_CATEGORY]-(m:Merchant)
              <-[:SOLD_BY]-(r:Receipt)
        RETURN c.name AS name,
               c.display_name AS display_name,
               count(DISTINCT r) AS receipt_count
        ORDER BY receipt_count DESC
        """
        with self._driver.session(
            database=self._database
        ) as session:
            result = session.run(cypher)
            return [dict(record) for record in result]

    # ------------------------------------------------------------------
    # 3. get_receipts_by_merchant
    # ------------------------------------------------------------------

    def get_receipts_by_merchant(
        self,
        merchant_name: str,
    ) -> dict[str, Any]:
        """Get all receipt keys for a merchant.

        Args:
            merchant_name: Exact merchant name.

        Returns:
            {merchant, count, receipts: [[image_id, receipt_id], ...]}.
        """
        cypher = """
        MATCH (m:Merchant)<-[:SOLD_BY]-(r:Receipt)
        WHERE toLower(m.name) = toLower($name)
        RETURN m.name AS merchant,
               count(r) AS count,
               collect([r.image_id, r.receipt_id]) AS receipts
        """
        with self._driver.session(
            database=self._database
        ) as session:
            result = session.run(cypher, name=merchant_name)
            record = result.single()
            if record is None:
                return {
                    "merchant": merchant_name,
                    "count": 0,
                    "receipts": [],
                }
            return dict(record)

    # ------------------------------------------------------------------
    # 4. get_receipt_summaries
    # ------------------------------------------------------------------

    def get_receipt_summaries(
        self,
        merchant_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Get receipt summaries with optional filters and aggregates.

        Args:
            merchant_filter: Partial merchant name match.
            category_filter: Category name (e.g. "grocery_store").
            start_date: ISO date string (YYYY-MM-DD), inclusive.
            end_date: ISO date string (YYYY-MM-DD), inclusive.
            limit: Max receipts to return.

        Returns:
            Dict with count, total_spending, total_tax, total_tip,
            average_receipt, filters, and summaries list.
        """
        # Build query dynamically based on which filters are set
        match_parts = ["MATCH (r:Receipt)-[:SOLD_BY]->(m:Merchant)"]
        where_parts: list[str] = []
        params: dict[str, Any] = {"limit": limit}

        if merchant_filter:
            where_parts.append(
                "toLower(m.name) CONTAINS toLower($merchant)"
            )
            params["merchant"] = merchant_filter

        if category_filter:
            match_parts.append(
                "MATCH (m)-[:IN_CATEGORY]->(c:Category)"
            )
            where_parts.append("c.name = $category")
            params["category"] = category_filter

        if start_date or end_date:
            match_parts.append(
                "MATCH (r)-[:ON_DATE]->(d:Date)"
            )
            if start_date:
                where_parts.append("d.date >= date($start)")
                params["start"] = start_date
            if end_date:
                where_parts.append("d.date <= date($end)")
                params["end"] = end_date

        where_clause = ""
        if where_parts:
            where_clause = "WHERE " + " AND ".join(where_parts)

        # Two-pass: first get aggregates, then individual summaries
        agg_cypher = "\n".join(
            match_parts
            + [
                where_clause,
                """
        RETURN count(r) AS count,
               sum(r.grand_total) AS total_spending,
               sum(r.tax) AS total_tax,
               sum(r.tip) AS total_tip,
               avg(r.grand_total) AS average_receipt,
               sum(CASE WHEN r.grand_total IS NOT NULL
                   THEN 1 ELSE 0 END) AS receipts_with_totals
        """,
            ]
        )

        detail_cypher = "\n".join(
            match_parts
            + [
                "OPTIONAL MATCH (r)-[:ON_DATE]->(d:Date)"
                if not (start_date or end_date)
                else "",
                where_clause,
                """
        RETURN r.image_id AS image_id,
               r.receipt_id AS receipt_id,
               m.name AS merchant_name,
               r.grand_total AS grand_total,
               r.subtotal AS subtotal,
               r.tax AS tax,
               r.tip AS tip,
               r.item_count AS item_count,
               r.date_str AS date,
               m.primary_category AS merchant_category
        ORDER BY r.date DESC
        LIMIT $limit
        """,
            ]
        )

        with self._driver.session(
            database=self._database
        ) as session:
            agg_record = session.run(agg_cypher, **params).single()
            detail_records = session.run(
                detail_cypher, **params
            )
            summaries = [dict(r) for r in detail_records]

        agg = dict(agg_record) if agg_record else {}
        count = agg.get("count", 0)
        total_spending = agg.get("total_spending") or 0.0
        total_tax = agg.get("total_tax") or 0.0
        total_tip = agg.get("total_tip") or 0.0
        receipts_with_totals = agg.get("receipts_with_totals", 0)
        avg_receipt = agg.get("average_receipt")

        return {
            "count": count,
            "total_spending": round(total_spending, 2),
            "total_tax": round(total_tax, 2),
            "total_tip": round(total_tip, 2),
            "receipts_with_totals": receipts_with_totals,
            "average_receipt": (
                round(avg_receipt, 2)
                if avg_receipt is not None
                else None
            ),
            "filters": {
                "merchant": merchant_filter,
                "category": category_filter,
                "start_date": start_date,
                "end_date": end_date,
            },
            "summaries": summaries,
        }

    # ------------------------------------------------------------------
    # 5. search_receipts_text (full-text product search)
    # ------------------------------------------------------------------

    def search_receipts_text(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Full-text search over product names.

        Uses the product_search full-text index on LineItem nodes.

        Args:
            query: Search terms (e.g. "COFFEE", "organic milk").
            limit: Max results.

        Returns:
            List of {image_id, receipt_id, merchant_name,
            product_name, amount, score}.
        """
        cypher = """
        CALL db.index.fulltext.queryNodes(
            'product_search', $query
        ) YIELD node AS li, score
        MATCH (r:Receipt)-[:HAS_ITEM]->(li)
        RETURN DISTINCT
               r.image_id AS image_id,
               r.receipt_id AS receipt_id,
               r.merchant_name AS merchant_name,
               li.product_name AS product_name,
               li.amount AS amount,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        with self._driver.session(
            database=self._database
        ) as session:
            result = session.run(
                cypher, query=query, limit=limit
            )
            return [dict(record) for record in result]

    # ------------------------------------------------------------------
    # 6. aggregate_amounts
    # ------------------------------------------------------------------

    def aggregate_amounts(
        self,
        filter_text: Optional[str] = None,
        merchant_filter: Optional[str] = None,
    ) -> dict[str, Any]:
        """Aggregate line-item amounts with optional filters.

        Args:
            filter_text: Product name filter (partial, case-insensitive).
            merchant_filter: Merchant name filter (partial).

        Returns:
            {total, count, breakdown: [{merchant, amount, product}, ...]}.
        """
        where_parts: list[str] = []
        params: dict[str, Any] = {}

        if filter_text:
            where_parts.append(
                "toLower(li.product_name) "
                "CONTAINS toLower($filter_text)"
            )
            params["filter_text"] = filter_text

        if merchant_filter:
            where_parts.append(
                "toLower(r.merchant_name) "
                "CONTAINS toLower($merchant)"
            )
            params["merchant"] = merchant_filter

        where_clause = ""
        if where_parts:
            where_clause = "WHERE " + " AND ".join(where_parts)

        cypher = f"""
        MATCH (r:Receipt)-[:HAS_ITEM]->(li:LineItem)
        {where_clause}
        WITH r, li
        WHERE li.amount IS NOT NULL
        RETURN sum(li.amount) AS total,
               count(li) AS count,
               collect({{
                   merchant: r.merchant_name,
                   amount: li.amount,
                   product: li.product_name
               }}) AS breakdown
        """
        with self._driver.session(
            database=self._database
        ) as session:
            record = session.run(cypher, **params).single()

        if record is None:
            return {"total": 0.0, "count": 0, "breakdown": []}

        data = dict(record)
        return {
            "total": round(data.get("total") or 0.0, 2),
            "count": data.get("count", 0),
            "breakdown": data.get("breakdown", []),
        }

    # ------------------------------------------------------------------
    # 7. search_product_lines
    # ------------------------------------------------------------------

    def search_product_lines(
        self,
        query: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search product lines with prices for spending analysis.

        Uses the product_search full-text index.

        Args:
            query: Search terms.
            limit: Max results.

        Returns:
            List of {product_name, amount, merchant, image_id,
            receipt_id, score}.
        """
        cypher = """
        CALL db.index.fulltext.queryNodes(
            'product_search', $query
        ) YIELD node AS li, score
        MATCH (r:Receipt)-[:HAS_ITEM]->(li)
        RETURN li.product_name AS text,
               li.amount AS price,
               r.merchant_name AS merchant,
               r.image_id AS image_id,
               r.receipt_id AS receipt_id,
               score
        ORDER BY score DESC
        LIMIT $limit
        """
        with self._driver.session(
            database=self._database
        ) as session:
            result = session.run(
                cypher, query=query, limit=limit
            )
            return [dict(record) for record in result]

    # ------------------------------------------------------------------
    # Temporal queries
    # ------------------------------------------------------------------

    def get_receipts_by_date_range(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Get receipts within a date range.

        Args:
            start_date: ISO date string (YYYY-MM-DD).
            end_date: ISO date string (YYYY-MM-DD).

        Returns:
            List of {merchant_name, grand_total, date}.
        """
        cypher = """
        MATCH (r:Receipt)-[:ON_DATE]->(d:Date)
        WHERE d.date >= date($start) AND d.date <= date($end)
        RETURN r.merchant_name AS merchant_name,
               r.grand_total AS grand_total,
               toString(d.date) AS date
        ORDER BY d.date DESC
        """
        with self._driver.session(
            database=self._database
        ) as session:
            result = session.run(
                cypher, start=start_date, end=end_date
            )
            return [dict(record) for record in result]

    def get_spending_by_day_of_week(self) -> list[dict[str, Any]]:
        """Get spending patterns grouped by day of week.

        Returns:
            List of {day_name, avg_spending, total, count}
            ordered Mon-Sun.
        """
        cypher = """
        MATCH (r:Receipt)-[:ON_DATE]->(d:Date)
        WHERE r.grand_total IS NOT NULL
        RETURN d.day_name AS day_name,
               avg(r.grand_total) AS avg_spending,
               sum(r.grand_total) AS total,
               count(r) AS count
        ORDER BY d.day_of_week
        """
        with self._driver.session(
            database=self._database
        ) as session:
            result = session.run(cypher)
            return [dict(record) for record in result]

    def find_duplicate_purchases(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Find items purchased at the same price across stores.

        Returns:
            List of {product_name, amount, store_1, store_2}.
        """
        cypher = """
        MATCH (r1:Receipt)-[:HAS_ITEM]->(li1:LineItem)
        MATCH (r2:Receipt)-[:HAS_ITEM]->(li2:LineItem)
        WHERE r1.receipt_key < r2.receipt_key
          AND toLower(li1.product_name) =
              toLower(li2.product_name)
          AND li1.amount IS NOT NULL
          AND abs(li1.amount - li2.amount) < 0.01
          AND r1.merchant_name <> r2.merchant_name
        RETURN li1.product_name AS product_name,
               li1.amount AS amount,
               r1.merchant_name AS store_1,
               r2.merchant_name AS store_2
        LIMIT $limit
        """
        with self._driver.session(
            database=self._database
        ) as session:
            result = session.run(cypher, limit=limit)
            return [dict(record) for record in result]
