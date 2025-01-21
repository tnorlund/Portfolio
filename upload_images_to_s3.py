from typing import Generator
import os
from time import sleep
from uuid import uuid4
import subprocess
import boto3
import json
from botocore.exceptions import ClientError
from dynamo import DynamoClient
import hashlib
import argparse
from pulumi.automation import select_stack


def calculate_sha256(file_path: str):
    """
    Calculate the SHA-256 hash of a file.

    Args:
        file_path (str): The path to the file to hash.

    Returns:
        str: The SHA-256 hash of the file.
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_image_indexes(client: DynamoClient, number_images: int) -> list[int]:
    """
    Assign IDs to new images based on the current IDs in the database.

    This will fill in any "gaps" (missing IDs in the sequence) first, then
    continue numbering after the highest existing ID if more IDs are needed.

    Args:
        client (DynamoClient): The DynamoDB client
        number_images (int): The number of local images to be uploaded

    Returns:
        list[int]: A list of image IDs to assign to the images
    """
    # Get existing images and their indexes (IDs)
    images = client.listImages()

    # If there are no images, just start from 1
    if not images:
        return list(range(1, number_images + 1))

    # Extract existing IDs and sort them
    existing_ids = sorted(
        [image.id for image in images]
    )  # or sorted(list(images.keys()))

    # Convert existing IDs into a set for O(1) lookups
    existing_ids_set = set(existing_ids)

    # We'll collect the new IDs in this list
    new_ids = []

    # Start checking for free IDs from index 1 upwards
    candidate = 1

    # Keep going until we've assigned all the needed IDs
    while len(new_ids) < number_images:
        if candidate not in existing_ids_set:
            new_ids.append(candidate)
        candidate += 1

    return new_ids




def chunked(iterable, n: int) -> Generator:
    """Yield successive n-sized chunks from an iterable."""
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]


def load_env(env: str = "dev") -> tuple[str, str, str]:
    """
    Uses pulumi to get the values of the RAW_IMAGE_BUCKET, LAMBDA_FUNCTION, DYNAMO_DB_TABLE.

    Returns:
        tuple[str, str, str]: The value of the RAW_IMAGE_BUCKET, LAMBDA_FUNCTION, DYNAMO_DB_TABLE from pulumi.
    """
    # The working directory is in the "development" directory next to this script. Get the full path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    work_dir = os.path.join(script_dir, "development")

    if not env:
        raise ValueError("The ENV environment variable is not set")
    stack_name = env.lower()
    project_name = "development"
    stack = select_stack(
        stack_name=stack_name,      # Your stack name
        project_name=project_name,  # Your project name from Pulumi.yaml
        work_dir=work_dir,          # Path to the Pulumi project (if needed)
    )
    outputs = stack.outputs()
    raw_bucket = str(outputs["image_bucket_name"].value)
    lambda_function = str(outputs["cluster_lambda_function_name"].value)
    dynamo_db_table = str(outputs["table_name"].value)
    return raw_bucket, lambda_function, dynamo_db_table


def run_swift_script(output_directory: str, list_of_image_paths: list[str]) -> bool:
    """
    Run the Swift script to process the images.

    Args:
        output_directory (str): The directory where the output JSON files will be saved.
        list_of_image_paths (list[str]): A list of image paths to process.

    Returns:
        bool: True if the script ran successfully, False otherwise.
    """
    swift_script = os.path.join(os.path.dirname(__file__), "OCRSwift.swift")
    try:
        swift_args = ["swift", swift_script, output_directory] + list_of_image_paths
        subprocess.run(swift_args, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running swift script: {e}")
        return False
    return True


def calc_avg_angle_degrees(json_path: str) -> float:
    with open(json_path, "r") as json_file:
        ocr_data = json.load(json_file)
        total_angle_degrees = 0
        for line in ocr_data["lines"]:
            total_angle_degrees += line["angle_degrees"]
        return total_angle_degrees / len(ocr_data["lines"])


def check_if_already_in_s3(bucket_name: str, s3_image_object_name: str):
    s3 = boto3.client("s3")
    try:
        s3.head_object(Bucket=bucket_name, Key=s3_image_object_name)
        # If no exception is thrown, the object exists
        raise FileExistsError(
            f"The object '{s3_image_object_name}' already exists in "
            f"bucket '{bucket_name}'. Aborting."
        )
    except ClientError as e:
        # If we get a 404 error, the object does not exist, and we can upload.
        if e.response["Error"]["Code"] != "404":
            # Some other error occurred; raise it.
            raise e


def compare_local(bucket_name, local_files, dynamo_table_name) -> list[str]:
    """
    Compares local files to DynamoDB.

    Compares the sha256 and file names with what's in DynamoDB. If a file is found it
    is not uploaded.

    Returns:
        list[str]: The files that should actually be uploaded
    """
    dynamo_client = DynamoClient(dynamo_table_name)
    hashes_in_dynamo = [image.sha256 for image in dynamo_client.listImages()]
    hashes_local = [calculate_sha256(file) for file in local_files]
    duplicates = set(hashes_local).intersection(hashes_in_dynamo)
    print(f"Found {len(duplicates)} duplicates in DynamoDB.")
    return [file for file in local_files if calculate_sha256(file) not in duplicates]


def upload_files_with_uuid_in_batches(
    directory,
    bucket_name,
    lambda_function,
    dynamodb_table_name,
    path="raw/",
    batch_size=10,
):
    """
    Uploads png and json files in the given directory to the specified S3 bucket
    with a UUID-based object name, but processes them in batches of size `batch_size`.
    """
    s3 = boto3.client("s3")
    # Get the full path of all PNG files in the directory
    all_png_files = [
        os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".png")
    ]
    files_to_upload = compare_local(bucket_name, all_png_files, dynamodb_table_name)
    print(f"Found {len(files_to_upload)} files to upload.")
    if not files_to_upload:
        print("No files to upload.")
        return
    # Compare all local PNG files with the ones in DynamoDB and the S3 bucket
    image_indexes = get_image_indexes(
        DynamoClient(dynamodb_table_name), len(files_to_upload)
    )

    # Split files_to_upload into batches
    for batch_index, batch in enumerate(chunked(files_to_upload, batch_size), start=1):
        print(f"\nProcessing batch #{batch_index} with up to {batch_size} files...")

        # Make a temporary working directory
        temp_dir = os.path.join(directory, "temp")
        # Delete the temp dir if it already exists
        if os.path.exists(temp_dir):
            for file in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, file))
            os.rmdir(temp_dir)

        os.mkdir(temp_dir)
        for file_name in batch:
            os.system(f"cp {file_name} {temp_dir}")

        for file in os.listdir(temp_dir):
            os.rename(
                os.path.join(temp_dir, file), os.path.join(temp_dir, f"{uuid4()}.png")
            )

        files_in_temp = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir)]
        # run_swift_script(temp_dir, files_in_temp)
        if not run_swift_script(temp_dir, files_in_temp):
            raise RuntimeError("Error running Swift script")

        # Upload all files in the temporary directory to S3
        for file in os.listdir(temp_dir):
            print(f"Uploading {file:<41} to S3 -> s3://{bucket_name}/{path}{file}")
            s3.upload_file(os.path.join(temp_dir, file), bucket_name, f"{path}{file}")

        uuids = list({file.split(".")[0] for file in os.listdir(temp_dir)})

        # Delete the temporary working directory
        for file in os.listdir(temp_dir):
            os.remove(os.path.join(temp_dir, file))
        os.rmdir(temp_dir)

        # Invoke Lambda function
        lambda_client = boto3.client("lambda")
        for index, uuid in enumerate(uuids):
            image_id = image_indexes[(batch_index - 1) * batch_size + index]
            print(
                f"Invoking Lambda function for UUID {uuid} and Image ID {image_id}..."
            )
            lambda_client.invoke(
                FunctionName=lambda_function,
                InvocationType="Event",
                Payload=json.dumps(
                    {
                        "uuid": uuid,
                        "s3_path": path,
                        "image_id": image_id,
                    }
                ),
            )

        print(f"Finished batch #{batch_index}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload images to S3")
    parser.add_argument(
        "--directory_to_upload", type=str, help="Directory containing images to upload"
    )
    parser.add_argument(
        "--env",
        type=str,
        help="The environment to use (e.g., 'dev', 'prod')",
        default="dev",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Delete all items from DynamoDB tables before uploading",
        default=False,
    )
    args = parser.parse_args()

    RAW_IMAGE_BUCKET, LAMBDA_FUNCTION, DYNAMO_DB_TABLE = load_env(args.env)

    if args.debug:
        print("Deleting all items from DynamoDB tables...")
        dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
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

    upload_files_with_uuid_in_batches(
        args.directory_to_upload, RAW_IMAGE_BUCKET, LAMBDA_FUNCTION, DYNAMO_DB_TABLE
    )
