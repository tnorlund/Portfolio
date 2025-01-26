import os
from pathlib import Path
from typing import List, Tuple
import pulumi.automation as auto
import boto3

def load_env():
    stack = auto.select_stack(
        stack_name="tnorlund/portfolio/e2e",
        project_name="portfolio",
        program=lambda: None,
    )

    # Convert Pulumi OutputValue objects to raw Python values
    return {key: val.value for key, val in stack.outputs().items()}

def bucket_exists(bucket_name: str) -> bool:
    s3 = boto3.client('s3')
    try:
        s3.head_bucket(Bucket=bucket_name)
        return True
    except boto3.exceptions.botocore.client.ClientError:
        return False
    
def table_exists(table_name: str) -> bool:
    dynamodb = boto3.client('dynamodb')
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
    s3 = boto3.client('s3')
    paginator = s3.get_paginator('list_objects_v2')
    response_iterator = paginator.paginate(Bucket=bucket_name)
    grouped_files = {}

    for page in response_iterator:
        # 'Contents' might not be present if the bucket is empty, so use get(..., [])
        for obj in page.get('Contents', []):
            key = obj['Key']

            # We need special handling to distinguish between `.json` and `_results.json`.
            if key.lower().endswith('_results.json'):
                # If the key is something like '123abc_results.json', 
                # we take everything up to '_results.json' as the base.
                base = key[:-len('_results.json')]
                extension = '_results.json'
            else:
                # Otherwise, just split by extension as usual.
                base, ext = os.path.splitext(key)
                extension = ext.lower()

            # Only care about .png, .json, or _results.json
            if extension in ('.png', '.json', '_results.json'):
                if base not in grouped_files:
                    grouped_files[base] = {}
                grouped_files[base][extension] = key

    # Build the final list of triples (png, ocr_json, results_json)
    files = []
    for base, exts in grouped_files.items():
        if all(ext in exts for ext in ['.png', '.json', '_results.json']):
            files.append((exts['.png'], exts['.json'], exts['_results.json']))

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
    s3 = boto3.client('s3')
    paginator = s3.get_paginator('list_objects_v2')
    page_iterator = paginator.paginate(Bucket=bucket_name)

    # We'll keep track of each "UUID" in this dict:
    #   images[uuid] = {'main': None, 'clusters': []}
    images = {}

    for page in page_iterator:
        for obj in page.get('Contents', []):
            # Filter to only objects with StorageClass == 'STANDARD_IA'
            if obj.get('StorageClass') != 'STANDARD_IA':
                continue

            key = obj['Key']
            # We only care about .png
            if not key.lower().endswith('.png'):
                continue

            # Take the filename portion only, to ignore directory prefixes:
            # e.g. "assets/15e4c27d_cluster_00001.png" -> "15e4c27d_cluster_00001.png"
            filename = os.path.basename(key)

            # Split off the .png extension
            base, _ = os.path.splitext(filename)

            # If it's a cluster image, it might match something like:
            #   <uuid>_cluster_00001.png
            # We'll extract the UUID portion before '_cluster_...'
            if '_cluster_' in base:
                uuid = base.split('_cluster_')[0]
            else:
                uuid = base  # It's the main .png file

            # Make sure this UUID is tracked
            if uuid not in images:
                images[uuid] = {'main': None, 'clusters': []}

            # Assign the S3 key to either the main image or a cluster image
            if '_cluster_' in base:
                images[uuid]['clusters'].append(key)
            else:
                images[uuid]['main'] = key

    # Build the final list of (main_png, [cluster_pngs]) tuples
    results = []
    for uuid, info in images.items():
        # Only return if we actually have the main file AND at least one cluster
        if info['main'] and info['clusters']:
            results.append((info['main'], info['clusters']))

    return results

def backup_raw_s3(bucket_name: str, backup_dir: str = "/tmp/raw_backup") -> List[Tuple[str, str]]:
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

    for (png_key, ocr_json_key, results_json_key) in raw_key_groups:
        # Each group has three files: .png, .json, and _results.json
        for s3_key in (png_key, ocr_json_key, results_json_key):
            local_filename = s3_key.replace("/", "_")  # Flatten any subfolders into filename
            local_path = str(Path(backup_dir) / local_filename)

            # Download file to local backup directory
            s3.download_file(bucket_name, s3_key, local_path)

            backup_list.append((s3_key, local_path))

    return backup_list

def backup_cdn_s3(bucket_name: str, backup_dir: str = "/tmp/cdn_backup") -> List[Tuple[str, str]]:
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
            local_filename = s3_key.replace("/", "_")  # Flatten any subfolders into filename
            local_path = str(Path(backup_dir) / local_filename)

            # Download file to local backup directory
            s3.download_file(bucket_name, s3_key, local_path)

            backup_list.append((s3_key, local_path))

    return backup_list

def delete_raw_s3(bucket_name: str) -> None:
    """
    Uses get_raw_keys(bucket_name) to find the .png, .json, and _results.json
    files for each image in the RAW S3 bucket, then deletes them.

    Args:
        bucket_name (str): Name of the RAW S3 bucket.
    """
    s3 = boto3.client("s3")
    raw_key_groups = get_raw_keys(bucket_name)
    for (png_key, ocr_json_key, results_json_key) in raw_key_groups:
        for s3_key in (png_key, ocr_json_key, results_json_key):
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
    for (main_png_key, cluster_keys) in cdn_key_groups:
        for s3_key in [main_png_key] + cluster_keys:
            s3.delete_object(Bucket=bucket_name, Key=s3_key)

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
