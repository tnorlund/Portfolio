import os
import subprocess
import json
from time import sleep
import cv2
from datetime import datetime
from dotenv import load_dotenv
import boto3

from dynamo import DynamoClient, Image, Line, Word, Letter, ScaledImage, itemToImage
from utils import (
    encode_image_below_size,
    get_max_index_in_images,
    process_ocr_dict,
    calculate_sha256,
)
import tempfile

# Load environment variables from .env file
load_dotenv()
# Use the environment variables
S3_BUCKET = os.getenv("RAW_IMAGE_BUCKET")
DYNAMO_DB_TABLE = os.getenv("DYNAMO_DB_TABLE")


# Get the images from DynamoDB
dynamo_client = DynamoClient(DYNAMO_DB_TABLE)

# scaled_images = dynamo_client.listScaledImages()
# print(scaled_images)
# # print(scaled_images)
images_in_dynamodb, lek = dynamo_client.listImages()

# print(images_in_dynamodb)
# Get all images that don't have a SHA256 hash
images_without_hash = [image for image in images_in_dynamodb if not image.sha256]
print(f"Found {len(images_without_hash)} images without a SHA256 hash")

# # Calculate the SHA256 hash for each image
for image in images_without_hash:
    # Use a temporary file to download from S3
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        print(f"Downloading image: {image.s3_key}")
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=S3_BUCKET, Key=image.s3_key)
        image_data = response["Body"].read()
        temp_file.write(image_data)
        temp_file.flush()
        sha256_hash = calculate_sha256(temp_file.name)
        print(f"SHA256 hash: {sha256_hash}")
        image.sha256 = sha256_hash
        dynamo_client.updateImage(image)
        print(f"Updated image: {image.id} with SHA256 hash: {sha256_hash}")
