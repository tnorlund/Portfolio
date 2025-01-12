import os
import subprocess
import json
from time import sleep
import cv2
from datetime import datetime
from dotenv import load_dotenv
import boto3

from dynamo import DynamoClient
from dynamo.utils import update_all_from_s3

# Load environment variables from .env file
load_dotenv(".env")
# Use the environment variables
S3_BUCKET = os.getenv("RAW_IMAGE_BUCKET")
DYNAMO_DB_TABLE = os.getenv("DYNAMO_DB_TABLE")

# Update all the data in DynamoDB from the OCR data
update_all_from_s3(S3_BUCKET, DYNAMO_DB_TABLE, dir="raw")
# raw/755b0ef1-26f3-49b4-be03-2167de443b6e.png
# raw/315e61e1-eb11-4e5d-a934-0ff1d0868714.png
