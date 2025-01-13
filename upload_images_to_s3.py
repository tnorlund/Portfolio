import os
from uuid import uuid4
import subprocess
import boto3
import json
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import cv2


def load_env():
    """Loads the .env file in the current directory and returns the value of the RAW_IMAGE_BUCKET environment variable.

    Returns:
        str: The value of the RAW_IMAGE_BUCKET environment variable
    """
    dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(dotenv_path)
    return os.getenv("RAW_IMAGE_BUCKET")

def run_swift_script(full_path_to_file, full_path_to_json):
    try:
        subprocess.run(
            ["swift", "OCRSwift.swift", full_path_to_file, full_path_to_json],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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


def upload_files_with_uuid(directory, bucket_name):
    """
    Uploads the png and json files in the given directory to the specified S3 bucket with a UUID-based object name.
    """
    s3 = boto3.client("s3")

    for file_name in [f for f in os.listdir(directory) if f.lower().endswith(".png")]:
        # Get full path
        full_path_to_file = os.path.join(directory, file_name)
        uuid = str(uuid4())
        full_path_to_json = os.path.join(os.getcwd(), uuid + ".json")

        if not run_swift_script(full_path_to_file, full_path_to_json):
            continue
        original_angle_degrees = calc_avg_angle_degrees(full_path_to_json)
        # Rotate the image 180 degrees if the average angle is greater than 90 degrees or less than -90 degrees
        if original_angle_degrees > 90 or original_angle_degrees < -90:
            print(f"Could be upside down {original_angle_degrees}: Rotating image 180 degrees")
            img = cv2.imread(full_path_to_file)
            img = cv2.rotate(img, cv2.ROTATE_180)
            cv2.imwrite(full_path_to_file, img)
            run_swift_script(full_path_to_file, full_path_to_json)
            new_angle_degrees = calc_avg_angle_degrees(full_path_to_json)
            print(f"New average angle: {new_angle_degrees}")

        s3_image_object_name = f"test/{uuid}.png"
        s3_json_object_name = f"test/{uuid}.json"

        check_if_already_in_s3(bucket_name, s3_image_object_name)
        check_if_already_in_s3(bucket_name, s3_json_object_name)

        # Upload the file to S3 using the new UUID name
        s3.upload_file(full_path_to_file, bucket_name, s3_image_object_name)
        print(
            f"Uploaded {full_path_to_file} -> s3://{bucket_name}/{s3_image_object_name}"
        )
        s3.upload_file(full_path_to_json, bucket_name, s3_json_object_name)
        print(
            f"Uploaded {full_path_to_json} -> s3://{bucket_name}/{s3_json_object_name}"
        )

        # Delete the temporary JSON file
        os.remove(full_path_to_json)


if __name__ == "__main__":
    RAW_IMAGE_BUCKET = load_env()
    # Update these variables
    directory_to_upload = "/Users/tnorlund/Receipt_Jan_11_2025"

    upload_files_with_uuid(directory_to_upload, RAW_IMAGE_BUCKET)
