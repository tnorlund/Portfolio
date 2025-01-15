import os
import tempfile
from uuid import uuid4
import subprocess
import boto3
import json
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from dynamo import DynamoClient


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
    images, _ = client.listImages()

    # If there are no images, just start from 1
    if not images:
        return list(range(1, number_images + 1))

    # Extract existing IDs and sort them
    existing_ids = sorted(images.keys())  # or sorted(list(images.keys()))

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


def chunked(iterable, n):
    """Yield successive n-sized chunks from an iterable."""
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]


def load_env():
    """Loads the .env file in the current directory and returns the value of the RAW_IMAGE_BUCKET environment variable.

    Returns:
        str: The value of the RAW_IMAGE_BUCKET environment variable
    """
    dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    # Clear the environment variables before loading the .env file
    os.environ.pop("RAW_IMAGE_BUCKET", None)
    os.environ.pop("LAMBDA_FUNCTION", None)
    os.environ.pop("DYNAMO_DB_TABLE", None)
    load_dotenv(dotenv_path)
    return (
        os.getenv("RAW_IMAGE_BUCKET"),
        os.getenv("LAMBDA_FUNCTION"),
        os.getenv("DYNAMO_DB_TABLE"),
    )


def run_swift_script(output_directory, list_of_image_paths) -> bool:
    try:
        swift_args = ["swift", "OCRSwift.swift", output_directory] + list_of_image_paths
        subprocess.run(swift_args, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running swift script: {e}")
        return False
    return True


def calc_avg_angle_degrees(json_path) -> float:
    with open(json_path, "r") as json_file:
        ocr_data = json.load(json_file)
        total_angle_degrees = 0
        for line in ocr_data["lines"]:
            total_angle_degrees += line["angle_degrees"]
        return total_angle_degrees / len(ocr_data["lines"])


def check_if_already_in_s3(bucket_name, s3_image_object_name):
    s3 = boto3.client("s3")
    try:
        s3.head_object(Bucket=bucket_name, Key=s3_image_object_name)
        # If no exception is thrown, the object exists
        raise FileExistsError(
            f"The object '{s3_image_object_name}' already exists in bucket '{bucket_name}'. Aborting."
        )
    except ClientError as e:
        # If we get a 404 error, the object does not exist, and we can upload.
        if e.response["Error"]["Code"] != "404":
            # Some other error occurred; raise it.
            raise e


def upload_files_with_uuid_in_batches(
    directory,
    bucket_name,
    lambda_function,
    dynamodb_table_name,
    path="test/",
    batch_size=10,
):
    """
    Uploads png and json files in the given directory to the specified S3 bucket with a UUID-based
    object name, but processes them in batches of size `batch_size`.
    """
    s3 = boto3.client("s3")
    # Get the full path of all PNG files in the directory
    all_png_files = [
        os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".png")
    ][:5]
    image_indexes = get_image_indexes(
        DynamoClient(dynamodb_table_name), len(all_png_files)
    )

    # Split all_png_files into batches
    for batch_index, batch in enumerate(chunked(all_png_files, batch_size), start=1):
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
            print(f"Uploading {file:<41} to S3 -> {path}{file}")
            s3.upload_file(os.path.join(temp_dir, file), bucket_name, f"{path}{file}")

        uuids = list(set([file.split(".")[0] for file in os.listdir(temp_dir)]))

        # Delete the temporary working directory
        for file in os.listdir(temp_dir):
            os.remove(os.path.join(temp_dir, file))
        os.rmdir(temp_dir)

        # invoke Lambda function
        lambda_client = boto3.client("lambda")
        for index, uuid in enumerate(uuids):
            image_id = image_indexes[(batch_index - 1) * batch_size + index]
            print(
                f"Invoking Lambda function for UUID {uuid} and Image ID {image_id}..."
            )
            response = lambda_client.invoke(
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
    RAW_IMAGE_BUCKET, LAMBDA_FUNCTION, DYNAMO_DB_TABLE = load_env()
    # Update these variables
    directory_to_upload = "/Users/tnorlund/Example_to_delete"

    upload_files_with_uuid_in_batches(
        directory_to_upload, RAW_IMAGE_BUCKET, LAMBDA_FUNCTION, DYNAMO_DB_TABLE
    )
