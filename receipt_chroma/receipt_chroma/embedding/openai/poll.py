"""OpenAI batch polling functions.

This module provides functions for polling OpenAI batch embedding jobs
and downloading their results.
"""

import json
import logging
from typing import List

from openai import OpenAI

from receipt_dynamo.constants import BatchStatus, BatchType
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import BatchSummary

logger = logging.getLogger(__name__)

# Provider-live statuses that ingest must keep polling. Writing the OpenAI
# status onto BatchSummary means a batch leaves PENDING after the first poll;
# querying only PENDING strands it until a manual reset.
ACTIVE_BATCH_STATUSES = (
    BatchStatus.PENDING,
    BatchStatus.VALIDATING,
    BatchStatus.IN_PROGRESS,
    BatchStatus.FINALIZING,
    BatchStatus.CANCELING,
)


def _list_active_batches(
    dynamo_client: DynamoClient, batch_type: BatchType
) -> List[BatchSummary]:
    """List every provider-live embedding batch with pagination."""
    summaries: dict[str, BatchSummary] = {}
    for status in ACTIVE_BATCH_STATUSES:
        page, lek = dynamo_client.get_batch_summaries_by_status(
            status=status,
            batch_type=batch_type,
            limit=25,
            last_evaluated_key=None,
        )
        for summary in page:
            summaries[summary.batch_id] = summary
        while lek:
            page, lek = dynamo_client.get_batch_summaries_by_status(
                status=status,
                batch_type=batch_type,
                limit=25,
                last_evaluated_key=lek,
            )
            for summary in page:
                summaries[summary.batch_id] = summary
    return sorted(
        summaries.values(),
        key=lambda summary: (summary.submitted_at, summary.batch_id),
    )


def get_openai_batch_status(
    openai_batch_id: str, openai_client: OpenAI
) -> str:
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

    logger.info(
        "Downloaded %d results from batch %s", len(results), openai_batch_id
    )
    return results


def list_pending_line_embedding_batches(
    dynamo_client: DynamoClient,
) -> List[BatchSummary]:
    """
    List line embedding batches that still need polling.

    Includes every provider-live status (PENDING through CANCELING), not just
    PENDING. The first poll writes the OpenAI status onto the summary, so a
    PENDING-only query would strand in-flight batches across ingest runs.

    Args:
        dynamo_client: DynamoDB client instance

    Returns:
        Deduplicated active batch summaries, oldest first.
    """
    return _list_active_batches(dynamo_client, BatchType.LINE_EMBEDDING)


def list_pending_word_embedding_batches(
    dynamo_client: DynamoClient,
) -> List[BatchSummary]:
    """
    List word embedding batches that still need polling.

    Includes every provider-live status (PENDING through CANCELING), not just
    PENDING. The first poll writes the OpenAI status onto the summary, so a
    PENDING-only query would strand in-flight batches across ingest runs.

    Args:
        dynamo_client: DynamoDB client instance

    Returns:
        Deduplicated active batch summaries, oldest first.
    """
    return _list_active_batches(dynamo_client, BatchType.WORD_EMBEDDING)
