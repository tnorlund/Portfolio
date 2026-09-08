#!/usr/bin/env python3
"""Copy an immutable CoreML export if needed, then promote it in one env.

Use the MCP server's Python environment for a live invocation. Dry-run performs
reads only and prints every proposed write. This script never deploys workers
or infrastructure.
"""

import argparse
import asyncio
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError


def _head(s3, bucket, key):
    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


async def promote_model(
    env: str, job_name: str, *, dry_run: bool = False, yes_prod: bool = False
) -> dict:
    if env not in ("dev", "prod"):
        raise ValueError("env must be dev or prod")
    if env == "prod" and not yes_prod:
        raise ValueError("--env prod requires --yes-prod, including --dry-run")

    from scripts.receipt_mcp_server import (
        coreml_bundle_reference,
        set_active_model_impl,
    )

    target = load_env(env=env)
    bucket = target["layoutlm_training_bucket"]
    table = target["dynamodb_table_name"]
    dynamo = DynamoClient(table_name=table)
    s3 = boto3.client("s3")
    jobs, _ = dynamo.get_job_by_name(job_name)
    source_env = "dev" if env == "prod" else "prod"
    source = None
    copy_job = False
    if jobs:
        job = jobs[0]
    else:
        source = load_env(env=source_env)
        source_dynamo = DynamoClient(table_name=source["dynamodb_table_name"])
        jobs, _ = source_dynamo.get_job_by_name(job_name)
        if not jobs:
            raise ValueError(f"No job found with name: {job_name}")
        job = jobs[0]
        # A name index is eventually consistent; check the primary key before
        # planning an insert and preserve any existing target entity.
        try:
            job = dynamo.get_job(job.job_id)
        except EntityNotFoundError:
            copy_job = True

    export_id, key = coreml_bundle_reference(job)
    writes = []
    source_bucket = None
    if _head(s3, bucket, key) is None:
        results = job.results or {}
        if isinstance(results, str):
            results = json.loads(results)
        uri = results.get("coreml_versioned_bundle_s3_uri")
        source_bucket = urlparse(uri).netloc if uri else None
        if not source_bucket or source_bucket == bucket:
            source = source or load_env(env=source_env)
            source_bucket = source["layoutlm_training_bucket"]
        if _head(s3, source_bucket, key) is None:
            raise ValueError(
                f"Exported bundle not found: s3://{source_bucket}/{key}"
            )
        writes.append(
            {
                "operation": "copy_bundle",
                "source": f"s3://{source_bucket}/{key}",
                "target": f"s3://{bucket}/{key}",
            }
        )
    if copy_job:
        writes.append(
            {
                "operation": "copy_job",
                "source_table": source["dynamodb_table_name"],
                "target_table": table,
                "job_id": job.job_id,
            }
        )
    active = dynamo.get_active_model_job()
    if active and active.job_id != job.job_id:
        writes.append(
            {
                "operation": "clear_active_tag",
                "table": table,
                "job_id": active.job_id,
            }
        )
    writes.extend(
        [
            {
                "operation": "set_active_tag",
                "table": table,
                "job_id": job.job_id,
            },
            {
                "operation": "put_pointer",
                "target": f"s3://{bucket}/coreml/active.json",
            },
        ]
    )
    plan = {
        "env": env,
        "export_id": export_id,
        "dry_run": dry_run,
        "writes": writes,
    }
    if dry_run:
        return plan

    if source_bucket:
        # Conditional destination creation keeps a racing promotion from
        # overwriting an immutable version. Stream the source without loading
        # the full model into Python memory.
        source_object = s3.get_object(Bucket=source_bucket, Key=key)
        body = source_object["Body"]
        try:
            with tempfile.TemporaryFile() as bundle:
                shutil.copyfileobj(body, bundle)
                bundle.seek(0)
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=bundle,
                    ContentLength=source_object["ContentLength"],
                    ContentType="application/zip",
                    IfNoneMatch="*",
                )
        finally:
            body.close()
    if copy_job:
        copied = copy.deepcopy(job)
        copied.tags = {
            k: v for k, v in (copied.tags or {}).items() if k != "active_model"
        }
        results = copied.results or {}
        if isinstance(results, str):
            results = json.loads(results)
        copied.results = {
            **results,
            "coreml_versioned_bundle_s3_uri": f"s3://{bucket}/{key}",
            "coreml_versioned_bundle_etag": s3.head_object(
                Bucket=bucket, Key=key
            )["ETag"],
        }
        dynamo.add_job(copied)
        job = copied
    result = await set_active_model_impl(
        dynamo,
        job_name,
        training_bucket=bucket,
        s3_client=s3,
        resolved_job=job,
    )
    return {**plan, **result}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("dev", "prod"), required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes-prod", action="store_true")
    args = parser.parse_args(argv)
    if args.env == "prod" and not args.yes_prod:
        parser.error("--env prod requires --yes-prod, including --dry-run")
    try:
        result = asyncio.run(promote_model(**vars(args)))
    except Exception as error:
        print(json.dumps({"success": False, "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("success", args.dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
