"""OpenAI batch polling functions.

This module provides functions for polling OpenAI batch embedding jobs
and downloading their results.
"""

import json
import logging
from typing import List

from openai import OpenAI

from receipt_dynamo.constants import BatchType
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import BatchSummary

logger = logging.getLogger(__name__)


def get_openai_batch_status(openai_batch_id: str, openai_client: OpenAI) -> str:
    """
    Retrieve the status of an OpenAI embedding batch job.

    Args:
        openai_batch_id: The identifier of the batch
        openai_client: OpenAI client instance

    Returns:
        Current status of the batch
    """
    return openai_client.batches.retrieve(openai_batch_id).status


def download_openai_batch_result(
    openai_batch_id: str, openai_client: OpenAI
) -> List[dict]:
    """
    Download and parse the results of an OpenAI embedding batch job.

    Args:
        openai_batch_id: The identifier of the batch
        openai_client: OpenAI client instance

    Returns:
        List of embedding result objects with `custom_id` and `embedding`
    """
    batch = openai_client.batches.retrieve(openai_batch_id)
    output_file_id = batch.output_file_id

    if not output_file_id:
        logger.warning(
            "No output file available for batch %s with status %s",
            openai_batch_id,
            batch.status,
        )
        return []

    response = openai_client.files.content(output_file_id)

    # Handle different response types
    if hasattr(response, "read"):
        lines = response.read().decode("utf-8").splitlines()
    elif isinstance(response, bytes):
        lines = response.decode("utf-8").splitlines()
    elif isinstance(response, str):
        lines = response.splitlines()
    else:
        raise ValueError("Unexpected OpenAI output file content type")

    results = []
    for line in lines:
        if not line.strip():
            continue

        try:
            record = json.loads(line)
            # Extract embedding data from nested structure
            embedding = (
                record.get("response", {})
                .get("body", {})
                .get("data", [{}])[0]
                .get("embedding")
            )

            if embedding:
                results.append(
                    {
                        "custom_id": record.get("custom_id"),
                        "embedding": embedding,
                    }
                )
            else:
                logger.warning(
                    "No embedding found in record: %s", record.get("custom_id")
                )

        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse result line: %s", e)
            continue

    logger.info("Downloaded %d results from batch %s", len(results), openai_batch_id)
    return results


def list_pending_line_embedding_batches(
    dynamo_client: DynamoClient,
) -> List[BatchSummary]:
    """
    List line embedding batches that are pending processing.

    Args:
        dynamo_client: DynamoDB client instance

    Returns:
        List of pending batch identifiers
    """
    summaries, lek = dynamo_client.get_batch_summaries_by_status(
        status="PENDING",
        batch_type=BatchType.LINE_EMBEDDING,
        limit=25,
        last_evaluated_key=None,
    )
    while lek:
        next_summaries, lek = dynamo_client.get_batch_summaries_by_status(
            status="PENDING",
            batch_type=BatchType.LINE_EMBEDDING,
            limit=25,
            last_evaluated_key=lek,
        )
        summaries.extend(next_summaries)
    return summaries  # type: ignore[no-any-return]


def list_pending_word_embedding_batches(
    dynamo_client: DynamoClient,
) -> List[BatchSummary]:
    """
    List word embedding batches that are pending processing.

    Args:
        dynamo_client: DynamoDB client instance

    Returns:
        List of pending batch identifiers
    """
    summaries, lek = dynamo_client.get_batch_summaries_by_status(
        status="PENDING",
        batch_type=BatchType.WORD_EMBEDDING,
        limit=25,
        last_evaluated_key=None,
    )
    while lek:
        next_summaries, lek = dynamo_client.get_batch_summaries_by_status(
            status="PENDING",
            batch_type=BatchType.WORD_EMBEDDING,
            limit=25,
            last_evaluated_key=lek,
        )
        summaries.extend(next_summaries)
    return summaries  # type: ignore[no-any-return]
