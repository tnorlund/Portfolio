import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

import boto3
import chromadb

from receipt_dynamo import DynamoClient


def build_chromadb_id(word: Dict[str, Any]) -> str:
    return (
        f"IMAGE#{word['image_id']}#"
        f"RECEIPT#{int(word['receipt_id']):05d}#"
        f"LINE#{int(word['line_id']):05d}#"
        f"WORD#{int(word['word_id']):05d}"
    )


def get_embeddings_for_words(
    dynamo_table: str, ids: List[str]
) -> List[List[float]]:
    dynamo_client = DynamoClient(dynamo_table)
    words, _ = dynamo_client.list_receipt_words(limit=50)

    keys = []
    for cid in ids:
        parts = cid.split("#")
        pk = f"IMAGE#{parts[1]}"
        sk = f"RECEIPT#{int(parts[3]):05d}#LINE#{int(parts[5]):05d}#WORD#{int(parts[7]):05d}"
        keys.append({"PK": {"S": pk}, "SK": {"S": sk}})

    ddb = boto3.client("dynamodb")
    resp = ddb.batch_get_item(RequestItems={dynamo_table: {"Keys": keys}})
    items = resp.get("Responses", {}).get(dynamo_table, [])
    sk_to_item = {i["SK"]["S"]: i for i in items}

    embeddings: List[List[float]] = []
    for cid in ids:
        parts = cid.split("#")
        sk = f"RECEIPT#{int(parts[3]):05d}#LINE#{int(parts[5]):05d}#WORD#{int(parts[7]):05d}"
        item = sk_to_item.get(sk)
        if not item:
            embeddings.append([])
            continue
        val = item.get("embedding", {}).get("L", [])
        embeddings.append([float(n["N"]) for n in val])
    return embeddings


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    endpoint = os.environ["CHROMA_HTTP_ENDPOINT"]
    table_name = os.environ["DYNAMO_TABLE_NAME"]

    print(f"[worker] CHROMA_HTTP_ENDPOINT={endpoint}")
    print(f"[worker] DYNAMO_TABLE_NAME={table_name}")

    # External internet egress probe
    try:
        with urllib.request.urlopen(
            "https://checkip.amazonaws.com", timeout=5
        ) as r:
            ip_txt = r.read().decode().strip()
            print(f"[worker] Internet OK status={r.status} ip={ip_txt}")
    except Exception as e:
        print(f"[worker] Internet probe failed: {e}")

    # Lightweight Google probe (ping-like): should return HTTP 204 immediately
    try:
        with urllib.request.urlopen(
            "https://www.gstatic.com/generate_204", timeout=5
        ) as r:
            print(
                f"[worker] Google generate_204 OK status={r.status} (expected 204)"
            )
    except Exception as e:
        print(f"[worker] Google generate_204 probe failed: {e}")

    # List the first 50 words from DynamoDB
    dyn = DynamoClient(table_name)
    words, _ = dyn.list_receipt_words(limit=50)
    print(f"[worker] Listed {len(words)} words")

    # Build Chroma IDs
    ids: List[str] = []
    for w in words:
        cid = (
            f"IMAGE#{w.image_id}#"
            f"RECEIPT#{int(w.receipt_id):05d}#"
            f"LINE#{int(w.line_id):05d}#"
            f"WORD#{int(w.word_id):05d}"
        )
        ids.append(cid)
    print(f"[worker] Built {len(ids)} Chroma IDs")

    # Query Chroma HTTP server
    host, port = endpoint.split(":")
    print(f"[worker] Connecting to Chroma http://{host}:{port}")
    # Chroma HTTP readiness probe (version endpoint)
    try:
        with urllib.request.urlopen(
            f"http://{host}:{port}/api/v1/version", timeout=5
        ) as r:
            body = r.read().decode(errors="ignore")
            print(
                f"[worker] Chroma version probe OK status={r.status} body={body}"
            )
    except urllib.error.HTTPError as he:
        print(
            f"[worker] Chroma version probe HTTP error status={he.code} msg={he}"
        )
    except Exception as e:
        print(f"[worker] Chroma version probe failed: {e}")
    client = chromadb.HttpClient(host=host, port=int(port))
    collection = client.get_collection("words")

    try:
        # Fetch stored embeddings for these IDs, then query by embeddings
        got = collection.get(ids=ids, include=["embeddings"])
        embeddings = got.get("embeddings", [])

        # Normalize and filter embeddings defensively (arrays vs lists)
        normalized: List[List[float]] = []
        for emb in embeddings:
            if emb is None:
                continue
            vec = list(emb) if not isinstance(emb, list) else emb
            if len(vec) == 0:
                continue
            normalized.append(vec)

        print(
            f"[worker] Retrieved embeddings from Chroma: {len(normalized)}/{len(ids)} present"
        )
        if not normalized:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "no embeddings found for ids"}),
            }

        result = collection.query(query_embeddings=normalized, n_results=10)
        returned = len(result.get("ids", [[]])[0]) if result.get("ids") else 0
        print(f"[worker] Query complete. Top-k (first): {returned}")
        return {"statusCode": 200, "body": {"Name": "workers_handler"}}
    except Exception as e:
        print(f"[worker] Query error: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
