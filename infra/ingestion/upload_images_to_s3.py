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


def calculate_sha256(file_path: str) -> str:
    """
    Calculate the SHA-256 hash of a file.

    Args:
        file_path: The path to the file to hash.

    Returns:
        The SHA-256 hash of the file.
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def chunked(iterable: List, n: int) -> Generator[List, None, None]:
    """
    Yield successive n-sized chunks from an iterable.

    Args:
        iterable: The list to chunk.
        n: The size of each chunk.

    Yields:
        Sub-list of length n (or fewer for the last chunk).
    """
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]


def load_env(env: str = "dev") -> tuple[str, str, str]:
    """
    Uses Pulumi to get the values of the RAW_IMAGE_BUCKET, LAMBDA_FUNCTION, and DYNAMO_DB_TABLE
    from the specified stack.

    Args:
        env: The name of the Pulumi stack/environment (e.g. 'dev', 'prod').

    Returns:
        A tuple of (cdn_bucket_name, lambda_function_name, dynamodb_table_name).
    """
    stack = auto.select_stack(
        stack_name=f"tnorlund/portfolio/{env}",
        project_name="portfolio",
        program=lambda: None,
    )
    return {key: val.value for key, val in stack.outputs().items()}


def run_swift_script(output_directory: Path, image_paths: list[str]) -> bool:
    """
    Run the Swift script to process the images.

    Args:
        output_directory: Directory where the output JSON files will be saved.
        image_paths: A list of image paths to process.

    Returns:
        True if the script ran successfully, False otherwise.
    """
    swift_script = Path(__file__).parent / "OCRSwift.swift"
    try:
        swift_args = ["swift", str(swift_script), str(output_directory)] + image_paths
        subprocess.run(swift_args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        print(f"Error running swift script: {e}")
        return False
    return True


def compare_local_files_with_dynamo(
    local_files: list[Path], dynamo_table_name: str
) -> list[Path]:
    """
    Compare local files to those already stored in DynamoDB, filtering out duplicates.

    Args:
        local_files: A list of image file paths on disk.
        dynamo_table_name: The DynamoDB table name used to store images.

    Returns:
        A list of new, non-duplicate image paths that should be uploaded.
    """
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
    lambda_function: str,
    dynamodb_table_name: str,
    path_prefix: str = "raw/",
    batch_size: int = 10,
) -> None:
    """
    Upload PNG files in the given directory to the specified S3 bucket, assigning a UUID-based
    object name, in batches of size `batch_size`.

    Args:
        directory: The path to the directory containing the PNG files to upload.
        bucket_name: The name of the S3 bucket.
        lambda_function: The Lambda function name to invoke after uploading each batch.
        dynamodb_table_name: The DynamoDB table name (used to filter out duplicates).
        path_prefix: The S3 "folder" path (prefix) where files should be stored. Defaults to 'raw/'.
        batch_size: The number of files to process per batch. Defaults to 10.
    """
    s3 = boto3.client("s3")
    dynamo_client = DynamoClient(dynamodb_table_name)

    # Gather all *.png files in the target directory.
    all_png_files = sorted(p for p in directory.iterdir() if p.suffix.lower() == ".png")

    # Compare local PNG files against those in DynamoDB to skip duplicates.
    files_to_upload = compare_local_files_with_dynamo(all_png_files, dynamodb_table_name)
    print(f"Found {len(files_to_upload)} files to upload.")
    if not files_to_upload:
        print("No new files to upload.")
        return

    # Process all files in chunks (batches).
    lambda_client = boto3.client("lambda")
    for batch_index, batch in enumerate(chunked(files_to_upload, batch_size), start=1):
        print(f"\nProcessing batch #{batch_index} with up to {batch_size} files...")

        # Create a temporary working directory for this batch.
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            mapped_uuids = []   # hold (uuid_str, file_path) in the order created
            for file_path in batch:
                new_uuid = str(uuid4())
                new_file_path = tmp_dir / f"{new_uuid}.png"
                shutil.copy2(file_path, new_file_path)
                mapped_uuids.append(new_uuid)

            # Run Swift script over the new set of files.
            files_in_temp = [str(p) for p in tmp_dir.iterdir() if p.is_file()]
            if not run_swift_script(tmp_dir, files_in_temp):
                raise RuntimeError("Error running Swift script")

            # Upload all files from the temp directory to S3.
            for temp_file in tmp_dir.iterdir():
                s3_key = f"{path_prefix}{temp_file.name}"
                print(f"Uploading {temp_file.name:<41} -> s3://{bucket_name}/{s3_key}")
                s3.upload_file(str(temp_file), bucket_name, s3_key)

            # Retrieve the UUIDs we just created (strip .png/.json for the base name).
            # We assume each file has a unique UUID-based name like `<UUID>.png` and `<UUID>.json`.
            uuids = {p.stem for p in tmp_dir.iterdir() if p.is_file()}
            uuids_list = list(uuids)

        # Invoke Lambda function for each new UUID.
        # Now use the *same* order to assign image_ids
        for idx, uuid_str in enumerate(mapped_uuids):
            print(f"Invoking Lambda for UUID: {uuid_str}")
            payload = {"uuid": uuid_str, "s3_path": path_prefix}
            lambda_client.invoke(
                FunctionName=lambda_function,
                InvocationType="Event",
                Payload=json.dumps(payload),
            )

        print(f"Finished batch #{batch_index}.")


def delete_in_batches(delete_func, items, chunk_size=1000):
    """
    Deletes items in batches of 'chunk_size' using the given 'delete_func',
    printing the remaining number of items after each batch.
    """
    total = len(items)
    if total == 0:
        return

    for start in range(0, total, chunk_size):
        batch = items[start : start + chunk_size]
        delete_func(batch)  # e.g. dynamo_client.deleteImages(batch)
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
    """
    Initiate a CloudWatch Logs export task for the given log group, time range,
    and S3 bucket. Returns (taskId, exportPrefix) so you can poll for completion 
    and know where to download from.
    """
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

            # Remove the export_prefix from the start of the key so we can recreate folder structure:
            rel_path = key[len(export_prefix):].lstrip("/")  # e.g. "3917a2f5-e69c-4835-8913.../000000.gz"
            local_path = download_dir / rel_path
            local_path.parent.mkdir(parents=True, exist_ok=True)  # ensure subfolders exist

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
    """
    Unzip each .gz file from CloudWatch logs and write the *raw* text lines 
    to a .txt file (same basename), ignoring any JSON parsing.
    """
    for gz_file in gz_files:
        txt_file = gz_file.with_suffix(".txt")  # same name, but .txt

        print(f"\n--- Parsing {gz_file.name} => {txt_file.name} ---")
        with gzip.open(gz_file, "rt", encoding="utf-8") as f_in, txt_file.open("w", encoding="utf-8") as f_out:
            for line in f_in:
                # Just write the raw log line as-is
                f_out.write(line)

        print(f"Finished writing to {txt_file}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Upload images to S3")
    parser.add_argument(
        "--directory_to_upload",
        required=True,
        help="Directory containing images to upload",
    )
    parser.add_argument(
        "--env",
        default="dev",
        help="The environment to use (e.g., 'dev', 'prod')",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Delete all items from DynamoDB tables before uploading",
    )
    args = parser.parse_args()

    pulumi_output = load_env(args.env)
    raw_bucket = pulumi_output["raw_bucket_name"]
    lambda_function = pulumi_output["cluster_lambda_function_name"]
    dynamo_db_table = pulumi_output["dynamodb_table_name"]

    print(f"dynamo_db_table: {dynamo_db_table}")

    # If `--debug` is set, clear out relevant tables.
    if args.debug:
        print("Deleting all items from DynamoDB tables...")
        dynamo_client = DynamoClient(dynamo_db_table)
        delete_items_in_table(dynamo_client)
        delete_items_in_table(dynamo_client)
    start_time = int(time.time() * 1000)

    # Perform the actual upload in batches.
    upload_files_with_uuid_in_batches(
        directory=Path(args.directory_to_upload).resolve(),
        bucket_name=raw_bucket,
        lambda_function=lambda_function,
        dynamodb_table_name=dynamo_db_table,
    )
    sleep(5)
    end_time = int(time.time() * 1000)
    log_group_name = f"/aws/lambda/{lambda_function}"


    # (B) Export logs
    task_id, export_prefix = export_logs_to_s3(
        log_group_name=log_group_name,
        bucket_name=raw_bucket,
        start_ms=start_time,
        end_ms=end_time,
        export_prefix="exportedlogs",
    )

    # (C) Wait for export to complete
    if task_id:
        logs_client = boto3.client("logs")
        wait_for_export_completion(logs_client, task_id, poll_interval=5)

        local_logs_dir = Path(f"lambda_logs_{args.env}")
        gz_files = download_and_cleanup_logs(
            bucket_name=raw_bucket,
            export_prefix="exportedlogs",
            download_dir=local_logs_dir,
            remove_from_s3=False,
        )

        # New: uncompress & parse
        parse_exported_logs([file for file in gz_files if file.suffix == ".gz"])
    else:
        print("No export task created, skipping download.")


if __name__ == "__main__":
    main()