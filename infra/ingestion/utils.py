import json
import difflib
import binascii
import os
import boto3
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple
import pulumi.automation as auto
from dynamo import (
    DynamoClient,
    Image,
    Line,
    Word,
    WordTag,
    Letter,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    ReceiptLetter,
)


FAILURE_DIR = Path("test_failures")
FAILURE_DIR.mkdir(parents=True, exist_ok=True)


def load_env():
    stack = auto.select_stack(
        stack_name="tnorlund/portfolio/e2e",
        project_name="portfolio",
        program=lambda: None,
    )

    # Convert Pulumi OutputValue objects to raw Python values
    return {key: val.value for key, val in stack.outputs().items()}


def bucket_exists(bucket_name: str) -> bool:
    s3 = boto3.client("s3")
    try:
        s3.head_bucket(Bucket=bucket_name)
        return True
    except boto3.exceptions.botocore.client.ClientError:
        return False


def table_exists(table_name: str) -> bool:
    dynamodb = boto3.client("dynamodb")
    try:
        dynamodb.describe_table(TableName=table_name)
        return True
    except boto3.exceptions.botocore.client.ClientError:
        return False


def get_raw_keys(bucket_name: str) -> List[Tuple[str, str, str]]:
    """
    Lists the '.png', '.json', and '_results.json' keys for each 'image',
    grouping them by their shared UUID.

    For each UUID, there should be three keys:
      1) The .png image
      2) The .json file (OCR results)
      3) The _results.json file (final clustering results)

    Returns:
        List[Tuple[str, str, str]]: A list of (png_key, ocr_json_key, results_json_key)
                                    for each image.
    """
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    response_iterator = paginator.paginate(Bucket=bucket_name)
    grouped_files = {}

    for page in response_iterator:
        # 'Contents' might not be present if the bucket is empty, so use get(..., [])
        for obj in page.get("Contents", []):
            key = obj["Key"]

            # We need special handling to distinguish between `.json` and `_results.json`.
            if key.lower().endswith("_results.json"):
                # If the key is something like '123abc_results.json',
                # we take everything up to '_results.json' as the base.
                base = key[: -len("_results.json")]
                extension = "_results.json"
            else:
                # Otherwise, just split by extension as usual.
                base, ext = os.path.splitext(key)
                extension = ext.lower()

            # Only care about .png, .json, or _results.json
            if extension in (".png", ".json", "_results.json"):
                if base not in grouped_files:
                    grouped_files[base] = {}
                grouped_files[base][extension] = key

    # Build the final list of triples (png, ocr_json, results_json)
    files = []
    for base, exts in grouped_files.items():
        if all(ext in exts for ext in [".png", ".json", "_results.json"]):
            files.append((exts[".png"], exts[".json"], exts["_results.json"]))

    return files


def get_cdn_keys(bucket_name: str) -> List[Tuple[str, List[str]]]:
    """
    Lists the '.png' and '_cluster_<cluster_num>.png' keys for each 'image',
    grouping them by their shared UUID.

    For each UUID, there should be at least 2 keys:
      1) The .png image
      2) The cluster .png file(s)

    Returns:
        List[Tuple[str, list[str]]]: A list of (main_png_key, [cluster_png_keys])
                                     for each image.
    """
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    page_iterator = paginator.paginate(Bucket=bucket_name)

    # We'll keep track of each "UUID" in this dict:
    #   images[uuid] = {'main': None, 'clusters': []}
    images = {}

    for page in page_iterator:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            # We only care about .png
            if not key.lower().endswith(".png"):
                continue

            # Take the filename portion only, to ignore directory prefixes:
            # e.g. "assets/15e4c27d_cluster_00001.png" -> "15e4c27d_cluster_00001.png"
            filename = os.path.basename(key)

            # Split off the .png extension
            base, _ = os.path.splitext(filename)

            # If it's a cluster image, it might match something like:
            #   <uuid>_cluster_00001.png
            # We'll extract the UUID portion before '_cluster_...'
            if "_cluster_" in base:
                uuid = base.split("_cluster_")[0]
            else:
                uuid = base  # It's the main .png file

            # Make sure this UUID is tracked
            if uuid not in images:
                images[uuid] = {"main": None, "clusters": []}

            # Assign the S3 key to either the main image or a cluster image
            if "_cluster_" in base:
                images[uuid]["clusters"].append(key)
            else:
                images[uuid]["main"] = key

    # Build the final list of (main_png, [cluster_pngs]) tuples
    results = []
    for uuid, info in images.items():
        # Only return if we actually have the main file AND at least one cluster
        if info["main"] and info["clusters"]:
            results.append((info["main"], info["clusters"]))

    return results


def backup_raw_s3(
    bucket_name: str, backup_dir: str = "/tmp/raw_backup"
) -> List[Tuple[str, str]]:
    """
    Uses get_raw_keys(bucket_name) to find the .png, .json, and _results.json
    files for each image in the RAW S3 bucket, then downloads them locally.

    Args:
        bucket_name (str): Name of the S3 bucket (RAW bucket).
        backup_dir (str): Local directory where backups are saved.

    Returns:
        A list of (s3_key, local_path) tuples indicating where each file was stored locally.
    """
    s3 = boto3.client("s3")
    os.makedirs(backup_dir, exist_ok=True)

    # get_raw_keys() returns a list of (png_key, ocr_json_key, results_json_key)
    raw_key_groups = get_raw_keys(bucket_name)
    backup_list: List[Tuple[str, str]] = []

    for png_key, ocr_json_key, results_json_key in raw_key_groups:
        # Each group has three files: .png, .json, and _results.json
        for s3_key in (png_key, ocr_json_key, results_json_key):
            local_filename = s3_key.replace(
                "/", "_"
            )  # Flatten any subfolders into filename
            local_path = str(Path(backup_dir) / local_filename)

            # Download file to local backup directory
            s3.download_file(bucket_name, s3_key, local_path)

            backup_list.append((s3_key, local_path))

    return backup_list


def backup_cdn_s3(
    bucket_name: str, backup_dir: str = "/tmp/cdn_backup"
) -> List[Tuple[str, str]]:
    """
    Uses get_cdn_keys(bucket_name) to find the .png and _cluster_<cluster_num>.png
    files for each image in the CDN S3 bucket, then downloads them locally.

    Args:
        bucket_name (str): Name of the CDN S3 bucket.
        backup_dir (str): Local directory where backups are saved.

    Returns:
        A list of (s3_key, local_path) tuples indicating where each file was stored locally.
    """
    s3 = boto3.client("s3")
    os.makedirs(backup_dir, exist_ok=True)

    # get_cdn_keys() returns a list of (main_png_key, [cluster_png_keys])
    cdn_key_groups = get_cdn_keys(bucket_name)
    backup_list: List[Tuple[str, str]] = []

    for main_key, cluster_keys in cdn_key_groups:
        # We'll handle the main key plus any cluster keys
        keys_to_backup = [main_key] + cluster_keys

        for s3_key in keys_to_backup:
            local_filename = s3_key.replace(
                "/", "_"
            )  # Flatten any subfolders into filename
            local_path = str(Path(backup_dir) / local_filename)

            # Download file to local backup directory
            s3.download_file(bucket_name, s3_key, local_path)

            backup_list.append((s3_key, local_path))

    return backup_list


def backup_dynamo_items(
    dynamo_name: str, backup_dir: str = "/tmp/dynamo_backup"
) -> str:
    """
    Uses DynamoClient to list all items in the DynamoDB table, then saves them locally as JSON.

    Args:
        dynamo_name (str): Name of the DynamoDB table.
        backup_dir (str, optional): Local directory where backups are saved. Defaults to "/tmp/dynamo_backup".

    Returns:
        The path to the JSON file containing all the DynamoDB items.
    """
    dynamo_client = DynamoClient(dynamo_name)
    os.makedirs(backup_dir, exist_ok=True)

    # Get all items from the DynamoDB table
    images = dynamo_client.listImages()
    lines = dynamo_client.listLines()
    words = dynamo_client.listWords()
    word_tags = dynamo_client.listWordTags()
    letters = dynamo_client.listLetters()
    receipts = dynamo_client.listReceipts()
    receipt_lines = dynamo_client.listReceiptLines()
    receipt_words = dynamo_client.listReceiptWords()
    receipt_word_tags = dynamo_client.listReceiptWordTags()
    receipt_letters = dynamo_client.listReceiptLetters()

    # Save all items to a single JSON file
    backup_path = Path(backup_dir) / "dynamo_backup.json"
    with open(backup_path, "w") as f:
        f.write(
            json.dumps(
                {
                    "images": [dict(image) for image in images],
                    "lines": [
                        {
                            key: value
                            for key, value in dict(line).items()
                            if key not in ("histogram", "num_chars")
                        }
                        for line in lines
                    ],
                    "words": [
                        {
                            key: value
                            for key, value in dict(word).items()
                            if key not in ("histogram", "num_chars")
                        }
                        for word in words
                    ],
                    "word_tags": [dict(tag) for tag in word_tags],
                    "letters": [dict(letter) for letter in letters],
                    "receipts": [dict(receipt) for receipt in receipts],
                    "receipt_lines": [
                        {
                            key: value
                            for key, value in dict(line).items()
                            if key not in ("histogram", "num_chars")
                        }
                        for line in receipt_lines
                    ],
                    "receipt_words": [
                        {
                            key: value
                            for key, value in dict(word).items()
                            if key not in ("histogram", "num_chars")
                        }
                        for word in receipt_words
                    ],
                    "receipt_word_tags": [dict(tag) for tag in receipt_word_tags],
                    "receipt_letters": [dict(letter) for letter in receipt_letters],
                },
                indent=2,
            )
        )

    return str(backup_path)


def group_by_uuid(backups: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    """
    Group a list of (s3_key, local_path) tuples by a UUID extracted from the s3_key.

    Args:
        backups: A list of (s3_key, local_path).

    Returns:
        A dictionary mapping uuid -> list of local file paths.
    """
    grouped = defaultdict(list)
    for s3_key, local_path in backups:
        uuid = extract_uuid_from_key(s3_key)
        grouped[uuid].append(local_path)
    return dict(grouped)


def parse_dynamo_json(backup_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Parse the Dynamo JSON backup file, grouping items by their UUID (derived from each Image).

    Args:
        backup_path: Path to the DynamoDB backup JSON file.

    Returns:
        A dictionary mapping uuid -> dictionary of image/lines/words/etc.

    Raises:
        FileNotFoundError: If the given backup_path does not exist.
    """
    if not os.path.isfile(backup_path):
        raise FileNotFoundError(f"Dynamo backup file not found: {backup_path}")

    with open(backup_path, "r") as f:
        data = json.load(f)

    images = [Image(**img) for img in data["images"]]
    lines = [Line(**ln) for ln in data["lines"]]
    words = [Word(**wd) for wd in data["words"]]
    word_tags = [WordTag(**tg) for tg in data["word_tags"]]
    letters = [Letter(**lt) for lt in data["letters"]]
    receipts = [Receipt(**rc) for rc in data["receipts"]]
    receipt_lines = [ReceiptLine(**ln) for ln in data["receipt_lines"]]
    receipt_words = [ReceiptWord(**wd) for wd in data["receipt_words"]]
    receipt_word_tags = [ReceiptWordTag(**tg) for tg in data["receipt_word_tags"]]
    receipt_letters = [ReceiptLetter(**lt) for lt in data["receipt_letters"]]

    dynamo_grouped: Dict[str, Dict[str, Any]] = {}
    for image in images:
        uuid = extract_uuid_from_image(image)
        if uuid not in dynamo_grouped:
            dynamo_grouped[uuid] = {}

        # Basic image-level data
        dynamo_grouped[uuid]["image"] = image
        dynamo_grouped[uuid]["lines"] = [ln for ln in lines if ln.image_id == image.id]
        dynamo_grouped[uuid]["words"] = [wd for wd in words if wd.image_id == image.id]
        dynamo_grouped[uuid]["word_tags"] = [
            tg for tg in word_tags if tg.image_id == image.id
        ]
        dynamo_grouped[uuid]["letters"] = [
            lt for lt in letters if lt.image_id == image.id
        ]

        # Receipt-level data grouped by receipt ID
        relevant_receipts = [rc for rc in receipts if rc.image_id == image.id]
        dynamo_grouped[uuid]["receipts"] = {
            receipt.id: {
                "lines": [ln for ln in receipt_lines if ln.receipt_id == receipt.id],
                "words": [wd for wd in receipt_words if wd.receipt_id == receipt.id],
                "word_tags": [
                    tg for tg in receipt_word_tags if tg.receipt_id == receipt.id
                ],
                "letters": [
                    lt for lt in receipt_letters if lt.receipt_id == receipt.id
                ],
            }
            for receipt in relevant_receipts
        }

    return dynamo_grouped


def group_images(
    raw_backup: List[Tuple[str, str]],
    cdn_backup: List[Tuple[str, str]],
    dynamo_backup_path: str,
) -> Dict[str, Dict[str, Any]]:
    """
    Combine the backed-up items from RAW S3, CDN S3, and Dynamo into a single
    dictionary keyed by UUID (or whichever unique ID your system uses).

    Args:
        raw_backup: A list of (s3_key, local_path) tuples for RAW backups.
        cdn_backup: A list of (s3_key, local_path) tuples for CDN backups.
        dynamo_backup_path: Local path to the DynamoDB JSON backup file.

    Returns:
        A dictionary keyed by UUID. Each value is another dictionary containing:
            {
               "raw": [...list of local RAW paths...],
               "cdn": [...list of local CDN paths...],
               "dynamo": {
                   "image": <Image>,
                   "lines": [...],
                   "words": [...],
                   "word_tags": [...],
                   "letters": [...],
                   "receipts": {
                       <receipt.id>: {
                           "lines": [...],
                           "words": [...],
                           "word_tags": [...],
                           "letters": [...]
                       },
                       ...
                   },
               },
            }

    Raises:
        ValueError: If multiple or zero matching images are found for a given UUID.
    """
    # 1. Group RAW and CDN backups by UUID
    raw_grouped = group_by_uuid(raw_backup)
    cdn_grouped = group_by_uuid(cdn_backup)

    # 2. Parse and group Dynamo items by UUID
    dynamo_grouped = parse_dynamo_json(dynamo_backup_path)

    # 3. Combine into a single dictionary
    all_uuids = (
        set(raw_grouped.keys()) | set(cdn_grouped.keys()) | set(dynamo_grouped.keys())
    )

    # We'll need the full list of images to verify unique matches:
    all_images = [grp["image"] for grp in dynamo_grouped.values() if "image" in grp]

    combined: Dict[str, Dict[str, Any]] = {}
    for uuid in all_uuids:
        # Check for exactly one matching image in the entire "images" list
        images_with_uuid = [img for img in all_images if uuid in img.raw_s3_key]
        if len(images_with_uuid) > 1:
            raise ValueError(f"Multiple images found for UUID: {uuid}")
        if not images_with_uuid:
            raise ValueError(
                f"No image found for UUID: {uuid}"
                f"\n - All UUIDs: {all_uuids}"
                f"\n - All images: {[image.uuid for image in all_images]}"
            )

        combined[uuid] = {
            "raw": raw_grouped.get(uuid, []),
            "cdn": cdn_grouped.get(uuid, []),
            "dynamo": dynamo_grouped.get(uuid, {}),
        }

    return combined


def extract_uuid_from_key(s3_key: str) -> str:
    """
    Extract the UUID portion from the S3 key (file path).
    For example:
      "assets/218e0b40-7231-42fb-83c5-fc5d44970198_results.json"
    should return "218e0b40-7231-42fb-83c5-fc5d44970198"
    """
    import os

    filename = os.path.basename(
        s3_key
    )  # => "218e0b40-7231-42fb-83c5-fc5d44970198_results.json"
    base, _ = os.path.splitext(
        filename
    )  # => base="218e0b40-7231-42fb-83c5-fc5d44970198_results"

    # If it ends with '_results', strip it
    if base.endswith("_results"):
        base = base[: -len("_results")]  # => "218e0b40-7231-42fb-83c5-fc5d44970198"

    # Handle '_cluster_' as before
    if "_cluster_" in base:
        return base.split("_cluster_")[0]
    else:
        return base


def extract_uuid_from_image(image: Image) -> str:
    """
    Extract the UUID from an Image object. Adjust to match how your 'raw_s3_key' stores UUID info.
    Here we assume 'raw_s3_key' might look like "15e4c27d.png" or "path/15e4c27d.json", etc.

    Args:
        image: An Image object from Dynamo.

    Returns:
        The UUID portion, e.g. "15e4c27d" (if raw_s3_key="15e4c27d.png").
    """
    return os.path.splitext(os.path.basename(image.raw_s3_key))[0]


def delete_raw_s3(bucket_name: str) -> None:
    """
    Uses get_raw_keys(bucket_name) to find the .png, .json, and _results.json
    files for each image in the RAW S3 bucket, then deletes them.

    Args:
        bucket_name (str): Name of the RAW S3 bucket.
    """
    s3 = boto3.client("s3")
    raw_key_groups = get_raw_keys(bucket_name)
    for png_key, ocr_json_key, results_json_key in raw_key_groups:
        for s3_key in (png_key, ocr_json_key, results_json_key):
            print(f" - Deleting {s3_key}")
            s3.delete_object(Bucket=bucket_name, Key=s3_key)


def delete_cdn_s3(bucket_name: str) -> None:
    """
    Uses get_cdn_keys(bucket_name) to find the main .png file and any
    '_cluster_<cluster_num>.png' files for each image in the CDN S3 bucket,
    then deletes them.

    Args:
        bucket_name (str): Name of the CDN S3 bucket.
    """
    s3 = boto3.client("s3")
    cdn_key_groups = get_cdn_keys(bucket_name)
    for main_png_key, cluster_keys in cdn_key_groups:
        for s3_key in [main_png_key] + cluster_keys:
            print(f" - Deleting {s3_key}")
            s3.delete_object(Bucket=bucket_name, Key=s3_key)


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


def delete_dynamo_items(dynamo_name: str) -> None:
    """
    Uses DynamoClient to list all items in the DynamoDB table, then deletes them.

    Args:
        dynamo_name (str): Name of the DynamoDB table.
    """
    dynamo_client = DynamoClient(dynamo_name)
    images = dynamo_client.listImages()
    print(f" - Deleting {len(images)} image items")
    delete_in_batches(dynamo_client.deleteImages, images)

    lines = dynamo_client.listLines()
    print(f" - Deleting {len(lines)} line items")
    delete_in_batches(dynamo_client.deleteLines, lines)

    words = dynamo_client.listWords()
    print(f" - Deleting {len(words)} word items")
    delete_in_batches(dynamo_client.deleteWords, words)

    word_tags = dynamo_client.listWordTags()
    print(f" - Deleting {len(word_tags)} word tag items")
    delete_in_batches(dynamo_client.deleteWordTags, word_tags)

    letters = dynamo_client.listLetters()
    print(f" - Deleting {len(letters)} letter items")
    delete_in_batches(dynamo_client.deleteLetters, letters)

    receipts = dynamo_client.listReceipts()
    print(f" - Deleting {len(receipts)} receipt items")
    delete_in_batches(dynamo_client.deleteReceipts, receipts)

    receipt_lines = dynamo_client.listReceiptLines()
    print(f" - Deleting {len(receipt_lines)} receipt line items")
    delete_in_batches(dynamo_client.deleteReceiptLines, receipt_lines)

    receipt_words = dynamo_client.listReceiptWords()
    print(f" - Deleting {len(receipt_words)} receipt word items")
    delete_in_batches(dynamo_client.deleteReceiptWords, receipt_words)

    receipt_word_tags = dynamo_client.listReceiptWordTags()
    print(f" - Deleting {len(receipt_word_tags)} receipt word tag items")
    delete_in_batches(dynamo_client.deleteReceiptWordTags, receipt_word_tags)

    receipt_letters = dynamo_client.listReceiptLetters()
    print(f" - Deleting {len(receipt_letters)} receipt letter items")
    delete_in_batches(dynamo_client.deleteReceiptLetters, receipt_letters)


def restore_s3(bucket_name: str, backup_list: List[Tuple[str, str]]) -> None:
    """
    Re-uploads the S3 files from a local backup directory.

    Args:
        bucket_name (str): Name of the S3 bucket.
        backup_list (List[Tuple[str, str]]): A list of (s3_key, local_path)
    """
    s3 = boto3.client("s3")

    for s3_key, local_path in backup_list:
        # Upload from local backup path back to the original S3 key
        s3.upload_file(local_path, bucket_name, s3_key)
        os.remove(local_path)  # Clean up the local backup file


def restore_dynamo_items(dynamo_name: str, backup_path: str) -> None:
    """
    Restores all items from a DynamoDB backup JSON file.

    Args:
        dynamo_name (str): Name of the DynamoDB table.
        backup_path (str): Path to the JSON file containing the backup.
    """
    dynamo_client = DynamoClient(dynamo_name)

    with open(backup_path, "r") as f:
        backup = json.load(f)

    # Restore each item type
    images = [Image(**image) for image in backup["images"]]
    print(f" - Restoring {len(images)} image items")
    dynamo_client.addImages(images)
    lines = [Line(**line) for line in backup["lines"]]
    print(f" - Restoring {len(lines)} line items")
    dynamo_client.addLines(lines)
    words = [Word(**word) for word in backup["words"]]
    print(f" - Restoring {len(words)} word items")
    dynamo_client.addWords(words)
    word_tags = [WordTag(**tag) for tag in backup["word_tags"]]
    print(f" - Restoring {len(word_tags)} word tag items")
    dynamo_client.addWordTags(word_tags)
    letters = [Letter(**letter) for letter in backup["letters"]]
    print(f" - Restoring {len(letters)} letter items")
    dynamo_client.addLetters(letters)
    receipts = [Receipt(**receipt) for receipt in backup["receipts"]]
    print(f" - Restoring {len(receipts)} receipt items")
    dynamo_client.addReceipts(receipts)
    receipt_lines = [ReceiptLine(**line) for line in backup["receipt_lines"]]
    print(f" - Restoring {len(receipt_lines)} receipt line items")
    dynamo_client.addReceiptLines(receipt_lines)
    receipt_words = [ReceiptWord(**word) for word in backup["receipt_words"]]
    print(f" - Restoring {len(receipt_words)} receipt word items")
    dynamo_client.addReceiptWords(receipt_words)
    receipt_word_tags = [ReceiptWordTag(**tag) for tag in backup["receipt_word_tags"]]
    print(f" - Restoring {len(receipt_word_tags)} receipt word tag items")
    dynamo_client.addReceiptWordTags(receipt_word_tags)
    receipt_letters = [ReceiptLetter(**letter) for letter in backup["receipt_letters"]]
    print(f" - Restoring {len(receipt_letters)} receipt letter items")
    dynamo_client.addReceiptLetters(receipt_letters)

    os.remove(backup_path)  # Clean up the local backup file


def assert_s3_cdn(bucket_name: str, cdn_backup: List[Tuple[str, str]]):
    """
    Asserts that the correct results are in the CDN bucket.

    This uses the CDN backup list to verify that the main PNG file and each cluster PNG file are in the bucket and that they match the expected results.
    """
    s3 = boto3.client("s3")
    for s3_key, local_path in cdn_backup:
        # Check if the file exists in the bucket
        try:
            s3.head_object(Bucket=bucket_name, Key=s3_key)
        except s3.exceptions.ClientError:
            raise AssertionError(f"CDN file not found in bucket: {s3_key}")

        # Compare the local file to the S3 file
        with open(local_path, "rb") as f:
            local_data = f.read()
        s3_data = s3.get_object(Bucket=bucket_name, Key=s3_key)["Body"].read()

        if local_data != s3_data:
            raise AssertionError(f"CDN file mismatch: {s3_key}")


def remove_key_recursively(data: Any, key_to_remove: str):
    """
    Recursively remove a given key (e.g. "timestamp_added") from a Python dict/list structure.
    """
    if isinstance(data, dict):
        # Pop the key if it exists at this level
        data.pop(key_to_remove, None)
        # Recursively handle nested dicts/lists
        for value in data.values():
            remove_key_recursively(value, key_to_remove)
    elif isinstance(data, list):
        for element in data:
            remove_key_recursively(element, key_to_remove)
    # If it's neither dict nor list, do nothing (e.g. string, int, etc.)


def assert_s3_raw(bucket_name: str, raw_backup: List[Tuple[str, str]]):
    """
    Verifies that each .png, .json, and _results.json file in the RAW bucket
    matches the local backup.

    Only the '_results.json' file ignores differences in 'timestamp_added'.
    """

    s3 = boto3.client("s3")

    # 1) Build a quick lookup so we can find local_path by s3_key
    local_lookup = {s3_key: local_path for (s3_key, local_path) in raw_backup}

    # 2) Find the actual RAW keys in S3 (the same logic used by backup_raw_s3)
    raw_key_groups = get_raw_keys(bucket_name)
    # => [ (png_key, ocr_json_key, results_json_key), ... ]

    for png_key, ocr_json_key, results_json_key in raw_key_groups:

        # 2a) Check that all three exist locally
        if png_key not in local_lookup:
            raise AssertionError(f"Local backup missing PNG for: {png_key}")
        if ocr_json_key not in local_lookup:
            raise AssertionError(f"Local backup missing OCR JSON for: {ocr_json_key}")
        if results_json_key not in local_lookup:
            raise AssertionError(
                f"Local backup missing results JSON for: {results_json_key}"
            )

        # 3) Compare the PNG file
        compare_png_file(s3, bucket_name, png_key, local_lookup[png_key])

        # 4) Compare the .json file (OCR). We do NOT ignore timestamp_added.
        compare_json_file(
            s3,
            bucket_name,
            ocr_json_key,
            local_lookup[ocr_json_key],
            remove_incremental=False,
        )

        # 5) Compare the _results.json file, ignoring timestamp_added
        compare_json_file(
            s3,
            bucket_name,
            results_json_key,
            local_lookup[results_json_key],
            remove_incremental=True,
        )


def compare_png_file(s3, bucket_name: str, s3_key: str, local_path: str):
    """
    Compare a PNG file by direct byte equality. If there's a mismatch, save both
    the local and the S3 version in the 'infra/ingestion/test_failures' directory,
    then raise an AssertionError.
    """
    # Confirm S3 object exists
    try:
        s3.head_object(Bucket=bucket_name, Key=s3_key)
    except s3.exceptions.ClientError:
        raise AssertionError(f"PNG file not found in bucket: {s3_key}")

    # Read local
    with open(local_path, "rb") as f:
        local_data = f.read()

    # Read from S3
    s3_data = s3.get_object(Bucket=bucket_name, Key=s3_key)["Body"].read()

    # Direct compare
    if local_data != s3_data:
        # 1) Save the mismatched PNGs for debugging
        #    Replace slashes so we can use the s3_key safely as part of a filename
        safe_key = s3_key.replace("/", "_")

        local_fail_path = FAILURE_DIR / f"local_{safe_key}"
        s3_fail_path = FAILURE_DIR / f"s3_{safe_key}"

        with open(local_fail_path, "wb") as f:
            f.write(local_data)
        with open(s3_fail_path, "wb") as f:
            f.write(s3_data)

        # 2) Show a short hex dump in the error message
        length_to_show = 200
        local_hex = binascii.hexlify(local_data[:length_to_show]).decode("ascii")
        s3_hex = binascii.hexlify(s3_data[:length_to_show]).decode("ascii")

        raise AssertionError(
            f"PNG mismatch: {s3_key}\n\n"
            f"Local (first {length_to_show} bytes hex):\n{local_hex}\n\n"
            f"S3    (first {length_to_show} bytes hex):\n{s3_hex}\n\n"
            f"Mismatched PNGs saved to:\n"
            f" - {local_fail_path}\n"
            f" - {s3_fail_path}"
        )


def compare_json_file(
    s3,
    bucket_name: str,
    s3_key: str,
    local_path: str,
    remove_incremental: bool = False,
):
    """
    Compare a JSON file in S3 vs. local_path. Optionally remove 'timestamp_added'
    from both. If parsing fails, compare as plain text. Show a unified diff if
    there's a mismatch. If there's a mismatch in the final JSON, save the
    two differing JSON files to FAILURE_DIR.
    """
    # Confirm S3 object exists
    try:
        s3.head_object(Bucket=bucket_name, Key=s3_key)
    except s3.exceptions.ClientError:
        raise AssertionError(f"JSON file not found in bucket: {s3_key}")

    # Read local
    with open(local_path, "rb") as f:
        local_data = f.read()

    # Read from S3
    s3_data = s3.get_object(Bucket=bucket_name, Key=s3_key)["Body"].read()

    # Quick check for exact bytes
    if local_data == s3_data:
        return

    # Attempt UTF-8 decode
    try:
        local_text = local_data.decode("utf-8")
        s3_text = s3_data.decode("utf-8")
    except UnicodeDecodeError:
        # If not valid text, fallback to a short hex dump
        length_to_show = 200
        local_hex = binascii.hexlify(local_data[:length_to_show]).decode("ascii")
        s3_hex = binascii.hexlify(s3_data[:length_to_show]).decode("ascii")
        raise AssertionError(
            f"File mismatch (binary) at {s3_key}.\n\n"
            f"Local (first {length_to_show} bytes hex):\n{local_hex}\n\n"
            f"S3    (first {length_to_show} bytes hex):\n{s3_hex}"
        )

    # Now parse JSON
    try:
        local_json = json.loads(local_text)
        s3_json = json.loads(s3_text)
    except json.JSONDecodeError:
        # If not valid JSON, do a text diff
        _raise_text_diff(s3_key, local_text, s3_text)

    # If we only want to ignore 'timestamp_added' in the _results.json file
    if remove_incremental:
        remove_key_recursively(local_json, "timestamp_added")
        remove_key_recursively(s3_json, "timestamp_added")

    # Re-serialize for final comparison
    local_normalized = json.dumps(local_json, indent=2, sort_keys=True)
    s3_normalized = json.dumps(s3_json, indent=2, sort_keys=True)

    if local_normalized != s3_normalized:
        # 1) Save the mismatch files for debugging
        safe_key = s3_key.replace("/", "_")
        local_fail_path = FAILURE_DIR / f"local_{safe_key}.json"
        s3_fail_path = FAILURE_DIR / f"s3_{safe_key}.json"

        with open(local_fail_path, "w") as f:
            f.write(local_normalized)
        with open(s3_fail_path, "w") as f:
            f.write(s3_normalized)

        raise AssertionError(f"JSON mismatch: {s3_key}")


def _raise_text_diff(s3_key: str, local_text: str, s3_text: str):
    """
    Show a unified diff of text if there's a mismatch.
    """
    diff = difflib.unified_diff(
        local_text.splitlines(keepends=True),
        s3_text.splitlines(keepends=True),
        fromfile="local",
        tofile="s3",
    )
    diff_text = "".join(diff)
    raise AssertionError(
        f"File mismatch (text): {s3_key}\nHere is the unified diff:\n{diff_text}"
    )


def assert_dynamo(dynamo_name: str, dynamo_backup_path: str):
    """
    Compares the expected DynamoDB items with the actual items in the database,
    ignoring differences in the 'timestamp_added' field.
    """
    # 1) Backup the *current* Dynamo contents so we can compare them
    current_dynamo_path = backup_dynamo_items(
        dynamo_name, backup_dir="/tmp/current_dynamo"
    )

    # 2) Load both JSON files
    with open(dynamo_backup_path, "r") as f:
        backup_data = json.load(f)
    with open(current_dynamo_path, "r") as f:
        current_data = json.load(f)

    # 3) Remove the 'incremented' fields from both
    remove_key_recursively(backup_data, "timestamp_added")
    remove_key_recursively(current_data, "timestamp_added")

    # 4) Serialize each for diffing (sorting keys ensures consistent order)
    backup_text = json.dumps(backup_data, indent=2, sort_keys=True)
    current_text = json.dumps(current_data, indent=2, sort_keys=True)

    # 5) Compare the normalized text
    if backup_text != current_text:
        old_fail_path = FAILURE_DIR / f"old_dynamo.json"
        test_fail_path = FAILURE_DIR / f"test_dynamo.json"

        with open(old_fail_path, "w") as f:
            f.write(backup_text)
        with open(test_fail_path, "w") as f:
            f.write(current_text)
        raise AssertionError(
            f"Dynamo mismatch (ignoring 'timestamp_added'). "
        )
