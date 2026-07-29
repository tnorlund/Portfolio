#!/usr/bin/env python3
"""Run QA-agent questions locally and inject results into the dev viz cache.

Runs the LOCAL working-copy receipt_agent code (unmerged edits included)
against the dev stack's DynamoDB table and Chroma Cloud database, builds
the same question-{i}.json payloads the EMR job produces, and uploads them
to the dev cache bucket so dev.tylernorlund.com serves them immediately —
no merge, CI, deploy, step function, LangSmith export, or EMR required.

Usage:
    python3.12 scripts/local_qa_run.py --questions 24,13,10   # subset
    python3.12 scripts/local_qa_run.py --all                  # all 32
    python3.12 scripts/local_qa_run.py --all --dry-run        # no upload
    python3.12 scripts/local_qa_run.py --questions 24 --model openai/gpt-5.6-luna

Credentials/env are pulled from the deployed dev run-question Lambda so
local runs always match the dev stack's configuration. The previous cache
files for touched questions are backed up to backup/<ts>/ in the bucket.
"""

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone

import boto3

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("receipt_agent", "receipt_dynamo", "receipt_chroma"):
    sys.path.insert(0, os.path.join(REPO, pkg))

DEV_LAMBDA_HINT = "run-question"
DEV_STACK_SUBSTR = "dev"
ENV_KEYS = [
    "DYNAMODB_TABLE_NAME",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "RECEIPT_AGENT_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "CHROMA_CLOUD_API_KEY",
    "CHROMA_CLOUD_TENANT",
    "CHROMA_CLOUD_DATABASE",
]


def load_run_question_module():
    """Import the Lambda module for QUESTIONS + callbacks without a package."""
    path = os.path.join(
        REPO, "infra", "qa_agent_step_functions", "lambdas", "run_question.py"
    )
    spec = importlib.util.spec_from_file_location("rq_lambda", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def bootstrap_env_from_dev_lambda(model_override=None):
    lam = boto3.client("lambda", region_name="us-east-1")
    fns = []
    for page in lam.get_paginator("list_functions").paginate():
        fns.extend(page["Functions"])
    name = next(
        f["FunctionName"]
        for f in fns
        if DEV_LAMBDA_HINT in f["FunctionName"]
        and DEV_STACK_SUBSTR in f["FunctionName"]
    )
    env = lam.get_function_configuration(FunctionName=name)["Environment"][
        "Variables"
    ]
    for k in ENV_KEYS:
        if k in env and k not in os.environ:
            os.environ[k] = env[k]
    if model_override:
        os.environ["OPENROUTER_MODEL"] = model_override
    # Local runs skip LangSmith; the cache is built directly.
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ.pop("LANGCHAIN_API_KEY", None)
    return name, env.get("BATCH_BUCKET", "")


def enrich_evidence(evidence, dynamo_client, cache):
    """Mirror qa_viz_cache_helpers._enrich_evidence using live Dynamo."""
    enriched = []
    for e in evidence:
        image_id = e.get("image_id") or e.get("imageId")
        receipt_id = e.get("receipt_id") or e.get("receiptId")
        if not image_id or receipt_id is None:
            continue
        key = f"{image_id}_{receipt_id}"
        if key not in cache:
            try:
                receipt = dynamo_client.get_receipt(image_id, int(receipt_id))
                cache[key] = {
                    "thumbnailKey": getattr(receipt, "cdn_webp_s3_key", "")
                    or getattr(receipt, "cdn_s3_key", "")
                    or "",
                    "width": getattr(receipt, "width", 0) or 0,
                    "height": getattr(receipt, "height", 0) or 0,
                }
            except Exception:
                cache[key] = None
        meta = cache[key]
        if not meta or not meta["thumbnailKey"]:
            continue
        enriched.append(
            {
                "imageId": image_id,
                "merchant": e.get("merchant", ""),
                "item": e.get("item", ""),
                "amount": e.get("amount", 0),
                "thumbnailKey": meta["thumbnailKey"],
                "width": meta["width"],
                "height": meta["height"],
            }
        )
    return enriched


def build_cache_payload(result, dynamo_client, lookup_cache):
    """Build question-{i}.json from a local run result.

    Uses the TraceCaptureCallback node events for real phase timings and
    attaches the synthesized answer + enriched evidence to the synthesize
    step, matching what the frontend's QAAgentFlow expects.
    """
    events = result.get("trace") or []
    t0 = min((e["start_ts"] for e in events if e.get("start_ts")), default=0)
    steps = []
    searches = list(result.get("_searches") or [])
    search_i = 0
    for e in events:
        step = {
            "type": e["type"],
            "content": "",
            "detail": "",
            "durationMs": e.get("duration_ms") or 0,
            "startOffsetMs": (
                round((e["start_ts"] - t0) * 1000, 1)
                if e.get("start_ts")
                else 0
            ),
        }
        if e["type"] == "plan":
            step["content"] = "Question classified"
        elif e["type"] == "agent":
            step["content"] = "Reasoning about the question"
        elif e["type"] == "tools":
            if search_i < len(searches):
                s = searches[search_i]
                step["content"] = s.get("type", "search")
                step["detail"] = json.dumps(
                    {"query": s.get("query", "")}, default=str
                )
                search_i += 1
            else:
                step["content"] = "Tool"
        elif e["type"] == "shape":
            step["content"] = (
                f"{result.get('receiptCount', 0)} receipts shaped"
            )
        elif e["type"] == "synthesize":
            step["content"] = result.get("answer", "") or "Answer generated"
            step["detail"] = (
                f"{result.get('receiptCount', 0)} receipts identified"
            )
            step["receipts"] = enrich_evidence(
                result.get("evidence", []), dynamo_client, lookup_cache
            )
        steps.append(step)

    # Guarantee a synthesize step even if the callback missed the node.
    if not any(s["type"] == "synthesize" for s in steps):
        steps.append(
            {
                "type": "synthesize",
                "content": result.get("answer", "") or "Answer generated",
                "detail": "",
                "durationMs": 0,
                "startOffsetMs": 0,
                "receipts": enrich_evidence(
                    result.get("evidence", []), dynamo_client, lookup_cache
                ),
            }
        )

    return {
        "question": result.get("question", ""),
        "questionIndex": result.get("questionIndex", 0),
        "traceId": f"local-{result.get('questionIndex', 0)}",
        "trace": steps,
        "stats": {
            "llmCalls": result.get("llmCalls", 0),
            "toolInvocations": result.get("toolInvocations", 0),
            "receiptsProcessed": result.get("receiptCount", 0),
            "cost": result.get("cost", 0),
        },
    }


async def run_questions(rq, indexes, concurrency):
    from receipt_agent.agents.question_answering import (
        answer_question,
        create_qa_graph,
    )
    from receipt_agent.clients.factory import (
        create_chroma_client,
        create_dynamo_client,
        create_embed_fn,
    )

    dynamo_client = create_dynamo_client(
        table_name=os.environ["DYNAMODB_TABLE_NAME"]
    )
    chroma_client = create_chroma_client(mode="read")
    embed_fn = create_embed_fn()

    sem = asyncio.Semaphore(concurrency)

    async def one(i):
        result = await rq._run_question(
            sem,
            answer_question,
            create_qa_graph,
            dynamo_client,
            chroma_client,
            embed_fn,
            rq.QUESTIONS[i],
            i,
        )
        status = "ok" if result.get("success") else "ERROR"
        print(
            f"  Q{i:02d} [{status}] {result.get('durationSeconds', 0):.0f}s "
            f"${result.get('cost', 0):.3f} "
            f"{len(result.get('evidence', []))} evidence | "
            f"{(result.get('answer') or '')[:70]!r}",
            flush=True,
        )
        return result

    results = await asyncio.gather(*(one(i) for i in indexes))
    return results, dynamo_client


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", help="comma-separated indexes, e.g. 24,13")
    ap.add_argument("--all", action="store_true", help="run all questions")
    ap.add_argument("--model", help="OPENROUTER_MODEL override")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="write JSON locally, skip S3 upload",
    )
    ap.add_argument(
        "--out",
        default=os.path.join(REPO, ".local-qa-out"),
        help="local output dir",
    )
    args = ap.parse_args()

    if not args.all and not args.questions:
        ap.error("pass --questions or --all")

    fn_name, batch_bucket = bootstrap_env_from_dev_lambda(args.model)
    print(f"env from {fn_name}; cache bucket: {batch_bucket}")
    print(f"model: {os.environ.get('OPENROUTER_MODEL')}")
    print(f"table: {os.environ.get('DYNAMODB_TABLE_NAME')}")

    rq = load_run_question_module()
    indexes = (
        list(range(len(rq.QUESTIONS)))
        if args.all
        else [int(x) for x in args.questions.split(",")]
    )
    print(f"running {len(indexes)} question(s): {indexes}")

    t0 = time.time()
    results, dynamo_client = asyncio.run(
        run_questions(rq, indexes, args.concurrency)
    )
    print(f"ran {len(results)} questions in {time.time() - t0:.0f}s")

    os.makedirs(args.out, exist_ok=True)
    lookup_cache = {}
    payloads = []
    for r in results:
        # searches drive tools-step detail; stash before payload build
        r["_searches"] = r.get("_searches") or []
        payloads.append(build_cache_payload(r, dynamo_client, lookup_cache))

    for p in payloads:
        path = os.path.join(args.out, f"question-{p['questionIndex']}.json")
        with open(path, "w") as f:
            json.dump(p, f, default=str)
        print(f"wrote {path}")

    if args.dry_run:
        print("dry run: skipping upload")
        return

    s3 = boto3.client("s3", region_name="us-east-1")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Back up current cache files for the touched questions + metadata.
    for p in payloads:
        key = f"questions/question-{p['questionIndex']}.json"
        try:
            s3.copy_object(
                Bucket=batch_bucket,
                CopySource={"Bucket": batch_bucket, "Key": key},
                Key=f"backup/{ts}/{key}",
            )
        except Exception:
            pass
    try:
        s3.copy_object(
            Bucket=batch_bucket,
            CopySource={"Bucket": batch_bucket, "Key": "metadata.json"},
            Key=f"backup/{ts}/metadata.json",
        )
    except Exception:
        pass

    for p in payloads:
        key = f"questions/question-{p['questionIndex']}.json"
        s3.put_object(
            Bucket=batch_bucket,
            Key=key,
            Body=json.dumps(p, default=str).encode(),
            ContentType="application/json",
        )
        print(f"uploaded s3://{batch_bucket}/{key}")

    # Patch metadata so the frontend shows the local run's provenance.
    try:
        meta = json.loads(
            s3.get_object(Bucket=batch_bucket, Key="metadata.json")[
                "Body"
            ].read()
        )
    except Exception:
        meta = {}
    meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    meta["execution_id"] = f"local-{ts}"
    meta["langsmith_project"] = "local-run"
    meta["local_injected_questions"] = sorted(
        p["questionIndex"] for p in payloads
    )
    s3.put_object(
        Bucket=batch_bucket,
        Key="metadata.json",
        Body=json.dumps(meta, default=str).encode(),
        ContentType="application/json",
    )
    print(f"metadata patched (execution_id=local-{ts})")
    print("dev API cache max-age is 60s; refresh the page after a minute.")


if __name__ == "__main__":
    main()
