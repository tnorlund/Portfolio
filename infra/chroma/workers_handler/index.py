import os
import json
from typing import Any, Dict, List

import boto3
import chromadb


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

    body = event.get("body") if isinstance(event, dict) else None
    payload = json.loads(body) if isinstance(body, str) else event

    if isinstance(payload, dict) and "words" in payload:
        ids = [build_chromadb_id(w) for w in payload["words"]]
    elif isinstance(payload, dict) and "ids" in payload:
        ids = list(payload["ids"])
    else:
        return {"statusCode": 400, "body": json.dumps({"error": "no ids"})}

    embeddings = get_embeddings_for_words(table_name, ids)

    host, port = endpoint.split(":")
    client = chromadb.HttpClient(host=f"http://{host}", port=int(port))
    collection = client.get_collection("words")
    result = collection.query(query_embeddings=embeddings, n_results=10)

    return {"statusCode": 200, "body": json.dumps(result)}
