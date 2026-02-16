#!/usr/bin/env python3
"""Run label-evaluator workflows locally using Pulumi dev configuration.

This wrapper loads Pulumi stack outputs/secrets for ``dev``, sets runtime
environment variables, and then runs the existing local evaluator script.

It supports:
1. Direct run by receipt identity (image_id + receipt_id)
2. Replay by LangSmith run_id (extracts image_id/receipt_id from trace payload)

Examples:
    python scripts/dev_label_evaluator.py run <image_id> <receipt_id>
    python scripts/dev_label_evaluator.py run <image_id> <receipt_id> --skip-llm
    python scripts/dev_label_evaluator.py trace <langsmith_run_id>
    python scripts/dev_label_evaluator.py trace <langsmith_run_id> --apply
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def load_dev_config() -> dict[str, str]:
    """Load required config/secrets from Pulumi dev stack."""
    from receipt_dynamo.data._pulumi import load_env, load_secrets

    outputs = load_env("dev")
    secrets = load_secrets("dev")

    config: dict[str, str] = {
        "dynamodb_table_name": str(outputs.get("dynamodb_table_name") or ""),
        "embedding_chromadb_bucket_name": str(
            outputs.get("embedding_chromadb_bucket_name") or ""
        ),
        "langchain_project": str(
            outputs.get("label_validation_project_name") or "label-evaluator-dev"
        ),
        "openrouter_api_key": str(secrets.get("portfolio:OPENROUTER_API_KEY") or ""),
        "langchain_api_key": str(secrets.get("portfolio:LANGCHAIN_API_KEY") or ""),
        "openai_api_key": str(secrets.get("portfolio:OPENAI_API_KEY") or ""),
    }

    if not config["dynamodb_table_name"]:
        raise RuntimeError("Could not load dynamodb_table_name from Pulumi dev")

    return config


def apply_env(config: dict[str, str], model: str | None) -> None:
    """Set env vars expected by local evaluator scripts."""
    table = config["dynamodb_table_name"]

    os.environ["RECEIPT_AGENT_DYNAMO_TABLE_NAME"] = table
    os.environ["RECEIPT_PLACES_TABLE_NAME"] = table
    os.environ["DYNAMODB_TABLE_NAME"] = table
    os.environ["DYNAMO_TABLE_NAME"] = table

    bucket = config.get("embedding_chromadb_bucket_name")
    if bucket:
        os.environ["EMBEDDING_CHROMADB_BUCKET_NAME"] = bucket

    if config.get("openrouter_api_key"):
        os.environ["OPENROUTER_API_KEY"] = config["openrouter_api_key"]

    if config.get("openai_api_key"):
        os.environ["OPENAI_API_KEY"] = config["openai_api_key"]

    if config.get("langchain_api_key"):
        os.environ["LANGCHAIN_API_KEY"] = config["langchain_api_key"]
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_PROJECT"] = config["langchain_project"]

    os.environ.setdefault("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    if model:
        os.environ["OPENROUTER_MODEL"] = model
    else:
        os.environ.setdefault("OPENROUTER_MODEL", "openai/gpt-oss-120b")


def _coerce_receipt_id(value: Any) -> int | None:
    """Try to coerce an arbitrary value to an integer receipt_id."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _find_receipt_identity(obj: Any) -> tuple[str, int] | None:
    """Recursively find ``(image_id, receipt_id)`` in nested run payload data."""
    if isinstance(obj, dict):
        image_val = obj.get("image_id")
        receipt_val = obj.get("receipt_id")
        if isinstance(image_val, str):
            receipt_id = _coerce_receipt_id(receipt_val)
            if image_val.strip() and receipt_id is not None:
                return image_val.strip(), receipt_id

        for value in obj.values():
            found = _find_receipt_identity(value)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = _find_receipt_identity(item)
            if found is not None:
                return found

    return None


def resolve_receipt_from_langsmith(run_id: str) -> tuple[str, int]:
    """Resolve image_id/receipt_id from a LangSmith run payload."""
    try:
        from langsmith import Client
    except ImportError as exc:
        raise RuntimeError(
            "langsmith package is not installed. Install it in your venv."
        ) from exc

    api_key = os.environ.get("LANGCHAIN_API_KEY")
    if not api_key:
        raise RuntimeError("LANGCHAIN_API_KEY is not set. Could not query LangSmith.")

    client = Client(api_key=api_key)
    run = client.read_run(run_id)

    candidate_payloads: list[Any] = []

    # langsmith schemas expose these as attributes; keep getattr fallback safe.
    for field in ("inputs", "outputs", "extra", "metadata"):
        value = getattr(run, field, None)
        if value is not None:
            candidate_payloads.append(value)

    run_dict = None
    if hasattr(run, "model_dump"):
        run_dict = run.model_dump()
    elif hasattr(run, "dict"):
        run_dict = run.dict()
    if isinstance(run_dict, dict):
        candidate_payloads.append(run_dict)

    for payload in candidate_payloads:
        found = _find_receipt_identity(payload)
        if found is not None:
            return found

    raise RuntimeError(
        "Could not find image_id/receipt_id in LangSmith run payload. "
        "Pass explicit run mode with image_id + receipt_id instead."
    )


def run_evaluator(
    image_id: str,
    receipt_id: int,
    *,
    apply: bool,
    skip_llm: bool,
    skip_patterns: bool,
    verbose: bool,
) -> int:
    """Execute the existing local evaluator script with configured env."""
    target_script = PROJECT_ROOT / "scripts" / "evaluate_single_receipt.py"
    cmd = [sys.executable, str(target_script), image_id, str(receipt_id)]

    if apply:
        cmd.append("--apply")
    if skip_llm:
        cmd.append("--skip-llm")
    if skip_patterns:
        cmd.append("--skip-patterns")
    if verbose:
        cmd.append("--verbose")

    completed = subprocess.run(cmd, check=False)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(
        description="Run label evaluator locally with Pulumi dev config"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional OPENROUTER_MODEL override",
    )

    subparsers = parser.add_subparsers(dest="mode", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Run evaluator for a specific image_id/receipt_id"
    )
    run_parser.add_argument("image_id")
    run_parser.add_argument("receipt_id", type=int)

    trace_parser = subparsers.add_parser(
        "trace", help="Replay evaluator using image_id/receipt_id from LangSmith run"
    )
    trace_parser.add_argument("run_id")

    for sub in (run_parser, trace_parser):
        sub.add_argument(
            "--apply",
            action="store_true",
            help="Apply invalid decisions to DynamoDB",
        )
        sub.add_argument(
            "--skip-llm",
            action="store_true",
            help="Skip LLM review and run compute-only",
        )
        sub.add_argument(
            "--skip-patterns",
            action="store_true",
            help="Skip pattern discovery",
        )
        sub.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help="Verbose output",
        )

    return parser


def main() -> int:
    """CLI entrypoint."""
    args = build_parser().parse_args()

    config = load_dev_config()
    apply_env(config, model=args.model)

    if args.mode == "run":
        image_id = args.image_id
        receipt_id = args.receipt_id
    else:
        image_id, receipt_id = resolve_receipt_from_langsmith(args.run_id)
        print(f"Resolved LangSmith run {args.run_id} -> " f"{image_id}#{receipt_id}")

    print(
        "Using dev config: "
        f"table={config['dynamodb_table_name']} "
        f"project={os.environ.get('LANGCHAIN_PROJECT','')}"
    )

    return run_evaluator(
        image_id,
        receipt_id,
        apply=args.apply,
        skip_llm=args.skip_llm,
        skip_patterns=args.skip_patterns,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
