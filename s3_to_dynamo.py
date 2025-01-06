import os
import subprocess
import json
from time import sleep
import cv2
from datetime import datetime
from dotenv import load_dotenv
import boto3

from dynamo import DynamoClient, Image, Line, Word, Letter, ScaledImage
from utils import encode_image_below_size, get_max_index_in_images, process_ocr_dict
# Load environment variables from .env file
load_dotenv()
# Use the environment variables
S3_BUCKET = os.getenv("RAW_IMAGE_BUCKET")
DYNAMO_DB_TABLE = os.getenv("DYNAMO_DB_TABLE")

# Get the images from DynamoDB
dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
images_in_dynamodb = dynamo_client.listImages()

# Use boto to list the objects in the bucket under the "raw/" directory
s3 = boto3.client("s3")
response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="raw/")
images_in_s3 = response["Contents"]

# Compare the S3 locations in the DynamoDB table with the S3 locations in the bucket
s3_keys_in_dynamodb = [image.s3_key for image in images_in_dynamodb]
s3_keys_in_s3 = [image["Key"] for image in images_in_s3]
# Get the keys that are in the bucket but not in the DynamoDB table
missing_images = set(s3_keys_in_s3) - set(s3_keys_in_dynamodb)

print(f"Found {len(missing_images)} missing images in the bucket")

for missing_image in missing_images:
    print(f"Processing missing image: {missing_image} at {get_max_index_in_images(dynamo_client)}")
    temporary_file_path = f"/tmp/{os.path.basename(missing_image)}"
    temporary_json_path = (
        f"/tmp/{os.path.basename(missing_image.replace('.png', ''))}.json"
    )
    # Download the image from the bucket
    response = s3.get_object(Bucket=S3_BUCKET, Key=missing_image)
    image_data = response["Body"].read()
    with open(temporary_file_path, "wb") as f:
        f.write(image_data)

    # Run the swift script to process the image OCR data
    try:
        subprocess.run(
            ["swift", "OCRSwift.swift", temporary_file_path, temporary_json_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as e:
        print(f"Error running swift script: {e}")
        continue

    # Read the JSON file and add the image to the DynamoDB table
    with open(temporary_json_path, "r") as json_file:
        ocr_data = json.load(json_file)
    
    # Read the image using OpenCV
    image_cv = cv2.imread(temporary_file_path)

    # Create DynamoDB objects from the OCR data
    image = Image(
        get_max_index_in_images(dynamo_client),
        image_cv.shape[1],
        image_cv.shape[0],
        datetime.now().isoformat(),
        S3_BUCKET,
        missing_image,
    )
    try:
        lines, words, letters = process_ocr_dict(ocr_data, image.id)
    except Exception as e:
        # Dump the OCR data to a file for debugging
        with open(f"ocr_data_{image.id}.json", "w") as f:
            json.dump(ocr_data, f, indent=4)
        print(f"Error processing OCR data: {e}")
        continue
    response = encode_image_below_size(image_cv)
    if response == -1:
        print(f"Could not compress the image below the desired size: {image.id}")
        continue
        # raise ValueError("Could not compress the image below the desired size.")
    encoded_str, quality = response
    scaled_image = ScaledImage(
        image.id,
        datetime.now().isoformat(),
        encoded_str,
        quality,
    )
    print(f"Adding image to DynamoDB: {image.id}")
    # Add the image to the DynamoDB table
    dynamo_client.addImage(image)
    dynamo_client.addScaledImage(scaled_image)
    dynamo_client.addLines(lines)
    dynamo_client.addWords(words)
    dynamo_client.addLetters(letters)

    # remove the temporary files
    os.remove(temporary_file_path)
    os.remove(temporary_json_path)
    sleep(1)

