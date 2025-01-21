from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from time import sleep
from typing import Generator, List
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError
from dynamo import DynamoClient
from pulumi.automation import select_stack


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
        A tuple of (raw_bucket_name, lambda_function_name, dynamo_db_table_name).
    """
    # The working directory is in the "development" directory next to this script.
    script_dir = Path(__file__).parent.resolve()
    work_dir = script_dir / "development"

    if not env:
        raise ValueError("The ENV environment variable is not set")

    stack_name = env.lower()
    project_name = "development"  # Adjust if your Pulumi project name differs

    stack = select_stack(
        stack_name=stack_name,
        project_name=project_name,
        work_dir=str(work_dir),
    )
    outputs = stack.outputs()
    raw_bucket = str(outputs["image_bucket_name"].value)
    lambda_function = str(outputs["cluster_lambda_function_name"].value)
    dynamo_db_table = str(outputs["table_name"].value)

    return raw_bucket, lambda_function, dynamo_db_table


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
        subprocess.run(swift_args, check=True)
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


def get_image_indexes(client: DynamoClient, number_images: int) -> list[int]:
    """
    Assign IDs to new images based on the current IDs in the database.

    This will fill in any "gaps" (missing IDs in the sequence) first, then
    continue numbering after the highest existing ID if more are needed.

    Args:
        client: A DynamoClient for the images table.
        number_images: The number of new images to upload.

    Returns:
        A list of image IDs to assign to the images.
    """
    images = client.listImages()
    if not images:
        return list(range(1, number_images + 1))

    existing_ids = sorted([image.id for image in images])
    existing_ids_set = set(existing_ids)

    new_ids = []
    candidate = 1

    while len(new_ids) < number_images:
        if candidate not in existing_ids_set:
            new_ids.append(candidate)
        candidate += 1

    return new_ids


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

    # Determine ID assignment for each image.
    image_indexes = get_image_indexes(dynamo_client, len(files_to_upload))

    # Process all files in chunks (batches).
    lambda_client = boto3.client("lambda")
    for batch_index, batch in enumerate(chunked(files_to_upload, batch_size), start=1):
        print(f"\nProcessing batch #{batch_index} with up to {batch_size} files...")

        # Create a temporary working directory for this batch.
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            # Copy files to temp directory and rename them with UUIDs.
            for file_path in batch:
                new_file_path = tmp_dir / f"{uuid4()}.png"
                shutil.copy2(file_path, new_file_path)

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
        for idx, uuid_str in enumerate(uuids_list):
            image_id = image_indexes[(batch_index - 1) * batch_size + idx]
            print(f"Invoking Lambda function for UUID={uuid_str} and image_id={image_id}...")
            payload = {"uuid": uuid_str, "s3_path": path_prefix, "image_id": image_id}
            lambda_client.invoke(
                FunctionName=lambda_function,
                InvocationType="Event",
                Payload=json.dumps(payload),
            )

        print(f"Finished batch #{batch_index}.")


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

    raw_bucket, lambda_function, dynamo_db_table = load_env(args.env)

    # If `--debug` is set, clear out relevant tables.
    if args.debug:
        print("Deleting all items from DynamoDB tables...")
        dynamo_client = DynamoClient(dynamo_db_table)
        print("Deleting images...")
        dynamo_client.deleteImages(dynamo_client.listImages())
        print("Deleting lines...")
        dynamo_client.deleteLines(dynamo_client.listLines())
        print("Deleting words...")
        dynamo_client.deleteWords(dynamo_client.listWords())
        print("Deleting letters...")
        dynamo_client.deleteLetters(dynamo_client.listLetters())
        print("Deleting receipts...")
        dynamo_client.deleteReceipts(dynamo_client.listReceipts())
        print("Deleting receipt lines...")
        dynamo_client.deleteReceiptLines(dynamo_client.listReceiptLines())
        print("Deleting receipt words...")
        dynamo_client.deleteReceiptWords(dynamo_client.listReceiptWords())
        print("Deleting receipt letters...")
        dynamo_client.deleteReceiptLetters(dynamo_client.listReceiptLetters())
        sleep(1)

    # Perform the actual upload in batches.
    upload_files_with_uuid_in_batches(
        directory=Path(args.directory_to_upload).resolve(),
        bucket_name=raw_bucket,
        lambda_function=lambda_function,
        dynamodb_table_name=dynamo_db_table,
    )


if __name__ == "__main__":
    main()