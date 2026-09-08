#!/usr/bin/env python3
"""Import built Lambda images on their actual runtime, without service access.

The deployment images and handler modules are unchanged. Only a DescribeTable
call made during DynamoClient construction is stubbed; any other AWS request
fails. The caller also disables container networking and supplies no secrets.
This verifies image/package/handler imports, not live handler behavior.
"""

import argparse
import importlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

IMAGES = [
    (
        "analytics",
        "infra/components/web_analytics/ga_extract_lambda/Dockerfile",
        "handler.handler",
        ["google.analytics.data_v1beta"],
    ),
    (
        "fix-place",
        "infra/fix_place_lambda/lambdas/Dockerfile",
        "handler.handler",
        ["receipt_agent", "receipt_upload.combine", "numpy", "PIL.Image"],
    ),
    (
        "glyph-mcp",
        "infra/glyph_mcp_lambda/lambdas/Dockerfile",
        "handler.handler",
        ["glyph_mcp_server.server", "numpy", "PIL.Image"],
    ),
    (
        "receipt-mcp",
        "infra/mcp_server_lambda/lambdas/Dockerfile",
        "handler.handler",
        ["receipt_mcp_server.server", "receipt_agent", "numpy"],
    ),
    (
        "merge-receipt",
        "infra/merge_receipt_lambda/lambdas/Dockerfile",
        "handler.handler",
        [
            "receipt_upload.combine",
            "receipt_agent.lifecycle.receipt_manager",
            "numpy",
            "PIL.Image",
        ],
    ),
    (
        "qa-agent",
        "infra/qa_agent_step_functions/lambdas/Dockerfile",
        "handler.handler",
        ["receipt_agent", "receipt_embeddings"],
    ),
    (
        "resegment",
        "infra/resegment_receipt_lambda/lambdas/Dockerfile",
        "handler.handler",
        [
            "receipt_upload.section_assignment",
            "receipt_dynamo_stream",
            "numpy",
        ],
    ),
    (
        "address-cache",
        "infra/routes/address_similarity_cache_generator/lambdas/Dockerfile",
        "index.handler",
        ["receipt_dynamo", "receipt_embeddings"],
    ),
    (
        "word-cache",
        "infra/routes/word_similarity_cache_generator/lambdas/Dockerfile",
        "index.handler",
        ["receipt_dynamo", "receipt_embeddings"],
    ),
    (
        "trigger-reocr",
        "infra/trigger_reocr_lambda/lambdas/Dockerfile",
        "handler.handler",
        ["receipt_dynamo"],
    ),
    (
        "process-ocr",
        "infra/upload_images/container_ocr/Dockerfile",
        "handler.handler.lambda_handler",
        ["receipt_upload", "receipt_agent", "numpy", "PIL.Image"],
    ),
    (
        "upload",
        "infra/upload_images/container_upload/Dockerfile",
        "handler.handler",
        ["receipt_dynamo"],
    ),
]


def import_image(name: str) -> None:
    # The matrix-planning runner uses only stdlib; botocore lives in images.
    BaseClient = importlib.import_module("botocore.client").BaseClient

    if sys.version_info[:2] != (3, 14):
        raise RuntimeError(f"Expected Python 3.14, got {sys.version}")
    if platform.system() != "Linux" or platform.machine() != "aarch64":
        raise RuntimeError("Run this check inside the Linux ARM64 image")
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    image = next(image for image in IMAGES if image[0] == name)
    for key in (
        "DYNAMODB_TABLE_NAME",
        "DYNAMO_TABLE_NAME",
        "BUCKET_NAME",
        "RAW_BUCKET",
        "SITE_BUCKET",
        "CURATED_BUCKET",
        "GA_PROPERTY_ID",
        "OCR_JOB_QUEUE_URL",
    ):
        os.environ[key] = "ci-import-only"
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_DEFAULT_REGION="us-east-1",
        AWS_REGION="us-east-1",
        AWS_EC2_METADATA_DISABLED="true",
        LANGCHAIN_TRACING_V2="false",
        LANGSMITH_TRACING="false",
    )

    def no_service_calls(client, operation, parameters):
        if (
            client.meta.service_model.service_name == "dynamodb"
            and operation == "DescribeTable"
        ):
            return {
                "Table": {
                    "TableName": parameters["TableName"],
                    "TableStatus": "ACTIVE",
                }
            }
        raise RuntimeError(f"Unexpected import-time AWS call: {operation}")

    with patch.object(BaseClient, "_make_api_call", no_service_calls):
        for module in image[3]:
            importlib.import_module(module)
        module_name, function = image[2].rsplit(".", 1)
        module = importlib.import_module(module_name)
        if not callable(getattr(module, function)):
            raise RuntimeError(f"Handler is not callable: {image[2]}")
    print(
        json.dumps(
            {
                "image": name,
                "python": platform.python_version(),
                "platform": platform.platform(),
                "handler": image[2],
                "imports": "passed",
            }
        )
    )


def build_needed(root: Path, event: str, before: str = "") -> bool:
    """Use the entire pushed range; unknown/shallow history builds conservatively."""
    if event == "workflow_dispatch":
        return True
    base = "HEAD^1" if event == "pull_request" else before
    if (
        not base
        or subprocess.run(
            ["git", "cat-file", "-e", f"{base}^{{commit}}"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
    ):
        return True
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", base, "HEAD"],
        cwd=root,
        text=True,
    ).splitlines()
    return any(
        Path(path).name in {"Dockerfile", "pyproject.toml", "setup.py"}
        or Path(path).name.startswith("requirements")
        or path
        in {
            ".github/workflows/lambda-images.yml",
            "scripts/lambda_image_import_check.py",
        }
        for path in changed
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "image", nargs="?", choices=[image[0] for image in IMAGES]
    )
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--needs-build", action="store_true")
    args = parser.parse_args()
    if args.matrix:
        print(
            json.dumps(
                {
                    "include": [
                        {"name": name, "dockerfile": file}
                        for name, file, _, _ in IMAGES
                    ]
                }
            )
        )
    elif args.needs_build:
        print(
            str(
                build_needed(
                    Path.cwd(),
                    os.environ.get("EVENT_NAME", ""),
                    os.environ.get("BEFORE_SHA", ""),
                )
            ).lower()
        )
    elif args.image:
        import_image(args.image)
    else:
        parser.error("Provide an image name or --matrix")


if __name__ == "__main__":
    main()
