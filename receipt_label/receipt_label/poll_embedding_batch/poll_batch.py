from typing import List
import json
import logging


"""
poll_batch.py

This module handles the polling, result retrieval, and ingestion pipeline for
embedding batch jobs submitted to OpenAI's Batch API. It is designed to be used
in conjunction with a Step Function that monitors the status of submitted batches
and processes them once complete.

Functions in this module perform the following tasks:
- List all embedding batches with status "PENDING" from DynamoDB.
- Poll OpenAI's Batch API to determine if a batch job is complete.
- Download the NDJSON-formatted embedding results once a batch completes.
- Parse and upsert the embeddings to Pinecone using structured metadata.
- Write embedding results to a DynamoDB table for tracking.
- Mark completed batches in DynamoDB to prevent reprocessing.

This supports scalable, event-driven processing of large embedding jobs in a
distributed receipt labeling and validation workflow.
"""

from receipt_dynamo.entities import EmbeddingBatchResult, BatchSummary
from receipt_label.utils import get_clients

dynamo_client, openai_client, pinecone_index = get_clients()


def parse_metadata_from_custom_id(custom_id: str, body: dict) -> dict:
    parts = custom_id.split("#")
    label = parts[-1]

    # Parse position and angle from input text (if you want to reuse it from there)
    meta = {
        "image_id": parts[1],
        "receipt_id": int(parts[3]),
        "line_id": int(parts[5]),
        "word_id": int(parts[7]),
        "label": label,
        "source": "openai_embedding_batch",
    }

    try:
        # extract raw input text
        input_text = body.get("input", "")
        if "(pos=" in input_text:
            pos_part = input_text.split("pos=")[1].split(")")[0]
            x, y = map(float, pos_part.split(","))
            meta["x_center"] = x
            meta["y_center"] = y
        if "angle=" in input_text:
            meta["angle"] = float(input_text.split("angle=")[1].split()[0])
        if "conf=" in input_text:
            meta["confidence"] = float(input_text.split("conf=")[1])
    except Exception:
        pass

    return meta


def list_pending_embedding_batches() -> List[BatchSummary]:
    """
    List embedding batches that are pending processing.
    Returns a list of pending batch identifiers.
    """
    summaries, lek = dynamo_client.getBatchSummariesByStatus(
        status="PENDING",
        limit=25,
        lastEvaluatedKey=None,
    )
    while lek:
        next_summaries, lek = dynamo_client.getBatchSummariesByStatus(
            status="PENDING",
            limit=25,
            lastEvaluatedKey=lek,
        )
        summaries.extend(next_summaries)
    return summaries


def get_openai_batch_status(openai_batch_id: str) -> str:
    """
    Retrieve the status of an OpenAI embedding batch job.
    Args:
        openai_batch_id (str): The identifier of the batch.
    Returns the current status of the batch.
    """
    return openai_client.batches.retrieve(openai_batch_id).status


def download_openai_batch_result(openai_batch_id: str) -> List[dict]:
    """
    Download and parse the results of an OpenAI embedding batch job.
    Returns a list of embedding result objects with `custom_id` and `embedding`.
    """
    batch = openai_client.batches.retrieve(openai_batch_id)
    output_file_id = batch.output_file_id
    response = openai_client.files.content(output_file_id)

    # If the content is raw bytes, decode it:
    if hasattr(response, "read"):
        lines = response.read().decode("utf-8").splitlines()
    elif isinstance(response, bytes):
        lines = response.decode("utf-8").splitlines()
    elif isinstance(response, str):
        lines = response.splitlines()
    else:
        raise ValueError("Unexpected OpenAI file content type")

    return [json.loads(line) for line in lines if line.strip()]


def upsert_embeddings_to_pinecone(results: List[dict]):
    """
    Upsert the embedding results to Pinecone.
    Args:
        results (List[dict]): The list of embedding results.
    """
    print(f"Upserting {len(results)} embeddings to Pinecone")
    print("First few embedding results:")

    vectors = [
        {
            "id": r["custom_id"],
            "values": r["response"]["body"]["data"][0]["embedding"],
            "metadata": parse_metadata_from_custom_id(
                r["custom_id"], r.get("body", {})
            ),
        }
        for r in results
        if r.get("response", {}).get("body", {}).get("data")
    ]

    batch_size = 100
    upserted_count = 0
    for i in range(0, len(vectors), batch_size):
        chunk = vectors[i : i + batch_size]
        try:
            response = pinecone_index.upsert(vectors=chunk)
            upserted_count += response.get("upserted_count", 0)
        except Exception as e:
            print(f"Failed to upsert chunk to Pinecone: {e}")
            raise e
    return upserted_count


def write_embedding_results_to_dynamo(results: List[dict], batch_id: str):
    """
    Write the embedding results to DynamoDB.
    Args:
        results (List[dict]): The list of embedding results.
    """

    embedding_results = []
    for result in results:
        custom_id = result["custom_id"]
        meta = parse_metadata_from_custom_id(custom_id, result.get("body", {}))
        image_id = meta["image_id"]
        receipt_id = meta["receipt_id"]
        line_id = meta["line_id"]
        word_id = meta["word_id"]
        label = meta["label"]
        pinecone_id = (
            f"RECEIPT#{receipt_id}#LINE#{line_id}#WORD#{word_id}#LABEL#{label}"
        )
        input_text = result["body"]["input"]
        text = input_text.split(" [label=")[0].strip()
        status = "SUCCESS"
        embedding_result = EmbeddingBatchResult(
            batch_id=batch_id,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            pinecone_id=pinecone_id,
            status=status,
            text=text,
            label=label,
        )
        embedding_results.append(embedding_result)

    # Batch embedding_results into chunks of 25 and process each chunk separately
    for i in range(0, len(embedding_results), 25):
        chunk = embedding_results[i : i + 25]
        dynamo_client.embedding_batch_result.addEmbeddingBatchResults(chunk)


def mark_batch_complete(batch_id: str):
    """
    Mark the embedding batch as complete in the system.
    Args:
        batch_id (str): The identifier of the batch.
    """
    batch_summary = dynamo_client.getBatchSummary(batch_id)
    batch_summary.status = "COMPLETED"
    dynamo_client.updateBatchSummary(batch_summary)
