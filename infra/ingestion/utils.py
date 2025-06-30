import binascii
import difflib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import boto3
import pulumi
import pulumi.automation as auto
from botocore.exceptions import ClientError
from receipt_dynamo import (
    DynamoClient,
    Image,
    Letter,
    Line,
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    ReceiptWordTag,
    Word,
    WordTag,
)

FAILURE_DIR = Path("test_failures")
FAILURE_DIR.mkdir(parents=True, exist_ok=True)


def load_env() -> Dict[str, Any]:
    """
    Load Pulumi stack outputs as a simple dictionary of key -> value.
    """
    stack = auto.select_stack(
        stack_name="tnorlund/portfolio/e2e",
        project_name="portfolio",
        program=lambda: None,
    )
    return {key: val.value for key, val in stack.outputs().items()}


def bucket_exists(bucket_name: str) -> bool:
    """
    Returns True if an S3 bucket with the given name exists, else False.
    """
    s3 = boto3.client("s3")
    try:
        s3.head_bucket(Bucket=bucket_name)
        return True
    except ClientError:
        return False


def table_exists(table_name: str) -> bool:
    """
    Returns True if a DynamoDB table with the given name exists, else False.
    """
    dynamodb = boto3.client("dynamodb")
    try:
        dynamodb.describe_table(TableName=table_name)
        return True
    except ClientError:
        return False


def get_raw_keys(bucket_name: str) -> List[Tuple[str, str, str]]:
    """
    Lists the '.png', '.json', and '_results.json' keys for each image in the RAW bucket,
    grouping them by UUID. For each UUID, we expect exactly three keys:
      1) The .png image
      2) The .json file (OCR results)
      3) The _results.json file (final clustering results)

    Returns:
        A list of (png_key, ocr_json_key, results_json_key) for each image.
    """
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    response_iterator = paginator.paginate(Bucket=bucket_name)

    grouped_files = {}

    for page in response_iterator:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith("_results.json"):
                base = key[: -len("_results.json")]
                extension = "_results.json"
            else:
                base, ext = os.path.splitext(key)
                extension = ext.lower()

            if extension in (".png", ".json", "_results.json"):
                if base not in grouped_files:
                    grouped_files[base] = {}
                grouped_files[base][extension] = key

    files = []
    for base, exts in grouped_files.items():
        if all(ext in exts for ext in [".png", ".json", "_results.json"]):
            files.append((exts[".png"], exts[".json"], exts["_results.json"]))

    return files


def get_cdn_keys(bucket_name: str) -> List[Tuple[str, List[str]]]:
    """
    Lists the '.png' and '_cluster_<cluster_num>.png' keys for each 'image' in the CDN bucket,
    grouping them by UUID. For each UUID, we expect at least 2 keys:
      1) The .png image
      2) One or more _cluster_<cluster_num>.png files

    Returns:
        A list of (main_png_key, [cluster_png_keys]) for each image.
    """
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    page_iterator = paginator.paginate(Bucket=bucket_name)

    images = {}

    for page in page_iterator:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(".png"):
                continue

            filename = os.path.basename(key)
            base, _ = os.path.splitext(filename)

            if "_cluster_" in base:
                uuid = base.split("_cluster_")[0]
            else:
                uuid = base

            if uuid not in images:
                images[uuid] = {"main": None, "clusters": []}

            if "_cluster_" in base:
                images[uuid]["clusters"].append(key)
            else:
                images[uuid]["main"] = key

    results = []
    for uuid, info in images.items():
        if info["main"] and info["clusters"]:
            results.append((info["main"], info["clusters"]))

    return results


def backup_raw_s3(
    bucket_name: str, backup_dir: str = "/tmp/raw_backup"
) -> List[Tuple[str, str]]:
    """
    Download each .png, .json, and _results.json file from the RAW bucket,
    saving them locally for backup.

    Returns:
        A list of (s3_key, local_path) indicating each file's original S3 key and backup path.
    """
    s3 = boto3.client("s3")
    os.makedirs(backup_dir, exist_ok=True)

    raw_key_groups = get_raw_keys(bucket_name)
    backup_list = []

    for png_key, ocr_json_key, results_json_key in raw_key_groups:
        for s3_key in (png_key, ocr_json_key, results_json_key):
            local_filename = s3_key.replace("/", "_")
            local_path = str(Path(backup_dir) / local_filename)
            s3.download_file(bucket_name, s3_key, local_path)
            backup_list.append((s3_key, local_path))

    return backup_list


def backup_cdn_s3(
    bucket_name: str, backup_dir: str = "/tmp/cdn_backup"
) -> List[Tuple[str, str]]:
    """
    Download each main .png and its corresponding _cluster_<cluster_num>.png files
    from the CDN bucket, saving them locally for backup.

    Returns:
        A list of (s3_key, local_path) for each downloaded file.
    """
    s3 = boto3.client("s3")
    os.makedirs(backup_dir, exist_ok=True)

    cdn_key_groups = get_cdn_keys(bucket_name)
    backup_list = []

    for main_key, cluster_keys in cdn_key_groups:
        keys_to_backup = [main_key] + cluster_keys
        for s3_key in keys_to_backup:
            local_filename = s3_key.replace("/", "_")
            local_path = str(Path(backup_dir) / local_filename)
            s3.download_file(bucket_name, s3_key, local_path)
            backup_list.append((s3_key, local_path))

    return backup_list


def backup_dynamo_items(
    dynamo_name: str, backup_dir: str = "/tmp/dynamo_backup"
) -> str:
    """
    List all items from the specified DynamoDB table (via DynamoClient)
    and save them to a JSON file locally.

    Returns:
        The path to the resulting JSON backup file.
    """
    dynamo_client = DynamoClient(dynamo_name)
    os.makedirs(backup_dir, exist_ok=True)

    images, _ = dynamo_client.list_images()
    lines = dynamo_client.list_lines()
    words = dynamo_client.list_words()
    word_tags = dynamo_client.list_word_tags()
    letters = dynamo_client.list_letters()
    receipts = dynamo_client.list_receipts()
    receipt_lines = dynamo_client.list_receipt_lines()
    receipt_words = dynamo_client.list_receipt_words()
    receipt_word_tags = dynamo_client.list_receipt_word_tags()
    receipt_letters = dynamo_client.list_receipt_letters()

    backup_path = Path(backup_dir) / "dynamo_backup.json"
    with open(backup_path, "w") as f:
        f.write(
            json.dumps(
                {
                    "images": [dict(i) for i in images],
                    "lines": [
                        {
                            k: v
                            for k, v in dict(ln).items()
                            if k not in ("histogram", "num_chars")
                        }
                        for ln in lines
                    ],
                    "words": [
                        {
                            k: v
                            for k, v in dict(wd).items()
                            if k not in ("histogram", "num_chars")
                        }
                        for wd in words
                    ],
                    "word_tags": [dict(tg) for tg in word_tags],
                    "letters": [dict(lt) for lt in letters],
                    "receipts": [dict(rc) for rc in receipts],
                    "receipt_lines": [
                        {
                            k: v
                            for k, v in dict(rl).items()
                            if k not in ("histogram", "num_chars")
                        }
                        for rl in receipt_lines
                    ],
                    "receipt_words": [
                        {
                            k: v
                            for k, v in dict(rw).items()
                            if k not in ("histogram", "num_chars")
                        }
                        for rw in receipt_words
                    ],
                    "receipt_word_tags": [dict(rwt) for rwt in receipt_word_tags],
                    "receipt_letters": [dict(rl) for rl in receipt_letters],
                },
                indent=2,
            )
        )

    return str(backup_path)


def group_by_uuid(backups: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    """
    Group a list of (s3_key, local_path) tuples by the UUID portion of the S3 key.
    """
    grouped = defaultdict(list)
    for s3_key, local_path in backups:
        uuid = extract_uuid_from_key(s3_key)
        grouped[uuid].append(local_path)
    return dict(grouped)


def parse_dynamo_json(backup_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Parse the Dynamo JSON backup file, returning a structure keyed by UUID.

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

        dynamo_grouped[uuid]["image"] = image
        dynamo_grouped[uuid]["lines"] = [ln for ln in lines if ln.image_id == image.id]
        dynamo_grouped[uuid]["words"] = [wd for wd in words if wd.image_id == image.id]
        dynamo_grouped[uuid]["word_tags"] = [
            tg for tg in word_tags if tg.image_id == image.id
        ]
        dynamo_grouped[uuid]["letters"] = [
            lt for lt in letters if lt.image_id == image.id
        ]

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
    Combine backed-up items from RAW S3, CDN S3, and Dynamo into a single dict keyed by UUID.

    Raises:
        ValueError: If multiple or zero matching images are found for a given UUID.
    """
    raw_grouped = group_by_uuid(raw_backup)
    cdn_grouped = group_by_uuid(cdn_backup)
    dynamo_grouped = parse_dynamo_json(dynamo_backup_path)

    all_uuids = (
        set(raw_grouped.keys()) | set(cdn_grouped.keys()) | set(dynamo_grouped.keys())
    )
    all_images = [grp["image"] for grp in dynamo_grouped.values() if "image" in grp]

    combined: Dict[str, Dict[str, Any]] = {}
    for uuid in all_uuids:
        images_with_uuid = [img for img in all_images if uuid in img.raw_s3_key]
        if len(images_with_uuid) > 1:
            raise ValueError(f"Multiple images found for UUID: {uuid}")
        if not images_with_uuid:
            raise ValueError(f"No image found for UUID: {uuid}")

        combined[uuid] = {
            "raw": raw_grouped.get(uuid, []),
            "cdn": cdn_grouped.get(uuid, []),
            "dynamo": dynamo_grouped.get(uuid, {}),
        }

    return combined


def extract_uuid_from_key(s3_key: str) -> str:
    """
    Attempt to extract the UUID portion from a given S3 key.
    Example: "assets/218e0b40-7231-42fb-83c5-fc5d44970198_results.json"
      -> "218e0b40-7231-42fb-83c5-fc5d44970198"
    """
    filename = os.path.basename(s3_key)
    base, _ = os.path.splitext(filename)

    if base.endswith("_results"):
        base = base[: -len("_results")]

    if "_cluster_" in base:
        return base.split("_cluster_")[0]
    return base


def extract_uuid_from_image(image: Image) -> str:
    """
    Extract the UUID from an Image's raw_s3_key by taking the basename (minus extension).
    """
    return os.path.splitext(os.path.basename(image.raw_s3_key))[0]


def delete_raw_s3(bucket_name: str) -> None:
    """
    Delete all .png, .json, and _results.json files in the RAW S3 bucket.
    """
    s3 = boto3.client("s3")
    raw_key_groups = get_raw_keys(bucket_name)
    for png_key, ocr_json_key, results_json_key in raw_key_groups:
        for s3_key in (png_key, ocr_json_key, results_json_key):
            pulumi.log.info(f" - Deleting {s3_key}")
            s3.delete_object(Bucket=bucket_name, Key=s3_key)


def delete_cdn_s3(bucket_name: str) -> None:
    """
    Delete the main .png file and all _cluster_<cluster_num>.png files
    in the CDN S3 bucket.
    """
    s3 = boto3.client("s3")
    cdn_key_groups = get_cdn_keys(bucket_name)
    for main_png_key, cluster_keys in cdn_key_groups:
        for s3_key in [main_png_key] + cluster_keys:
            pulumi.log.info(f" - Deleting {s3_key}")
            s3.delete_object(Bucket=bucket_name, Key=s3_key)


def delete_in_batches(delete_func, items, chunk_size=1000):
    """
    Delete items in batches of `chunk_size` using the provided `delete_func`.
    """
    total = len(items)
    if total == 0:
        return

    for start in range(0, total, chunk_size):
        batch = items[start : start + chunk_size]
        delete_func(batch)
        remaining = total - (start + len(batch))
        pulumi.log.info(f"   Deleted {len(batch)} items. {remaining} remaining...")


def delete_dynamo_items(dynamo_name: str) -> None:
    """
    Delete all items from the given DynamoDB table via DynamoClient.
    """
    dynamo_client = DynamoClient(dynamo_name)

    images, _ = dynamo_client.list_images()
    pulumi.log.info(f" - Deleting {len(images)} image items")
    delete_in_batches(dynamo_client.deleteImages, images)

    lines = dynamo_client.list_lines()
    pulumi.log.info(f" - Deleting {len(lines)} line items")
    delete_in_batches(dynamo_client.deleteLines, lines)

    words = dynamo_client.list_words()
    pulumi.log.info(f" - Deleting {len(words)} word items")
    delete_in_batches(dynamo_client.deleteWords, words)

    word_tags = dynamo_client.list_word_tags()
    pulumi.log.info(f" - Deleting {len(word_tags)} word tag items")
    delete_in_batches(dynamo_client.deleteWordTags, word_tags)

    letters = dynamo_client.list_letters()
    pulumi.log.info(f" - Deleting {len(letters)} letter items")
    delete_in_batches(dynamo_client.deleteLetters, letters)

    receipts = dynamo_client.list_receipts()
    pulumi.log.info(f" - Deleting {len(receipts)} receipt items")
    delete_in_batches(dynamo_client.deleteReceipts, receipts)

    receipt_lines = dynamo_client.list_receipt_lines()
    pulumi.log.info(f" - Deleting {len(receipt_lines)} receipt line items")
    delete_in_batches(dynamo_client.deleteReceiptLines, receipt_lines)

    receipt_words = dynamo_client.list_receipt_words()
    pulumi.log.info(f" - Deleting {len(receipt_words)} receipt word items")
    delete_in_batches(dynamo_client.deleteReceiptWords, receipt_words)

    receipt_word_tags = dynamo_client.list_receipt_word_tags()
    pulumi.log.info(f" - Deleting {len(receipt_word_tags)} receipt word tag items")
    delete_in_batches(dynamo_client.deleteReceiptWordTags, receipt_word_tags)

    receipt_letters = dynamo_client.list_receipt_letters()
    pulumi.log.info(f" - Deleting {len(receipt_letters)} receipt letter items")
    delete_in_batches(dynamo_client.deleteReceiptLetters, receipt_letters)


def restore_s3(bucket_name: str, backup_list: List[Tuple[str, str]]) -> None:
    """
    Re-upload each file from the local backup path to the original S3 key.
    Then remove the local backup file.
    """
    s3 = boto3.client("s3")

    for s3_key, local_path in backup_list:
        s3.upload_file(local_path, bucket_name, s3_key)
        os.remove(local_path)


def restore_dynamo_items(dynamo_name: str, backup_path: str) -> None:
    """
    Read the JSON backup file from disk and re-insert all items into DynamoDB.
    Then remove the local backup file.
    """
    dynamo_client = DynamoClient(dynamo_name)

    with open(backup_path, "r") as f:
        backup = json.load(f)

    images = [Image(**image) for image in backup["images"]]
    pulumi.log.info(f" - Restoring {len(images)} image items")
    dynamo_client.add_images(images)

    lines = [Line(**line) for line in backup["lines"]]
    pulumi.log.info(f" - Restoring {len(lines)} line items")
    dynamo_client.add_lines(lines)

    words = [Word(**word) for word in backup["words"]]
    pulumi.log.info(f" - Restoring {len(words)} word items")
    dynamo_client.add_words(words)

    word_tags = [WordTag(**tag) for tag in backup["word_tags"]]
    pulumi.log.info(f" - Restoring {len(word_tags)} word tag items")
    dynamo_client.add_word_tags(word_tags)

    letters = [Letter(**letter) for letter in backup["letters"]]
    pulumi.log.info(f" - Restoring {len(letters)} letter items")
    dynamo_client.add_letters(letters)

    receipts = [Receipt(**receipt) for receipt in backup["receipts"]]
    pulumi.log.info(f" - Restoring {len(receipts)} receipt items")
    dynamo_client.add_receipts(receipts)

    receipt_lines = [ReceiptLine(**line) for line in backup["receipt_lines"]]
    pulumi.log.info(f" - Restoring {len(receipt_lines)} receipt line items")
    dynamo_client.add_receipt_lines(receipt_lines)

    receipt_words = [ReceiptWord(**word) for word in backup["receipt_words"]]
    pulumi.log.info(f" - Restoring {len(receipt_words)} receipt word items")
    dynamo_client.add_receipt_words(receipt_words)

    receipt_word_tags = [ReceiptWordTag(**tag) for tag in backup["receipt_word_tags"]]
    pulumi.log.info(f" - Restoring {len(receipt_word_tags)} receipt word tag items")
    dynamo_client.add_receipt_word_tags(receipt_word_tags)

    receipt_letters = [ReceiptLetter(**letter) for letter in backup["receipt_letters"]]
    pulumi.log.info(f" - Restoring {len(receipt_letters)} receipt letter items")
    dynamo_client.add_receipt_letters(receipt_letters)

    os.remove(backup_path)


def assert_s3_cdn(bucket_name: str, cdn_backup: List[Tuple[str, str]]) -> None:
    """
    Verify each PNG in cdn_backup exists in the bucket and matches exactly.
    """
    s3 = boto3.client("s3")
    for s3_key, local_path in cdn_backup:
        try:
            s3.head_object(Bucket=bucket_name, Key=s3_key)
        except ClientError:
            raise AssertionError(f"CDN file not found in bucket: {s3_key}")

        with open(local_path, "rb") as f:
            local_data = f.read()
        s3_data = s3.get_object(Bucket=bucket_name, Key=s3_key)["Body"].read()

        if local_data != s3_data:
            raise AssertionError(f"CDN file mismatch: {s3_key}")


def remove_key_recursively(data: Any, key_to_remove: str) -> None:
    """
    Recursively remove a given key (e.g. "timestamp_added") from dict/list structures.
    """
    if isinstance(data, dict):
        data.pop(key_to_remove, None)
        for value in data.values():
            remove_key_recursively(value, key_to_remove)
    elif isinstance(data, list):
        for element in data:
            remove_key_recursively(element, key_to_remove)


def assert_s3_raw(bucket_name: str, raw_backup: List[Tuple[str, str]]) -> None:
    """
    Verify each .png, .json, and _results.json file from raw_backup
    is present in S3 and matches the local copy (with special handling for
    timestamp in _results.json).
    """
    s3 = boto3.client("s3")
    local_lookup = {s3_key: local_path for (s3_key, local_path) in raw_backup}
    raw_key_groups = get_raw_keys(bucket_name)

    for png_key, ocr_json_key, results_json_key in raw_key_groups:
        if png_key not in local_lookup:
            raise AssertionError(f"Local backup missing PNG for: {png_key}")
        if ocr_json_key not in local_lookup:
            raise AssertionError(f"Local backup missing OCR JSON for: {ocr_json_key}")
        if results_json_key not in local_lookup:
            raise AssertionError(
                f"Local backup missing results JSON for: {results_json_key}"
            )

        compare_png_file(s3, bucket_name, png_key, local_lookup[png_key])

        compare_json_file(
            s3,
            bucket_name,
            ocr_json_key,
            local_lookup[ocr_json_key],
            remove_incremental=False,
        )

        compare_json_file(
            s3,
            bucket_name,
            results_json_key,
            local_lookup[results_json_key],
            remove_incremental=True,
        )


def compare_png_file(s3, bucket_name: str, s3_key: str, local_path: str) -> None:
    """
    Compare a PNG file by direct byte equality. If there's a mismatch,
    save both files under test_failures for debugging.
    """
    try:
        s3.head_object(Bucket=bucket_name, Key=s3_key)
    except ClientError:
        raise AssertionError(f"PNG file not found in bucket: {s3_key}")

    with open(local_path, "rb") as f:
        local_data = f.read()
    s3_data = s3.get_object(Bucket=bucket_name, Key=s3_key)["Body"].read()

    if local_data != s3_data:
        safe_key = s3_key.replace("/", "_")
        local_fail_path = FAILURE_DIR / f"local_{safe_key}"
        s3_fail_path = FAILURE_DIR / f"s3_{safe_key}"

        with open(local_fail_path, "wb") as f:
            f.write(local_data)
        with open(s3_fail_path, "wb") as f:
            f.write(s3_data)

        length_to_show = 200
        local_hex = binascii.hexlify(local_data[:length_to_show]).decode("ascii")
        s3_hex = binascii.hexlify(s3_data[:length_to_show]).decode("ascii")

        raise AssertionError(
            f"PNG mismatch: {s3_key}\n"
            f"Local (first {length_to_show} bytes hex):\n{local_hex}\n\n"
            f"S3 (first {length_to_show} bytes hex):\n{s3_hex}\n\n"
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
) -> None:
    """
    Compare a JSON file in S3 vs a local file. Optionally remove 'timestamp_added'
    from both before comparing. Shows a diff if there's a mismatch.
    """
    try:
        s3.head_object(Bucket=bucket_name, Key=s3_key)
    except ClientError:
        raise AssertionError(f"JSON file not found in bucket: {s3_key}")

    with open(local_path, "rb") as f:
        local_data = f.read()

    s3_data = s3.get_object(Bucket=bucket_name, Key=s3_key)["Body"].read()

    if local_data == s3_data:
        return

    try:
        local_text = local_data.decode("utf-8")
        s3_text = s3_data.decode("utf-8")
    except UnicodeDecodeError:
        length_to_show = 200
        local_hex = binascii.hexlify(local_data[:length_to_show]).decode("ascii")
        s3_hex = binascii.hexlify(s3_data[:length_to_show]).decode("ascii")
        raise AssertionError(
            f"File mismatch (binary) at {s3_key}.\n"
            f"Local (first {length_to_show} bytes hex):\n{local_hex}\n\n"
            f"S3 (first {length_to_show} bytes hex):\n{s3_hex}"
        )

    try:
        local_json = json.loads(local_text)
        s3_json = json.loads(s3_text)
    except json.JSONDecodeError:
        _raise_text_diff(s3_key, local_text, s3_text)

    if remove_incremental:
        remove_key_recursively(local_json, "timestamp_added")
        remove_key_recursively(s3_json, "timestamp_added")

    local_normalized = json.dumps(local_json, indent=2, sort_keys=True)
    s3_normalized = json.dumps(s3_json, indent=2, sort_keys=True)

    if local_normalized != s3_normalized:
        safe_key = s3_key.replace("/", "_")
        local_fail_path = FAILURE_DIR / f"local_{safe_key}.json"
        s3_fail_path = FAILURE_DIR / f"s3_{safe_key}.json"

        with open(local_fail_path, "w") as f:
            f.write(local_normalized)
        with open(s3_fail_path, "w") as f:
            f.write(s3_normalized)

        raise AssertionError(f"JSON mismatch: {s3_key}")


def _raise_text_diff(s3_key: str, local_text: str, s3_text: str) -> None:
    """
    Show a unified diff of text if there's a mismatch in non-JSON content.
    """
    diff = difflib.unified_diff(
        local_text.splitlines(keepends=True),
        s3_text.splitlines(keepends=True),
        fromfile="local",
        tofile="s3",
    )
    diff_text = "".join(diff)
    raise AssertionError(f"File mismatch (text): {s3_key}\nUnified diff:\n{diff_text}")


def assert_dynamo(dynamo_name: str, dynamo_backup_path: str) -> None:
    """
    Compare the expected DynamoDB items with the actual items in the database,
    ignoring 'timestamp_added'.
    """
    current_dynamo_path = backup_dynamo_items(
        dynamo_name, backup_dir="/tmp/current_dynamo"
    )

    with open(dynamo_backup_path, "r") as f:
        backup_data = json.load(f)

    with open(current_dynamo_path, "r") as f:
        current_data = json.load(f)

    remove_key_recursively(backup_data, "timestamp_added")
    remove_key_recursively(current_data, "timestamp_added")

    backup_text = json.dumps(backup_data, indent=2, sort_keys=True)
    current_text = json.dumps(current_data, indent=2, sort_keys=True)

    if backup_text != current_text:
        old_fail_path = FAILURE_DIR / "old_dynamo.json"
        test_fail_path = FAILURE_DIR / "test_dynamo.json"

        with open(old_fail_path, "w") as f:
            f.write(backup_text)
        with open(test_fail_path, "w") as f:
            f.write(current_text)

        raise AssertionError(
            "Dynamo mismatch (ignoring 'timestamp_added'). See test_failures folder."
        )
