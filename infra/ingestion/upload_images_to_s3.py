from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from time import sleep
from typing import Generator, List
from uuid import uuid4
import time

import boto3
from botocore.exceptions import ClientError
from dynamo import DynamoClient
import pulumi.automation as auto

import requests  # <--- Added import


def calculate_sha256(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def chunked(iterable: List, n: int) -> Generator[List, None, None]:
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]


def load_env(env: str = "dev") -> dict:
    """
    Uses Pulumi to get stack outputs and return them as a dict. 
    Example keys: 
      - 'raw_bucket_name'
      - 'cluster_lambda_function_name'
      - 'dynamodb_table_name'
      - 'cdn_bucket_name'
      etc.
    """
    stack = auto.select_stack(
        stack_name=f"tnorlund/portfolio/{env}",
        project_name="portfolio",
        program=lambda: None,
    )
    return {key: val.value for key, val in stack.outputs().items()}


def run_swift_script(output_directory: Path, image_paths: list[str]) -> bool:
    swift_script = Path(__file__).parent / "OCRSwift.swift"
    try:
        swift_args = ["swift", str(swift_script), str(output_directory)] + image_paths
        subprocess.run(swift_args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        print(f"Error running swift script: {e}")
        return False
    return True


def compare_local_files_with_dynamo(local_files: list[Path], dynamo_table_name: str) -> list[Path]:
    dynamo_client = DynamoClient(dynamo_table_name)
    hashes_in_dynamo = {image.sha256 for image in dynamo_client.listImages()}
    new_files = []
    for local_file in local_files:
        file_hash = calculate_sha256(str(local_file))
        if file_hash not in hashes_in_dynamo:
            new_files.append(local_file)
    duplicates_found = len(local_files) - len(new_files)
    print(f"Found {duplicates_found} duplicates in DynamoDB.")
    return new_files


def upload_files_with_uuid_in_batches(
    directory: Path,
    bucket_name: str,
    # keep lambda_function param only if you still use it for logs, else remove
    lambda_function: str,
    dynamodb_table_name: str,
    cdn_bucket_name: str,      # <--- Make sure you add cdn_bucket_name if needed
    cdn_prefix: str = "cdn/",  # <--- We'll default to 'cdn/'
    path_prefix: str = "raw/",
    batch_size: int = 10,
    env: str = "dev",
) -> None:
    s3 = boto3.client("s3")
    dynamo_client = DynamoClient(dynamodb_table_name)

    all_png_files = sorted(p for p in directory.iterdir() if p.suffix.lower() == ".png")
    files_to_upload = compare_local_files_with_dynamo(all_png_files, dynamodb_table_name)
    print(f"Found {len(files_to_upload)} files to upload.")
    if not files_to_upload:
        print("No new files to upload.")
        return

    # Comment out old lambda client usage:
    # lambda_client = boto3.client("lambda")

    for batch_index, batch in enumerate(chunked(files_to_upload, batch_size), start=1):
        print(f"\nProcessing batch #{batch_index} with up to {batch_size} files...")

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            mapped_uuids = []
            for file_path in batch:
                new_uuid = str(uuid4())
                new_file_path = tmp_dir / f"{new_uuid}.png"
                shutil.copy2(file_path, new_file_path)
                mapped_uuids.append(new_uuid)

            # Run Swift script
            files_in_temp = [str(p) for p in tmp_dir.iterdir() if p.is_file()]
            if not run_swift_script(tmp_dir, files_in_temp):
                raise RuntimeError("Error running Swift script")

            # Upload from temp directory to S3
            for temp_file in tmp_dir.iterdir():
                s3_key = f"{path_prefix}{temp_file.name}"
                print(f"Uploading {temp_file.name:<41} -> s3://{bucket_name}/{s3_key}")
                s3.upload_file(str(temp_file), bucket_name, s3_key)

            # Retrieve UUIDs for this batch
            uuids = {p.stem for p in tmp_dir.iterdir() if p.is_file()}
            uuids_list = list(uuids)

        # **Replace Lambda invocation with HTTP GET request**:
        domain_part = "api" if env == "prod" else f"{env}-api"
        # Adjust the base URL to match your actual API gateway or custom domain
        # For example, let's say the route is: GET https://{domain_part}.YOURDOMAIN.com/process
        base_url = f"https://{domain_part}.tylernorlund.com/process"

        for idx, uuid_str in enumerate(mapped_uuids):
            print(f"Sending GET request to process event for UUID: {uuid_str}")
            params = {
                "table_name": dynamodb_table_name,
                "raw_bucket_name": bucket_name,
                "raw_prefix": path_prefix,
                "uuid": uuid_str,
                "cdn_bucket_name": cdn_bucket_name,
                "cdn_prefix": cdn_prefix,
            }
            try:
                resp = requests.get(base_url, params=params)
                
                print("Status code:", resp.status_code)
                print("Raw response text:", resp.text)
                print("Raw response JSON:", resp.json())

                if resp.status_code == 200:
                    print(f"Successfully triggered processing for UUID: {uuid_str}")
                else:
                    print(
                        f"Failed to trigger processing for UUID {uuid_str}. "
                        f"Status: {resp.status_code}, Body: {resp.text}"
                    )
            except Exception as e:
                print(f"Exception during request for UUID {uuid_str}: {e}")

        print(f"Finished batch #{batch_index}.")


def delete_in_batches(delete_func, items, chunk_size=1000):
    total = len(items)
    if total == 0:
        return

    for start in range(0, total, chunk_size):
        batch = items[start : start + chunk_size]
        delete_func(batch)
        remaining = total - (start + len(batch))
        print(f"   Deleted {len(batch)} items. {remaining} remaining...")


def delete_items_in_table(dynamo_client: DynamoClient) -> None:
    images = dynamo_client.listImages()
    print(f" - Deleting {len(images)} image items")
    dynamo_client.deleteImages(images)

    lines = dynamo_client.listLines()
    print(f" - Deleting {len(lines)} line items")
    dynamo_client.deleteLines(lines)

    words = dynamo_client.listWords()
    print(f" - Deleting {len(words)} word items")
    dynamo_client.deleteWords(words)

    word_tags = dynamo_client.listWordTags()
    print(f" - Deleting {len(word_tags)} word tag items")
    dynamo_client.deleteWordTags(word_tags)

    letters = dynamo_client.listLetters()
    print(f" - Deleting {len(letters)} letter items")
    dynamo_client.deleteLetters(letters)

    receipts = dynamo_client.listReceipts()
    print(f" - Deleting {len(receipts)} receipt items")
    dynamo_client.deleteReceipts(receipts)

    receipt_lines = dynamo_client.listReceiptLines()
    print(f" - Deleting {len(receipt_lines)} receipt line items")
    dynamo_client.deleteReceiptLines(receipt_lines)

    receipt_words = dynamo_client.listReceiptWords()
    print(f" - Deleting {len(receipt_words)} receipt word items")
    dynamo_client.deleteReceiptWords(receipt_words)

    receipt_word_tags = dynamo_client.listReceiptWordTags()
    print(f" - Deleting {len(receipt_word_tags)} receipt word tag items")
    dynamo_client.deleteReceiptWordTags(receipt_word_tags)

    receipt_letters = dynamo_client.listReceiptLetters()
    print(f" - Deleting {len(receipt_letters)} receipt letter items")
    dynamo_client.deleteReceiptLetters(receipt_letters)
    sleep(1)


def export_logs_to_s3(
    log_group_name: str,
    bucket_name: str,
    start_ms: int,
    end_ms: int,
    export_prefix: str = "exportedlogs",
) -> tuple[str, str]:
    logs_client = boto3.client("logs")
    try:
        response = logs_client.create_export_task(
            logGroupName=log_group_name,
            fromTime=start_ms,
            to=end_ms,
            destination=bucket_name,
            destinationPrefix=export_prefix,
        )
        task_id = response["taskId"]
        print(f"Successfully created export task. Task Id: {task_id}")
        return task_id, export_prefix
    except logs_client.exceptions.ClientError as e:
        print(f"Error starting export task: {e}")
        return "", export_prefix


def wait_for_export_completion(logs_client, task_id: str, poll_interval=5):
    while True:
        tasks_info = logs_client.describe_export_tasks(taskId=task_id)
        tasks = tasks_info.get("exportTasks", [])
        if not tasks:
            print(f"No task found for ID {task_id}")
            return
        status = tasks[0]["status"]["code"]
        print(f"Export task {task_id} status: {status}")
        if status in ["CANCELLED", "FAILED"]:
            print(f"Task {task_id} finished with status: {status}")
            return
        if status == "COMPLETED":
            print(f"Task {task_id} completed!")
            return
        time.sleep(poll_interval)


def download_and_cleanup_logs(
    bucket_name: str,
    export_prefix: str,
    download_dir: Path,
    remove_from_s3: bool = True
) -> list[Path]:
    s3 = boto3.client("s3")
    download_dir.mkdir(parents=True, exist_ok=True)
    downloaded_files = []
    continuation_token = None

    print(f"Downloading log files from s3://{bucket_name}/{export_prefix} to {download_dir} ...")

    while True:
        list_kwargs = {
            "Bucket": bucket_name,
            "Prefix": export_prefix,
            "MaxKeys": 1000
        }
        if continuation_token:
            list_kwargs["ContinuationToken"] = continuation_token

        response = s3.list_objects_v2(**list_kwargs)
        contents = response.get("Contents", [])
        if not contents:
            print("No more log files found under prefix.")
            break

        for obj in contents:
            key = obj["Key"]
            print(f"key: {key}")

            rel_path = key[len(export_prefix):].lstrip("/")
            local_path = download_dir / rel_path
            local_path.parent.mkdir(parents=True, exist_ok=True)

            print(f"  - Downloading {key} -> {local_path}")
            s3.download_file(bucket_name, key, str(local_path))
            downloaded_files.append(local_path)

            if remove_from_s3:
                print(f"    Deleting from S3: {key}")
                s3.delete_object(Bucket=bucket_name, Key=key)

        if response.get("IsTruncated"):
            continuation_token = response["NextContinuationToken"]
        else:
            break

    print("Download (and cleanup) complete!")
    return downloaded_files


def parse_exported_logs(gz_files: list[Path]) -> None:
    for gz_file in gz_files:
        txt_file = gz_file.with_suffix(".txt")
        print(f"\n--- Parsing {gz_file.name} => {txt_file.name} ---")
        with gzip.open(gz_file, "rt", encoding="utf-8") as f_in, txt_file.open("w", encoding="utf-8") as f_out:
            for line in f_in:
                f_out.write(line)
        print(f"Finished writing to {txt_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload images to S3")
    parser.add_argument("--directory_to_upload", required=True, help="Directory containing images to upload")
    parser.add_argument("--env", default="dev", help="The environment to use (e.g., 'dev', 'prod')")
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Delete all items from DynamoDB tables before uploading",
    )
    args = parser.parse_args()

    pulumi_output = load_env(args.env)
    raw_bucket = pulumi_output["raw_bucket_name"]
    cdn_bucket = pulumi_output["cdn_bucket_name"]
    lambda_function = pulumi_output["cluster_lambda_function_name"]
    dynamo_db_table = pulumi_output["dynamodb_table_name"]
    cdn_prefix = "assets/"

    if args.debug:
        print("Deleting all items from DynamoDB tables...")
        dynamo_client = DynamoClient(dynamo_db_table)
        delete_items_in_table(dynamo_client)

    # Perform the actual upload in batches, now using an HTTP GET request to the function:
    upload_files_with_uuid_in_batches(
        directory=Path(args.directory_to_upload).resolve(),
        bucket_name=raw_bucket,
        lambda_function=lambda_function,
        dynamodb_table_name=dynamo_db_table,
        cdn_bucket_name=cdn_bucket,
        cdn_prefix=cdn_prefix,
        env=args.env,
    )


if __name__ == "__main__":
    main()