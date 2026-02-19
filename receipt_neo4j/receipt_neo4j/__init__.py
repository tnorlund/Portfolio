"""Neo4j graph database client for receipt data.

Provides a graph-based query layer complementing DynamoDB (entities)
and ChromaDB (semantic search). Neo4j handles structured traversals
for merchant/category/date queries, cross-receipt analysis, and
temporal aggregations.
"""

from receipt_neo4j.client import ReceiptGraphClient
from receipt_neo4j.etl import populate_graph
from receipt_neo4j.schema import ensure_schema

__all__ = [
    "ReceiptGraphClient",
    "ensure_schema",
    "populate_graph",
]
