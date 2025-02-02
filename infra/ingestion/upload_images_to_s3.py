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
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.exceptions import ClientError
from dynamo import DynamoClient
import pulumi.automation as auto

import requests


def warm_up_api(base_url: str, params_common: dict, warmup_uuid: str = "warmup"):
    """
    Call the API with a dummy UUID to warm it up.
    """
    try:
        warmup_params = params_common.copy()
        # Use a dummy value that your API can either ignore or handle gracefully.
        warmup_params["uuids"] = warmup_uuid
        response = requests.get(base_url, params=warmup_params)
        print(f"Warm-up request status: {response.status_code}")
    except Exception as e:
        print(f"Warm-up request failed: {e}")

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
    dynamodb_table_name: str,
    cdn_bucket_name: str,
    cdn_prefix: str = "cdn/",
    path_prefix: str = "raw/",
    batch_size: int = 6,
    sub_batch_size: int = 3,
    env: str = "dev",
) -> None:
    s3 = boto3.client("s3")
    dynamo_client = DynamoClient(dynamodb_table_name)
    domain_part = "api" if env == "prod" else f"{env}-api"
    base_url = f"https://{domain_part}.tylernorlund.com/process"
    warm_up_api(base_url, {"table_name": dynamodb_table_name})
    warm_up_api(base_url, {"table_name": dynamodb_table_name})
    warm_up_api(base_url, {"table_name": dynamodb_table_name})

    all_png_files = sorted(p for p in directory.iterdir() if p.suffix.lower() == ".png")
    files_to_upload = compare_local_files_with_dynamo(all_png_files, dynamodb_table_name)
    print(f"Found {len(files_to_upload)} files to upload.")
    if not files_to_upload:
        print("No new files to upload.")
        return

    for batch_index, batch in enumerate(chunked(files_to_upload, batch_size), start=1):
        print(f"\nProcessing batch #{batch_index} with up to {batch_size} files...")

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            # Create a UUID for each file, copy it into temp dir
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

            # --------------------------------------------------------------------------------
            #           GET requests for sub-batches of UUIDs
            # --------------------------------------------------------------------------------
            print("\nSending GET requests for sub-batches of UUIDs in this batch, then waiting for all responses...")
            

            # Subdivide the list of UUIDs into sub-batches
            sub_batches = list(chunked(mapped_uuids, sub_batch_size))
            print(f"Subdivided {len(mapped_uuids)} UUIDs into {len(sub_batches)} sub-batches.")

            # Set up common parameters for the GET requests
            params_common = {
                "table_name": dynamodb_table_name,
                "raw_bucket_name": bucket_name,
                "raw_prefix": path_prefix,
                "cdn_bucket_name": cdn_bucket_name,
                "cdn_prefix": cdn_prefix,
            }

            def call_api_for_sub_batch(sub_batch):
                params = params_common.copy()
                # API expects a comma-separated string of UUIDs
                params["uuids"] = ",".join(sub_batch)
                return requests.get(base_url, params=params)

            responses = []
            # Use a thread pool to launch sub-batch GET requests concurrently.
            with ThreadPoolExecutor(max_workers=len(sub_batches)) as executor:
                futures = []
                for i, sub_batch in enumerate(sub_batches):
                    # Wait 0.1 seconds between starting each sub-batch request
                    if i > 0:
                        time.sleep(0.1)
                    futures.append(executor.submit(call_api_for_sub_batch, sub_batch))
                
                # Wait for all the sub-batch requests to complete
                for future in as_completed(futures):
                    try:
                        response = future.result()
                        responses.append(response)
                        if response.status_code == 200:
                            print(f"Sub-batch GET request succeeded: {response.request.url}")
                        else:
                            print(f"Sub-batch GET request failed: Status {response.status_code} - {response.text}")
                    except Exception as e:
                        print(f"Error during sub-batch API call: {e}")

            print(f"Finished batch #{batch_index}.\n")


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
    dynamo_db_table = pulumi_output["dynamodb_table_name"]
    cdn_prefix = "assets/"

    # Optional: debug flag to delete everything from Dynamo
    if args.debug:
        print("Deleting all items from DynamoDB tables...")
        dynamo_client = DynamoClient(dynamo_db_table)
        delete_items_in_table(dynamo_client)

    # Perform the actual upload in batches, 
    # sending a single GET request per batch with all UUIDs:
    upload_files_with_uuid_in_batches(
        directory=Path(args.directory_to_upload).resolve(),
        bucket_name=raw_bucket,
        dynamodb_table_name=dynamo_db_table,
        cdn_bucket_name=cdn_bucket,
        cdn_prefix=cdn_prefix,
        env=args.env,
    )


if __name__ == "__main__":
    main()