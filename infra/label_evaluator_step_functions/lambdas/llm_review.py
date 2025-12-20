"""
LLM Review Lambda Handler

Reviews flagged label issues using Ollama LLM with ChromaDB for similarity
search. This Lambda is invoked optionally when skip_llm_review=false.

Environment Variables:
- BATCH_BUCKET: S3 bucket for data and results
- CHROMADB_BUCKET: S3 bucket with ChromaDB snapshots
- RECEIPT_AGENT_OLLAMA_API_KEY: Ollama Cloud API key
- RECEIPT_AGENT_OLLAMA_BASE_URL: Ollama API base URL
- RECEIPT_AGENT_OLLAMA_MODEL: Ollama model name
- LANGCHAIN_API_KEY: LangSmith API key
- LANGCHAIN_TRACING_V2: Enable tracing
- LANGCHAIN_PROJECT: LangSmith project name
"""

# pylint: disable=import-outside-toplevel
# Lambda handlers delay imports until runtime for cold start optimization

import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any

import boto3

# Import shared tracing utility
from utils.tracing import flush_langsmith_traces

if TYPE_CHECKING:
    from evaluator_types import LLMReviewOutput

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


def download_chromadb_snapshot(
    bucket: str, collection: str, cache_path: str
) -> str:
    """Download ChromaDB snapshot from S3 using atomic pointer pattern."""
    chroma_db_file = os.path.join(cache_path, "chroma.sqlite3")
    if os.path.exists(chroma_db_file):
        logger.info("ChromaDB already cached at %s", cache_path)
        return cache_path

    logger.info("Downloading ChromaDB from s3://%s/%s/", bucket, collection)

    # Get latest pointer
    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    try:
        response = s3.get_object(Bucket=bucket, Key=pointer_key)
        timestamp = response["Body"].read().decode().strip()
        logger.info("Latest snapshot: %s", timestamp)
    except Exception as e:
        logger.error("Failed to get pointer: %s", e)
        raise

    # Download snapshot files
    prefix = f"{collection}/snapshot/timestamped/{timestamp}/"
    paginator = s3.get_paginator("list_objects_v2")

    os.makedirs(cache_path, exist_ok=True)
    downloaded = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relative_path = key[len(prefix) :]
            if not relative_path or key.endswith(".snapshot_hash"):
                continue

            local_path = os.path.join(cache_path, relative_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3.download_file(bucket, key, local_path)
            downloaded += 1

    logger.info("Downloaded %s files to %s", downloaded, cache_path)
    return cache_path


def load_json_from_s3(bucket: str, key: str) -> dict[str, Any]:
    """Load JSON data from S3."""
    response = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def upload_json_to_s3(bucket: str, key: str, data: Any) -> None:
    """Upload JSON data to S3."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def handler(event: dict[str, Any], _context: Any) -> "LLMReviewOutput":
    """
    Review flagged issues with LLM.

    Input:
    {
        "results_s3_key": "results/{exec}/{image_id}_{receipt_id}.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name"
    }

    Output:
    {
        "status": "completed",
        "reviewed_results_s3_key": "reviewed/{exec}/{image_id}_{receipt_id}.json",
        "issues_reviewed": 3,
        "decisions": {"VALID": 1, "INVALID": 1, "NEEDS_REVIEW": 1}
    }
    """
    results_s3_key = event.get("results_s3_key")
    execution_id = event.get("execution_id", "unknown")
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET")
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET")

    if not results_s3_key:
        raise ValueError("results_s3_key is required")
    if not batch_bucket:
        raise ValueError("batch_bucket is required")

    start_time = time.time()

    try:
        # 1. Load evaluation results from S3
        logger.info(
            "Loading results from s3://%s/%s",
            batch_bucket,
            results_s3_key,
        )
        eval_results = load_json_from_s3(batch_bucket, results_s3_key)

        image_id = eval_results.get("image_id")
        receipt_id = eval_results.get("receipt_id")
        issues = eval_results.get("issues", [])

        if not issues:
            logger.info("No issues to review")
            return {
                "status": "completed",
                "reviewed_results_s3_key": None,
                "issues_reviewed": 0,
                "decisions": {},
            }

        logger.info(
            "Reviewing %s issues for %s#%s",
            len(issues),
            image_id,
            receipt_id,
        )

        # 2. Setup ChromaDB (optional)
        # TODO: Wire ChromaDB into the LLM review prompt to provide similarity
        # search context. Currently the client is initialized but not used.
        # Options:
        #   1. Query similar words from ChromaDB and include in prompt
        #   2. Use embeddings for semantic similarity scoring
        #   3. Remove ChromaDB setup if not needed for this use case
        _chroma_client = None  # Reserved for future ChromaDB integration
        if chromadb_bucket:
            try:
                chroma_path = os.environ.get(
                    "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY", "/tmp/chromadb"
                )
                download_chromadb_snapshot(
                    chromadb_bucket, "words", chroma_path
                )
                os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = (
                    chroma_path
                )

                from receipt_chroma import ChromaClient

                _chroma_client = ChromaClient(persist_directory=chroma_path)
                logger.info("ChromaDB client initialized")
            except Exception as e:
                logger.warning("Could not initialize ChromaDB: %s", e)

        # 3. Setup Ollama LLM
        ollama_api_key = os.environ.get("RECEIPT_AGENT_OLLAMA_API_KEY")
        ollama_base_url = os.environ.get(
            "RECEIPT_AGENT_OLLAMA_BASE_URL", "https://ollama.com"
        )
        ollama_model = os.environ.get(
            "RECEIPT_AGENT_OLLAMA_MODEL", "gpt-oss:20b-cloud"
        )

        if not ollama_api_key:
            raise ValueError("RECEIPT_AGENT_OLLAMA_API_KEY not set")

        from langchain_ollama import ChatOllama

        llm = ChatOllama(
            model=ollama_model,
            base_url=ollama_base_url,
            client_kwargs={
                "headers": {"Authorization": f"Bearer {ollama_api_key}"},
                "timeout": 120,
            },
            temperature=0,
        )

        logger.info("LLM initialized: %s at %s", ollama_model, ollama_base_url)

        # 4. Review each issue
        from collections import Counter

        decisions: Counter = Counter()
        reviewed_issues: list[dict[str, Any]] = []

        for issue in issues:
            try:
                # Build prompt for LLM review
                prompt = _build_review_prompt(issue)

                # Call LLM
                from langchain_core.messages import HumanMessage

                response = llm.invoke([HumanMessage(content=prompt)])
                response_text = response.content.strip()

                # Parse response
                review_result = _parse_llm_response(response_text)
                decisions[review_result["decision"]] += 1

                reviewed_issues.append(
                    {
                        **issue,
                        "llm_review": review_result,
                    }
                )

                logger.debug(
                    "Reviewed '%s': %s",
                    issue.get("word_text"),
                    review_result["decision"],
                )

            except Exception as e:
                logger.warning("Error reviewing issue: %s", e)
                reviewed_issues.append(
                    {
                        **issue,
                        "llm_review": {
                            "decision": "NEEDS_REVIEW",
                            "reasoning": f"LLM review failed: {e}",
                            "error": True,
                        },
                    }
                )
                decisions["NEEDS_REVIEW"] += 1

        logger.info("Reviewed %s issues: %s", len(issues), dict(decisions))

        # 5. Upload reviewed results to S3
        reviewed_results = {
            **eval_results,
            "issues": reviewed_issues,
            "llm_review_summary": dict(decisions),
        }

        reviewed_s3_key = results_s3_key.replace("results/", "reviewed/")
        upload_json_to_s3(batch_bucket, reviewed_s3_key, reviewed_results)

        logger.info(
            "Uploaded reviewed results to s3://%s/%s",
            batch_bucket,
            reviewed_s3_key,
        )

        # 6. Log metrics
        from utils.emf_metrics import emf_metrics

        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "IssuesReviewed": len(issues),
                "DecisionsValid": decisions.get("VALID", 0),
                "DecisionsInvalid": decisions.get("INVALID", 0),
                "DecisionsNeedsReview": decisions.get("NEEDS_REVIEW", 0),
                "ProcessingTimeSeconds": round(processing_time, 2),
            },
            dimensions={},
            properties={
                "execution_id": execution_id,
                "image_id": image_id,
                "receipt_id": receipt_id,
            },
            units={"ProcessingTimeSeconds": "Seconds"},
        )

        # Flush LangSmith traces
        flush_langsmith_traces()

        return {
            "status": "completed",
            "reviewed_results_s3_key": reviewed_s3_key,
            "issues_reviewed": len(issues),
            "decisions": dict(decisions),
        }

    except Exception as e:
        logger.error("Error in LLM review: %s", e, exc_info=True)

        from utils.emf_metrics import emf_metrics

        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "LLMReviewFailed": 1,
                "ProcessingTimeSeconds": round(processing_time, 2),
            },
            dimensions={},
            properties={"execution_id": execution_id, "error": str(e)},
            units={"ProcessingTimeSeconds": "Seconds"},
        )

        flush_langsmith_traces()

        return {
            "status": "error",
            "error": str(e),
            "issues_reviewed": 0,
            "decisions": {},
        }


def _build_review_prompt(issue: dict[str, Any]) -> str:
    """Build LLM review prompt for an issue."""
    # Core labels definition
    core_labels = """
- MERCHANT_NAME: Trading name or brand of the store
- STORE_HOURS: Business hours printed on receipt
- PHONE_NUMBER: Store telephone number
- WEBSITE: Web or email address
- LOYALTY_ID: Customer loyalty/rewards ID
- ADDRESS_LINE: Full address line
- DATE: Transaction date
- TIME: Transaction time
- PAYMENT_METHOD: Payment instrument (VISA, CASH, etc.)
- COUPON: Coupon code or description
- DISCOUNT: Non-coupon discount line
- PRODUCT_NAME: Name of product being purchased
- QUANTITY: Number of units purchased
- UNIT_PRICE: Price per unit
- LINE_TOTAL: Total for a line item
- SUBTOTAL: Subtotal before tax
- TAX: Tax amount
- GRAND_TOTAL: Final total paid
- CHANGE: Change returned to customer
- CASH_BACK: Cash back amount
- REFUND: Refund amount
"""

    prompt = f"""You are reviewing a flagged label issue on a receipt.

## CORE_LABELS Definitions
{core_labels}

## Issue Details
- **Word**: "{issue.get('word_text')}"
- **Current Label**: {issue.get('current_label') or 'None'}
- **Issue Type**: {issue.get('type')}
- **Evaluator Reasoning**: {issue.get('reasoning', 'No reasoning provided')[:500]}

## Your Task

Decide if the current label is correct:

- **VALID**: The label IS correct despite the flag (false positive)
- **INVALID**: The label IS wrong - provide the correct label from CORE_LABELS,
  or null if no label applies
- **NEEDS_REVIEW**: Genuinely ambiguous, needs human review

Respond with ONLY a JSON object:
{{"decision": "VALID|INVALID|NEEDS_REVIEW", "reasoning": "your explanation",
 "suggested_label": "LABEL_NAME or null"}}
"""
    return prompt


def _parse_llm_response(response_text: str) -> dict[str, Any]:
    """Parse LLM JSON response."""

    # Handle markdown code blocks
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
        response_text = response_text.strip()

    try:
        result = json.loads(response_text)
        decision = result.get("decision", "NEEDS_REVIEW")
        if decision not in ("VALID", "INVALID", "NEEDS_REVIEW"):
            decision = "NEEDS_REVIEW"

        return {
            "decision": decision,
            "reasoning": result.get("reasoning", "No reasoning provided"),
            "suggested_label": result.get("suggested_label"),
        }
    except json.JSONDecodeError as e:
        return {
            "decision": "NEEDS_REVIEW",
            "reasoning": f"Failed to parse LLM response: {response_text[:200]}",
            "error": str(e),
        }
