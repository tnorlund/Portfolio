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
from concurrent.futures import ThreadPoolExecutor, as_completed

import pulumi.automation as auto
from receipt_dynamo import DynamoClient, process, validate


def calculate_sha256(file_path: str) -> str:
    """Calculates the SHA-256 hash for a file.

    Args:
        file_path (str): Local filesystem path to the file.

    Returns:
        str: Hex digest string of the file's SHA-256 hash.
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def chunked(iterable: List, n: int) -> Generator[List, None, None]:
    """Yields successive n-sized chunks from a list (or other sequence).

    Args:
        iterable (List): The list (or sequence) to chunk.
        n (int): Size of each chunk.

    Yields:
        Generator[List, None, None]: A generator that produces chunks of size n.
    """
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]


def load_env(env: str = "dev") -> dict:
    """Retrieves Pulumi stack outputs for the specified environment.

    Args:
        env (str, optional): Pulumi environment (stack) name, e.g. "dev" or "prod".
            Defaults to "dev".

    Returns:
        dict: A dictionary of key-value pairs from the Pulumi stack outputs.
    """
    stack = auto.select_stack(
        stack_name=f"tnorlund/portfolio/{env}",
        project_name="portfolio",
        program=lambda: None,
    )
    return {key: val.value for key, val in stack.outputs().items()}


def run_swift_script(output_directory: Path, image_paths: list[str]) -> bool:
    """Executes a Swift OCR script on the provided image paths.

    The Swift script (OCRSwift.swift) is expected to produce <UUID>.json files
    in the `output_directory`.

    Args:
        output_directory (Path): Directory where the OCR JSON output will be stored.
        image_paths (list[str]): A list of local image file paths to process.

    Returns:
        bool: True if the script succeeded, False if it failed.
    """
    swift_script = Path(__file__).parent / "OCRSwift.swift"
    try:
        swift_args = ["swift", str(swift_script), str(output_directory)] + image_paths
        subprocess.run(
            swift_args,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error running Swift script: {e}")
        return False
    return True


def compare_local_files_with_dynamo(
    local_files: list[Path], dynamo_table_name: str
) -> list[Path]:
    """Compares local image files against DynamoDB records by SHA-256 hash.

    Each local file's SHA-256 is calculated and checked against the set of SHA-256
    hashes found in DynamoDB (Images table). Files not present in DynamoDB are returned.

    Args:
        local_files (list[Path]): A list of PNG file paths to compare.
        dynamo_table_name (str): Name of the DynamoDB table storing image metadata.

    Returns:
        list[Path]: Files that do not exist in DynamoDB (by hash).
    """
    dynamo_client = DynamoClient(dynamo_table_name)
    images, _ = dynamo_client.listImages()
    hashes_in_dynamo = {image.sha256 for image in images}
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
    cdn_prefix: str = "assets/",
    path_prefix: str = "raw/",
    batch_size: int = 10,
    sub_batch_size: int = 5,
) -> None:
    """Orchestrates uploading PNG files by running a Swift OCR script and calling `process()`.

    The ingestion occurs in the following steps:
      1. Gather all PNG files from `directory`.
      2. Compare them against DynamoDB to skip duplicates.
      3. Chunk them into top-level batches of size `batch_size`.
      4. For each batch:
         a) Copy files into a temporary directory and rename them to <UUID>.png.
         b) Run the Swift script to produce <UUID>.json OCR results.
         c) Use a ThreadPoolExecutor with `sub_batch_size` workers to:
            - Read each PNG and JSON in memory.
            - Call `process()` to upload the data to S3 and insert records in DynamoDB.
         d) After all files are processed, validate the batch using parallel workers.

    Args:
        directory (Path): Path to the local directory containing PNG files.
        bucket_name (str): S3 bucket name for raw images.
        dynamodb_table_name (str): Name of DynamoDB table for storing metadata.
        cdn_bucket_name (str): S3 bucket name for serving processed images as a CDN.
        cdn_prefix (str, optional): Path prefix in the CDN bucket. Defaults to "assets/".
        path_prefix (str, optional): Path prefix in the raw bucket. Defaults to "raw/".
        batch_size (int, optional): Number of files per top-level batch. Defaults to 6.
        sub_batch_size (int, optional): Number of concurrent threads for each batch. Defaults to 3.

    Returns:
        None
    """
    all_png_files = sorted(p for p in directory.iterdir() if p.suffix.lower() == ".png")
    files_to_upload = compare_local_files_with_dynamo(
        all_png_files, dynamodb_table_name
    )
    print(f"Found {len(files_to_upload)} files to upload.")
    if not files_to_upload:
        print("No new files to upload.")
        return

    for batch_index, batch in enumerate(chunked(files_to_upload, batch_size), start=1):
        print(f"\nProcessing batch #{batch_index} with up to {batch_size} files...")

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            # 1) Copy files to temporary folder & assign each a UUID filename
            mapped_uuids = []
            for file_path in batch:
                new_uuid = str(uuid4())
                new_file_path = tmp_dir / f"{new_uuid}.png"
                shutil.copy2(file_path, new_file_path)
                mapped_uuids.append(new_uuid)

            # 2) Run Swift OCR on all files in the temporary directory
            files_in_temp = [str(p) for p in tmp_dir.iterdir() if p.is_file()]
            if not run_swift_script(tmp_dir, files_in_temp):
                raise RuntimeError("Error running Swift OCR script.")

            # 3) Process each file in parallel (sub_batch_size concurrency)
            failed_uuids: list[str] = []

            def process_single_uuid(new_uuid: str) -> None:
                """
                Reads PNG & JSON files, then calls process() with in-memory data.
                If process() fails, the UUID is appended to failed_uuids.
                """
                try:
                    png_path = tmp_dir / f"{new_uuid}.png"
                    json_path = tmp_dir / f"{new_uuid}.json"

                    if not png_path.exists():
                        raise FileNotFoundError(f"Swift OCR did not produce {png_path}")
                    with open(png_path, "rb") as pf:
                        png_data = pf.read()

                    if not json_path.exists():
                        raise FileNotFoundError(
                            f"Swift OCR did not produce {json_path}"
                        )
                    with open(json_path, "r", encoding="utf-8") as jf:
                        ocr_dict = json.load(jf)

                    print(f"Processing UUID: {new_uuid}")
                    process(
                        table_name=dynamodb_table_name,
                        raw_bucket_name=bucket_name,
                        raw_prefix=path_prefix,
                        uuid=new_uuid,
                        cdn_bucket_name=cdn_bucket_name,
                        cdn_prefix=cdn_prefix,
                        ocr_dict=ocr_dict,
                        png_data=png_data,
                    )
                except Exception as e:
                    print(f"Error processing {new_uuid}: {e}")
                    failed_uuids.append(new_uuid)

            # Process files concurrently without artificial delays
            with ThreadPoolExecutor(max_workers=sub_batch_size) as executor:
                futures = [
                    executor.submit(process_single_uuid, uuid) for uuid in mapped_uuids
                ]
                for future in as_completed(futures):
                    future.result()

            # If any process() call failed, retry those UUIDs.
            if failed_uuids:
                print("Retrying failed UUIDs:", failed_uuids)
                retry_list = failed_uuids.copy()
                failed_uuids.clear()
                for uuid in retry_list:
                    process_single_uuid(uuid)
                if failed_uuids:
                    print("The following UUIDs still failed after retry:", failed_uuids)

            # Remove failed UUIDs from the mapped_uuids list before validation
            successful_uuids = [
                uuid for uuid in mapped_uuids if uuid not in failed_uuids
            ]
            if successful_uuids:
                print(
                    f"\nValidating {len(successful_uuids)} successfully processed images..."
                )
                validation_failed_uuids: list[str] = []

                def validate_single_uuid(uuid: str) -> None:
                    """Validates a single image's OCR results using GPT."""
                    try:
                        print(f"Validating UUID: {uuid}")
                        validate(table_name=dynamodb_table_name, image_id=uuid)
                    except Exception as e:
                        print(f"Error validating {uuid}: {e}")
                        validation_failed_uuids.append(uuid)

                # Validate files concurrently without artificial delays
                with ThreadPoolExecutor(max_workers=sub_batch_size) as executor:
                    futures = [
                        executor.submit(validate_single_uuid, uuid)
                        for uuid in successful_uuids
                    ]
                    for future in as_completed(futures):
                        future.result()

                # If any validations failed, retry them
                if validation_failed_uuids:
                    print("Retrying failed validations:", validation_failed_uuids)
                    retry_list = validation_failed_uuids.copy()
                    validation_failed_uuids.clear()
                    for uuid in retry_list:
                        validate_single_uuid(uuid)
                    if validation_failed_uuids:
                        print(
                            "The following validations still failed after retry:",
                            validation_failed_uuids,
                        )

            print(f"Finished batch #{batch_index}.\n")


def delete_items_in_table(dynamo_client: DynamoClient) -> None:
    """Deletes all items from the DynamoDB table associated with the given client.

    Clears images, lines, words, letters, receipts, etc., then waits briefly for
    eventual consistency.

    Args:
        dynamo_client (DynamoClient): The DynamoClient instance pointing to the correct table.
    """
    images, _ = dynamo_client.listImages()
    print(f" - Deleting {len(images)} image items")
    dynamo_client.deleteImages(images)

    lines = dynamo_client.listLines()
    print(f" - Deleting {len(lines)} line items")
    dynamo_client.deleteLines(lines)

    words = dynamo_client.listWords()
    print(f" - Deleting {len(words)} word items")
    dynamo_client.deleteWords(words)

    word_tags, _ = dynamo_client.listWordTags()
    print(f" - Deleting {len(word_tags)} word tag items")
    dynamo_client.deleteWordTags(word_tags)

    letters = dynamo_client.listLetters()
    print(f" - Deleting {len(letters)} letter items")
    dynamo_client.deleteLetters(letters)

    receipts, _ = dynamo_client.listReceipts()
    print(f" - Deleting {len(receipts)} receipt items")
    dynamo_client.deleteReceipts(receipts)

    receipt_lines = dynamo_client.listReceiptLines()
    print(f" - Deleting {len(receipt_lines)} receipt line items")
    dynamo_client.deleteReceiptLines(receipt_lines)

    receipt_words = dynamo_client.listReceiptWords()
    print(f" - Deleting {len(receipt_words)} receipt word items")
    dynamo_client.deleteReceiptWords(receipt_words)

    receipt_word_tags, _ = dynamo_client.listReceiptWordTags()
    print(f" - Deleting {len(receipt_word_tags)} receipt word tag items")
    dynamo_client.deleteReceiptWordTags(receipt_word_tags)

    receipt_letters = dynamo_client.listReceiptLetters()
    print(f" - Deleting {len(receipt_letters)} receipt letter items")
    dynamo_client.deleteReceiptLetters(receipt_letters)

    # Pause briefly for eventual consistency
    sleep(1)


def main() -> None:
    """Parses command-line arguments, optionally clears DynamoDB, then uploads PNGs in batches.

    Command-line Arguments:
        --directory_to_upload (str): Directory containing PNGs to ingest.
        --env (str, optional): Pulumi environment to select (default: "dev").
        --debug (bool, optional): If set, delete all items from DynamoDB prior to ingestion.
    """
    parser = argparse.ArgumentParser(description="Upload images to S3.")
    parser.add_argument(
        "--directory_to_upload",
        required=True,
        help="Directory containing images (PNGs) to upload.",
    )
    parser.add_argument(
        "--env", default="dev", help="Pulumi environment name (e.g. 'dev' or 'prod')."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="If set, delete all items from DynamoDB tables before uploading.",
    )
    args = parser.parse_args()

    # Load Pulumi outputs for the given environment
    pulumi_output = load_env(args.env)
    raw_bucket = pulumi_output["raw_bucket_name"]
    cdn_bucket = pulumi_output["cdn_bucket_name"]
    dynamo_db_table = pulumi_output["dynamodb_table_name"]
    cdn_prefix = "assets/"

    # (Optional) Clear the entire table if --debug is set
    if args.debug:
        print("Deleting all items from DynamoDB tables...")
        dynamo_client = DynamoClient(dynamo_db_table)
        delete_items_in_table(dynamo_client)

    # Ingest new PNG files in batches
    upload_files_with_uuid_in_batches(
        directory=Path(args.directory_to_upload).resolve(),
        bucket_name=raw_bucket,
        dynamodb_table_name=dynamo_db_table,
        cdn_bucket_name=cdn_bucket,
        cdn_prefix=cdn_prefix,
    )


if __name__ == "__main__":
    main()
