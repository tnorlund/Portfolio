"""
Zip-based Lambda handler that validates a single receipt's merchant by
querying a Fargate-hosted Chroma HTTP server (if configured) plus
Google Places as fallback, then writing `ReceiptMetadata` to DynamoDB.

Reads CHROMA_HTTP_ENDPOINT (e.g. "chroma-<stack>.svc.local:8000").
"""

import os
from typing import Any, Dict

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_label.data.places_api import PlacesAPI
from receipt_label.merchant_resolution.resolver import resolve_receipt

from .common import setup_logger

# Set up logging
logger = setup_logger(__name__)


def _embed_fn_from_openai_texts(texts):
    if not texts:
        return []
    if not os.environ.get("OPENAI_API_KEY"):
        return [[0.0] * 1536 for _ in texts]
    from receipt_label.utils import get_client_manager

    openai_client = get_client_manager().openai
    embed_response = openai_client.embeddings.create(
        model="text-embedding-3-small", input=list(texts)
    )
    return [d.embedding for d in embed_response.data]


class _HttpVectorAdapter:
    """Minimal adapter exposing .query(...) over Chroma HTTP.

    Expects CHROMA_HTTP_ENDPOINT env: "host:port" or URL with scheme.
    Only the methods used by resolver/chroma.py are implemented.
    """

    def __init__(self, endpoint: str | None):
        self._client = None
        self._endpoint = (endpoint or "").strip()
        if not self._endpoint:
            return
        try:
            import chromadb  # Lazy import in Lambda
        except ImportError as e:  # pragma: no cover - defensive
            logger.warning("Chroma import failed: %s", e)
            self._client = None
            return

        host = self._endpoint
        port = None
        if "://" in host:
            host = host.split("://", 1)[1]
        if ":" in host:
            host, port_str = host.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = None

        kwargs = {"host": host}
        if port is not None:
            kwargs["port"] = port
        try:
            self._client = chromadb.HttpClient(**kwargs)
        except (TypeError, ValueError) as e:  # pragma: no cover - defensive
            logger.warning("Chroma HttpClient create failed: %s", e)
            self._client = None

    def query(
        self,
        collection_name: str,
        query_embeddings: list[list[float]] | None = None,
        query_texts: list[str] | None = None,
        n_results: int = 10,
        where: Dict[str, Any] | None = None,
        include: list[str] | None = None,
    ) -> Dict[str, Any]:
        if not self._client:
            # Behave like empty result; resolver will fall back to Places
            return {"metadatas": [[]], "distances": [[]]}
        if query_embeddings is None and query_texts is None:
            raise ValueError(
                "Either query_embeddings or query_texts must be provided"
            )
        coll = self._client.get_collection(name=collection_name)
        result = coll.query(
            query_embeddings=query_embeddings,
            query_texts=query_texts,
            n_results=n_results,
            where=where,
            include=include or ["metadatas", "documents", "distances"],
        )
        return result


def validate_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Validate a single receipt's merchant using Chroma HTTP + Places.
    """
    logger.info("Starting validate_single_receipt_handler")

    image_id = event["image_id"]
    receipt_id = int(event["receipt_id"])

    table = os.environ["DYNAMO_TABLE_NAME"]
    dynamo = DynamoClient(table)

    places_api = PlacesAPI(api_key=os.environ.get("GOOGLE_PLACES_API_KEY"))

    chroma_endpoint = os.environ.get("CHROMA_HTTP_ENDPOINT") or os.environ.get(
        "CHROMA_HTTP_URL"
    )
    chroma_line_client = _HttpVectorAdapter(chroma_endpoint)

    resolution = resolve_receipt(
        key=(image_id, receipt_id),
        dynamo=dynamo,
        places_api=places_api,
        chroma_line_client=chroma_line_client,
        embed_fn=_embed_fn_from_openai_texts,
        write_metadata=True,
    )

    decision = resolution.get("decision") or {}
    best = decision.get("best") or {}
    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "wrote_metadata": bool(resolution.get("wrote_metadata")),
        "best_source": best.get("source"),
        "best_score": best.get("score"),
        "best_place_id": best.get("place_id"),
    }


# For local testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(f"Usage: python {__file__} <image_id> <receipt_id>")
        sys.exit(1)

    test_event = {"image_id": sys.argv[1], "receipt_id": int(sys.argv[2])}

    cli_resp = validate_handler(test_event, None)
    print(f"Result: {cli_resp}")
