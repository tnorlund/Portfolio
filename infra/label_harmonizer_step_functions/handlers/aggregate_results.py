"""
Aggregate Results Handler (Zip Lambda)

Combines all harmonization results and generates a summary report.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def _load_results_from_s3(batch_bucket: str, execution_id: str) -> List[Dict]:
    """Load all results from S3 for the given execution_id."""
    results: List[Dict] = []
    prefix = f"results/{execution_id}/"

    try:
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=batch_bucket, Prefix=prefix)

        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".json"):
                    try:
                        response = s3.get_object(Bucket=batch_bucket, Key=key)
                        result = json.loads(response["Body"].read().decode("utf-8"))
                        results.append(result)
                    except Exception as e:
                        logger.warning(f"Failed to load result from {key}: {e}")

        logger.info(f"Loaded {len(results)} results from S3")
    except Exception as e:
        logger.error(f"Failed to load results from S3: {e}")
        raise

    return results


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Aggregate results from all harmonization runs.

    Input:
    {
        "execution_id": "abc123",
        "process_results": [
            {"status": "completed", "outliers_found": 5, ...},
            ...
        ],
        "dry_run": true
    }

    Output:
    {
        "execution_id": "abc123",
        "summary": {
            "total_labels_processed": 12345,
            "total_outliers_found": 234,
            "merchants_processed": 45,
            ...
        },
        "report_path": "s3://bucket/reports/..."
    }
    """
    execution_id = event.get("execution_id", "unknown")
    process_results: List[Dict] = event.get("process_results", [])
    dry_run = event.get("dry_run", True)
    batch_bucket = os.environ.get("BATCH_BUCKET", "")

    logger.info(f"Aggregating results for execution {execution_id}")

    # Always read results from S3 to avoid 256KB payload limit
    # The Step Function only passes batch_count to avoid payload size issues
    # Results are stored at results/{execution_id}/{label_type}/{merchant}.json
    logger.info("Reading results from S3 (payload only contains batch_count)...")
    process_results = _load_results_from_s3(batch_bucket, execution_id)

    logger.info(f"Processing {len(process_results)} results from S3")

    # Aggregate metrics
    total_labels = 0
    total_outliers = 0
    total_merchants = 0
    label_type_stats: Dict[str, Dict] = {}
    outlier_details: List[Dict] = []
    errors: List[Dict] = []

    for result in process_results:
        if not isinstance(result, dict):
            logger.warning(f"Skipping non-dict result: {result}")
            continue

        status = result.get("status", "unknown")
        if status == "error":
            errors.append(result)
            continue

        label_type = result.get("label_type", "unknown")
        merchant_name = result.get("merchant_name", "unknown")
        labels_processed = result.get("labels_processed", 0)
        outliers_found = result.get("outliers_found", 0)

        total_labels += labels_processed
        total_outliers += outliers_found
        total_merchants += 1

        # Aggregate by label type
        if label_type not in label_type_stats:
            label_type_stats[label_type] = {
                "labels_processed": 0,
                "outliers_found": 0,
                "merchants_processed": 0,
            }

        label_type_stats[label_type]["labels_processed"] += labels_processed
        label_type_stats[label_type]["outliers_found"] += outliers_found
        label_type_stats[label_type]["merchants_processed"] += 1

        # Collect outlier details from S3 if not in payload
        # Full results are stored at results/{execution_id}/{label_type}/{merchant}.json
        results_path = result.get("results_path")
        if results_path and not result.get("outlier_details"):
            try:
                # Parse S3 path: s3://bucket/key
                if results_path.startswith("s3://"):
                    path_parts = results_path[5:].split("/", 1)
                    if len(path_parts) == 2:
                        bucket, key = path_parts
                        response = s3.get_object(Bucket=bucket, Key=key)
                        full_result = json.loads(response["Body"].read().decode("utf-8"))
                        for outlier in full_result.get("outlier_details", []):
                            outlier_details.append(
                                {
                                    "label_type": label_type,
                                    "merchant_name": merchant_name,
                                    **outlier,
                                }
                            )
            except Exception as e:
                logger.warning(f"Failed to load outlier details from {results_path}: {e}")
        else:
            # Use outlier_details from payload if available
            for outlier in result.get("outlier_details", []):
                outlier_details.append(
                    {
                        "label_type": label_type,
                        "merchant_name": merchant_name,
                        **outlier,
                    }
                )

    # Build summary
    summary = {
        "execution_id": execution_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "dry_run": dry_run,
        "totals": {
            "labels_processed": total_labels,
            "outliers_found": total_outliers,
            "merchants_processed": total_merchants,
            "label_types_processed": len(label_type_stats),
            "errors": len(errors),
        },
        "by_label_type": label_type_stats,
        "outlier_sample": outlier_details[:100],  # First 100 outliers
        "errors": errors[:10],  # First 10 errors
    }

    logger.info(
        f"Summary: {total_labels} labels, {total_outliers} outliers, "
        f"{total_merchants} merchants, {len(errors)} errors"
    )

    # Upload full report to S3
    report_key = f"reports/{execution_id}/summary.json"
    if batch_bucket:
        try:
            s3.put_object(
                Bucket=batch_bucket,
                Key=report_key,
                Body=json.dumps(summary, indent=2, default=str).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(f"Report uploaded to s3://{batch_bucket}/{report_key}")
        except Exception as e:
            logger.error(f"Failed to upload report: {e}")

        # Upload full outlier list
        outliers_key = f"reports/{execution_id}/all_outliers.json"
        try:
            s3.put_object(
                Bucket=batch_bucket,
                Key=outliers_key,
                Body=json.dumps(outlier_details, indent=2, default=str).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(f"Outliers uploaded to s3://{batch_bucket}/{outliers_key}")
        except Exception as e:
            logger.error(f"Failed to upload outliers: {e}")

    return {
        "execution_id": execution_id,
        "summary": summary,
        "report_path": f"s3://{batch_bucket}/{report_key}" if batch_bucket else None,
    }


