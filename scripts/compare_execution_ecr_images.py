#!/usr/bin/env python3
"""Compare ECR images used in step function executions.

This script analyzes two step function executions to determine which ECR images
were used by the Lambda functions at the time of execution.
"""

import json
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Step function ARNs (update with actual ARNs)
LINE_SF_NAME = "line-ingest-sf-dev-1554303"
WORD_SF_NAME = "word-ingest-sf-dev-8fa425a"

# Execution IDs to compare
SUCCESS_EXECUTION = "885ea78b-e884-4fe3-94ae-7e29736cc4fe"
FAILED_EXECUTION = "a7bfaf55-8500-4e63-a4d8-bdea190d0c4a"

# Lambda functions that might be invoked
LAMBDA_FUNCTIONS = [
    "embedding-line-poll-lambda-dev",
    "embedding-word-poll-lambda-dev",
    "embedding-vector-compact-lambda-dev",
    "embedding-list-pending-lambda-dev",
    "embedding-split-into-chunks-lambda-dev",
]


def run_aws_command(cmd: List[str]) -> Dict:
    """Run an AWS CLI command and return JSON output."""
    try:
        result = subprocess.run(
            ["aws"] + cmd + ["--output", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {' '.join(cmd)}", file=sys.stderr)
        print(f"Error: {e.stderr}", file=sys.stderr)
        return {}
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}", file=sys.stderr)
        return {}


def get_step_function_arn(sf_name: str) -> Optional[str]:
    """Get the ARN for a step function by name."""
    result = run_aws_command(
        [
            "stepfunctions",
            "list-state-machines",
        ]
    )
    for sf in result.get("stateMachines", []):
        if sf_name in sf.get("name", ""):
            return sf.get("stateMachineArn")
    return None


def get_execution_details(sf_arn: str, execution_id: str) -> Optional[Dict]:
    """Get execution details including start time and status."""
    # Try to find execution by listing and filtering
    result = run_aws_command(
        [
            "stepfunctions",
            "list-executions",
            "--state-machine-arn",
            sf_arn,
            "--max-results",
            "1000",
        ]
    )
    for execution in result.get("executions", []):
        if execution_id in execution.get("executionArn", ""):
            # Get full execution details
            exec_arn = execution.get("executionArn")
            details = run_aws_command(
                [
                    "stepfunctions",
                    "describe-execution",
                    "--execution-arn",
                    exec_arn,
                ]
            )
            return details
    return None


def get_execution_history(sf_arn: str, execution_id: str) -> List[Dict]:
    """Get execution history to find invoked Lambda functions."""
    # Find execution ARN
    result = run_aws_command(
        [
            "stepfunctions",
            "list-executions",
            "--state-machine-arn",
            sf_arn,
            "--max-results",
            "1000",
        ]
    )
    exec_arn = None
    for execution in result.get("executions", []):
        if execution_id in execution.get("executionArn", ""):
            exec_arn = execution.get("executionArn")
            break

    if not exec_arn:
        return []

    # Get execution history
    history = run_aws_command(
        [
            "stepfunctions",
            "get-execution-history",
            "--execution-arn",
            exec_arn,
            "--max-results",
            "1000",
        ]
    )
    return history.get("events", [])


def extract_lambda_functions_from_history(history: List[Dict]) -> List[str]:
    """Extract Lambda function names from execution history."""
    lambda_functions = set()
    for event in history:
        event_type = event.get("type")
        if event_type == "TaskStateEntered":
            resource = event.get("taskStateEnteredEventDetails", {}).get("resource")
            if resource and "lambda:" in resource:
                # Extract function name from ARN
                # Format: arn:aws:lambda:region:account:function:name
                parts = resource.split(":")
                if len(parts) >= 7:
                    func_name = parts[6]
                    lambda_functions.add(func_name)
        elif event_type == "LambdaFunctionScheduled":
            resource = event.get("lambdaFunctionScheduledEventDetails", {}).get("resource")
            if resource:
                parts = resource.split(":")
                if len(parts) >= 7:
                    func_name = parts[6]
                    lambda_functions.add(func_name)
    return sorted(list(lambda_functions))


def get_lambda_image_uri(function_name: str) -> Optional[str]:
    """Get the current ECR image URI for a Lambda function."""
    result = run_aws_command(
        [
            "lambda",
            "get-function",
            "--function-name",
            function_name,
        ]
    )
    code = result.get("Code", {})
    return code.get("ImageUri")


def get_lambda_last_modified(function_name: str) -> Optional[str]:
    """Get the last modified timestamp for a Lambda function."""
    result = run_aws_command(
        [
            "lambda",
            "get-function-configuration",
            "--function-name",
            function_name,
        ]
    )
    return result.get("LastModified")


def get_ecr_image_details(image_uri: str) -> Dict:
    """Get ECR image details from image URI."""
    # Parse image URI: account.dkr.ecr.region.amazonaws.com/repo@sha256:digest
    # or: account.dkr.ecr.region.amazonaws.com/repo:tag
    if "@" in image_uri:
        repo_and_digest = image_uri.split("@")
        repo = repo_and_digest[0]
        digest = repo_and_digest[1]
    else:
        # Try to extract from tag format
        parts = image_uri.split("/")
        repo = "/".join(parts[:-1])
        tag = parts[-1]
        digest = None

    # Extract repository name
    repo_name = repo.split("/")[-1]

    # Get image details from ECR
    if digest:
        result = run_aws_command(
            [
                "ecr",
                "describe-images",
                "--repository-name",
                repo_name,
                "--image-ids",
                f"imageDigest={digest}",
            ]
        )
        images = result.get("imageDetails", [])
        if images:
            return images[0]
    else:
        # Try to get by tag
        result = run_aws_command(
            [
                "ecr",
                "describe-images",
                "--repository-name",
                repo_name,
                "--image-ids",
                f"imageTag={tag}",
            ]
        )
        images = result.get("imageDetails", [])
        if images:
            return images[0]

    return {}


def get_lambda_image_at_time(function_name: str, execution_time: str) -> Optional[Dict]:
    """Try to determine which image was used at execution time.

    This checks ECR image push times relative to execution time.
    """
    # Get current image
    current_image_uri = get_lambda_image_uri(function_name)
    if not current_image_uri:
        return None

    # Get last modified time
    last_modified = get_lambda_last_modified(function_name)

    # Extract repository name from image URI
    if "@" in current_image_uri:
        repo = current_image_uri.split("@")[0]
    else:
        repo = current_image_uri.rsplit(":", 1)[0]

    repo_name = repo.split("/")[-1]

    # Get all images from ECR
    result = run_aws_command(
        [
            "ecr",
            "describe-images",
            "--repository-name",
            repo_name,
            "--max-items",
            "50",
        ]
    )

    # Parse execution time
    try:
        exec_dt = datetime.fromisoformat(execution_time.replace("Z", "+00:00"))
    except Exception as e:
        print(f"Error parsing execution time: {e}", file=sys.stderr)
        exec_dt = None

    # Find image that was pushed just before or at execution time
    images = result.get("imageDetails", [])
    matching_image = None

    if exec_dt:
        for image in sorted(images, key=lambda x: x.get("imagePushedAt", ""), reverse=True):
            pushed_at = image.get("imagePushedAt", "")
            if pushed_at:
                try:
                    pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                    if pushed_dt <= exec_dt:
                        matching_image = image
                        break
                except Exception:
                    continue

    # If no matching image found, use the most recent one
    if not matching_image and images:
        matching_image = sorted(images, key=lambda x: x.get("imagePushedAt", ""), reverse=True)[0]

    if matching_image:
        digest = matching_image.get("imageDigest", "")
        image_uri = f"{repo}@{digest}"
        return {
            "image_uri": image_uri,
            "image_digest": digest,
            "image_pushed_at": matching_image.get("imagePushedAt"),
            "image_tags": matching_image.get("imageTags", []),
            "last_modified": last_modified,
        }

    return {
        "image_uri": current_image_uri,
        "last_modified": last_modified,
    }


def get_ecr_image_before_time(image_uri: str, before_time: str) -> Dict:
    """Get ECR image that was pushed before the given time."""
    # Extract repository name
    if "@" in image_uri:
        repo = image_uri.split("@")[0]
    else:
        repo = image_uri.rsplit(":", 1)[0]

    repo_name = repo.split("/")[-1]

    # Get all images and find one pushed before execution time
    result = run_aws_command(
        [
            "ecr",
            "describe-images",
            "--repository-name",
            repo_name,
            "--max-items",
            "100",
        ]
    )

    exec_dt = datetime.fromisoformat(before_time.replace("Z", "+00:00"))

    # Find image pushed just before execution time
    images = result.get("imageDetails", [])
    for image in sorted(images, key=lambda x: x.get("imagePushedAt", ""), reverse=True):
        pushed_at = image.get("imagePushedAt", "")
        if pushed_at:
            try:
                pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                if pushed_dt <= exec_dt:
                    return image
            except Exception:
                continue

    return {}


def analyze_execution(execution_id: str, sf_name: str) -> Dict:
    """Analyze an execution to determine ECR images used."""
    print(f"\n{'='*80}")
    print(f"Analyzing execution: {execution_id}")
    print(f"Step Function: {sf_name}")
    print(f"{'='*80}\n")

    # Get step function ARN
    sf_arn = get_step_function_arn(sf_name)
    if not sf_arn:
        print(f"ERROR: Could not find step function: {sf_name}")
        return {}

    print(f"Step Function ARN: {sf_arn}\n")

    # Get execution details
    exec_details = get_execution_details(sf_arn, execution_id)
    if not exec_details:
        print(f"ERROR: Could not find execution: {execution_id}")
        return {}

    start_time = exec_details.get("startDate")
    stop_time = exec_details.get("stopDate")
    status = exec_details.get("status")

    print(f"Status: {status}")
    print(f"Start Time: {start_time}")
    print(f"Stop Time: {stop_time}\n")

    # Get execution history
    print("Getting execution history...")
    history = get_execution_history(sf_arn, execution_id)
    print(f"Found {len(history)} events in history\n")

    # Extract Lambda functions
    lambda_functions = extract_lambda_functions_from_history(history)
    print(f"Lambda functions invoked: {lambda_functions}\n")

    # Get image info for each Lambda
    lambda_images = {}
    for func_name in lambda_functions:
        print(f"Checking {func_name}...")
        image_info = get_lambda_image_at_time(func_name, start_time)
        if image_info:
            lambda_images[func_name] = image_info
            image_uri = image_info.get("image_uri", "Unknown")
            print(f"  Image URI: {image_uri}")
            if "image_pushed_at" in image_info:
                print(f"  Image Pushed At: {image_info.get('image_pushed_at')}")
            if "image_digest" in image_info:
                digest = image_info.get("image_digest", "")
                print(f"  Image Digest: {digest[:20]}...")
            if "image_tags" in image_info:
                tags = image_info.get("image_tags", [])
                if tags:
                    print(f"  Image Tags: {', '.join(tags)}")
            if "last_modified" in image_info:
                print(f"  Lambda Last Modified: {image_info.get('last_modified')}")
        else:
            print(f"  Could not determine image")
        print()

    return {
        "execution_id": execution_id,
        "status": status,
        "start_time": start_time,
        "stop_time": stop_time,
        "lambda_functions": lambda_functions,
        "lambda_images": lambda_images,
    }


def main():
    """Main function to compare executions."""
    print("="*80)
    print("ECR Image Comparison for Step Function Executions")
    print("="*80)

    # Analyze successful execution
    success_data = analyze_execution(SUCCESS_EXECUTION, LINE_SF_NAME)

    # Analyze failed execution
    failed_data = analyze_execution(FAILED_EXECUTION, LINE_SF_NAME)

    # Compare results
    print("\n" + "="*80)
    print("COMPARISON")
    print("="*80 + "\n")

    if not success_data or not failed_data:
        print("ERROR: Could not analyze both executions")
        return

    # Compare Lambda functions
    success_funcs = set(success_data.get("lambda_functions", []))
    failed_funcs = set(failed_data.get("lambda_functions", []))

    print("Lambda Functions:")
    print(f"  Success: {sorted(success_funcs)}")
    print(f"  Failed:  {sorted(failed_funcs)}")
    print()

    # Compare images for common functions
    common_funcs = success_funcs & failed_funcs
    print(f"Comparing images for {len(common_funcs)} common Lambda functions:\n")

    for func_name in sorted(common_funcs):
        print(f"{func_name}:")
        success_img = success_data.get("lambda_images", {}).get(func_name, {})
        failed_img = failed_data.get("lambda_images", {}).get(func_name, {})

        success_uri = success_img.get("image_uri", "Unknown")
        failed_uri = failed_img.get("image_uri", "Unknown")

        print(f"  Success execution: {success_uri}")
        print(f"  Failed execution:  {failed_uri}")

        if success_uri != failed_uri and success_uri != "Unknown" and failed_uri != "Unknown":
            print(f"  ⚠️  DIFFERENT IMAGES!")

            # Extract digests for comparison
            success_digest = success_uri.split("@")[-1] if "@" in success_uri else None
            failed_digest = failed_uri.split("@")[-1] if "@" in failed_uri else None

            if success_digest and failed_digest:
                print(f"    Success digest: {success_digest[:20]}...")
                print(f"    Failed digest:  {failed_digest[:20]}...")
        else:
            print(f"  ✅ Same image")
        print()


if __name__ == "__main__":
    main()

