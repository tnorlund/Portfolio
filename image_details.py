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


dynamo_client = DynamoClient(DYNAMO_DB_TABLE)
image, lines, words, letters, scaled_images = dynamo_client.getImageDetails(20)
dump = {
    "image": dict(image),
    "lines": [dict(line) for line in lines],
    "words": [dict(word) for word in words],
    "letters": [dict(letter) for letter in letters],
    "scaled_images": [dict(scaled_image) for scaled_image in scaled_images],
}
print(json.dumps(dump, indent=2))