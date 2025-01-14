import os
import tempfile
from uuid import uuid4
import subprocess
import boto3
import json
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import cv2


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
    load_dotenv(dotenv_path)
    return os.getenv("RAW_IMAGE_BUCKET")


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


def upload_files_with_uuid_in_batches(directory, bucket_name, batch_size=10):
    """
    Uploads png and json files in the given directory to the specified S3 bucket with a UUID-based
    object name, but processes them in batches of size `batch_size`.
    """
    s3 = boto3.client("s3")
    # Get the full path of all PNG files in the directory
    # print(os.listdir(directory))
    all_png_files = [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".png")]

    # Split all_png_files into batches
    for batch_index, batch in enumerate(chunked(all_png_files, batch_size), start=1):
        print(f"\nProcessing batch #{batch_index} with up to {batch_size} files...")
        
        # Make a temporary working directory
        temp_dir = os.path.join(directory, "temp")
        os.mkdir(temp_dir)
        for file_name in batch:
            os.system(f"cp {file_name} {temp_dir}")
        
        for file in os.listdir(temp_dir):
            os.rename(
                os.path.join(temp_dir, file),
                os.path.join(temp_dir, f"{uuid4()}.png")
            )
        
        files_in_temp = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir)]
        # run_swift_script(temp_dir, files_in_temp)
        if not run_swift_script(temp_dir, files_in_temp):
            raise RuntimeError("Error running Swift script")
        
        # batch upload to S3

        # Upload all files in the temporary directory to S3
        for file in os.listdir(temp_dir):
            print(f"Uploading {file} to S3...")
            s3.upload_file(
                os.path.join(temp_dir, file),
                bucket_name,
                f"test/{file}"
            )

        # Delete the temporary working directory
        for file in os.listdir(temp_dir):
            os.remove(os.path.join(temp_dir, file))
        os.rmdir(temp_dir)

        print(f"Finished batch #{batch_index}.")


if __name__ == "__main__":
    RAW_IMAGE_BUCKET = load_env()
    # Update these variables
    directory_to_upload = "/Users/tnorlund/Example_to_delete"

    upload_files_with_uuid_in_batches(directory_to_upload, RAW_IMAGE_BUCKET)
