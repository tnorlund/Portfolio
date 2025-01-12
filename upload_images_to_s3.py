import os
import uuid
import boto3
from botocore.exceptions import ClientError

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
RAW_IMAGE_BUCKET = os.getenv("RAW_IMAGE_BUCKET")


def upload_png_files_with_uuid(directory, bucket_name):
    """
    Uploads all .png files from the specified directory to the given S3 bucket
    using UUIDs as the object names.
    """
    s3 = boto3.client("s3")

    for file_name in os.listdir(directory):
        # Only process .png files
        if file_name.lower().endswith(".png"):
            local_file_path = os.path.join(directory, file_name)

            # Generate a UUID-based S3 object name
            s3_object_name = f"raw/{uuid.uuid4()}.png"

            # Check if the object name already exists
            try:
                s3.head_object(Bucket=bucket_name, Key=s3_object_name)
                # If no exception is thrown, the object exists
                raise FileExistsError(
                    f"The object '{s3_object_name}' already exists in bucket '{bucket_name}'. Aborting."
                )
            except ClientError as e:
                # If we get a 404 error, the object does not exist, and we can upload.
                if e.response["Error"]["Code"] != "404":
                    # Some other error occurred; raise it.
                    raise e

            # Upload the file to S3 using the new UUID name
            s3.upload_file(local_file_path, bucket_name, s3_object_name)
            print(f"Uploaded {local_file_path} -> s3://{bucket_name}/{s3_object_name}")


if __name__ == "__main__":
    # Update these variables
    directory_to_upload = "pic"
    s3_bucket_name = RAW_IMAGE_BUCKET

    upload_png_files_with_uuid(directory_to_upload, s3_bucket_name)
