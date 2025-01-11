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

