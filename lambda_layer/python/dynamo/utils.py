from typing import Tuple
from dynamo.entities.letter import Letter
from dynamo.entities.line import Line
from dynamo.entities.word import Word
from dynamo import DynamoClient
import boto3
import tempfile
import cv2
import subprocess
import json

def nextImageIndex(client: DynamoClient) -> int:
    """
    Get the maximum index in the list of images.
    """
    images, _ = client.listImages()
    if images == []:
        return 1
    image_indexes = [image.id for image in images]
    image_indexes.sort()
    # Find where the indexes are not consecutive
    for i, index in enumerate(image_indexes):
        if i + 1 != index:
            return i + 1
    return len(image_indexes) + 1

def update_all_from_s3(s3_bucket: str, dynamo_table_name: str, dir: str = "raw") -> None:
    """
    Update all the data in DynamoDB from the OCR data.

    Args:
        s3_bucket (str): The S3 bucket name
        dir (str): The directory in the bucket
    """
    # List all PNG from bucket
    s3_client = boto3.client("s3")
    response = s3_client.list_objects_v2(Bucket=s3_bucket, Prefix=dir)
    if "Contents" not in response:
        print(f"No images found in the bucket {s3_bucket} under the directory {dir}")
        return
    images_in_s3 = response["Contents"]

    # Get all images from DynamoDB
    dynamo_client = DynamoClient(dynamo_table_name)
    images_in_dynamodb, _ = dynamo_client.listImages()

    # Compare the S3 locations in the DynamoDB table with the S3 locations in the bucket
    s3_keys_in_dynamodb = [image.s3_key for image in images_in_dynamodb]
    s3_keys_in_s3 = [image["Key"] for image in images_in_s3]
    # Get the keys that are in the bucket but not in the DynamoDB table
    missing_images = set(s3_keys_in_s3) - set(s3_keys_in_dynamodb)
    for missing_image_key in missing_images:
        create_dynamo_entities_from_s3(s3_client, s3_bucket, dynamo_client, [missing_image_key])

def create_dynamo_entities_from_s3(s3_client: boto3.client, s3_bucket: str, dynamo_client: DynamoClient, keys: list[str]) -> None:
    """
    Create Dynamo entities from S3 data.

    Args:
        s3_client (boto3.client): The S3 client
        dynamo_client (DynamoClient): The DynamoDB client
        keys (list[str]): The list of keys to process
    """
    for key in keys:
        print(f"Processing missing image: {key} at {nextImageIndex(dynamo_client)}")
        with tempfile.TemporaryDirectory() as temp_dir:
            temporary_directory = temp_dir
            # Download the image from the bucket
            response = s3_client.get_object(Bucket=s3_bucket, Key=key)
            image_path = f"{temporary_directory}/{key.split('/')[-1]}"
            json_path = f"{temporary_directory}/{key.split('/')[-1].replace('.png', '')}.json"
            rotate_image_path = f"{temporary_directory}/{key.split('/')[-1].replace('.png', '')}_rotated.png"
            rotate_json_path = f"{temporary_directory}/{key.split('/')[-1].replace('.png', '')}_rotated.json"
            with open(image_path, 'wb') as file:
                file.write(response['Body'].read())
            # Run the swift script to process the image OCR data
            try:
                subprocess.run(
                    ["swift", "OCRSwift.swift", image_path, json_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError as e:
                print(f"Error running swift script: {e}")
                continue
            # Read the JSON file and add the image to the DynamoDB table
            with open(json_path, "r") as json_file:
                ocr_data = json.load(json_file)
            lines_no_rotate, words_no_rotate, letters_no_rotate = process_ocr_dict(ocr_data, nextImageIndex(dynamo_client))
            # Rotate the image
            image_cv = cv2.imread(image_path)
            image_cv = cv2.rotate(image_cv, cv2.ROTATE_180)
            cv2.imwrite(rotate_image_path, image_cv)
            # Run the swift script to process the rotated image OCR data
            try:
                subprocess.run(
                    ["swift", "OCRSwift.swift", rotate_image_path, rotate_json_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError as e:
                print(f"Error running swift script: {e}")
                continue
            # Read the JSON file and add the image to the DynamoDB table
            with open(rotate_json_path, "r") as json_file:
                ocr_data = json.load(json_file)
            lines_rotate, words_rotate, letters_rotate = process_ocr_dict(ocr_data, nextImageIndex(dynamo_client))
            # Compare the angles of the lines. Get the one that is more horizontal
            avg_angle_no_rotate = sum([line.angleRadians for line in lines_no_rotate]) / len(lines_no_rotate)
            avg_angle_rotate = sum([line.angleRadians for line in lines_rotate]) / len(lines_rotate)
            if abs(avg_angle_no_rotate) < abs(avg_angle_rotate):
                print("Using non-rotated data")
                lines = lines_no_rotate
                words = words_no_rotate
                letters = letters_no_rotate
            else:
                print("Using rotated data")
                lines = lines_rotate
                words = words_rotate
                letters = letters_rotate
        


def process_ocr_dict(ocr_data: dict, image_id: int) -> Tuple[list, list, list]:
    """
    Process the OCR data and return lists of lines, words, and letters.
    """
    lines = []
    words = []
    letters = []
    for line_id, line_data in enumerate(ocr_data["lines"]):
        line_id = line_id + 1
        line_obj = Line(
            image_id=image_id,
            id=line_id,
            text=line_data["text"],
            boundingBox=line_data["boundingBox"],
            topRight=line_data["topRight"],
            topLeft=line_data["topLeft"],
            bottomRight=line_data["bottomRight"],
            bottomLeft=line_data["bottomLeft"],
            angleDegrees=line_data["angleDegrees"],
            angleRadians=line_data["angleRadians"],
            confidence=line_data["confidence"],
        )
        lines.append(line_obj)
        for word_id, word_data in enumerate(line_data["words"]):
            word_id = word_id + 1
            word_obj = Word(
                image_id=image_id,
                line_id=line_id,
                id=word_id,
                text=word_data["text"],
                boundingBox=word_data["boundingBox"],
                topRight=word_data["topRight"],
                topLeft=word_data["topLeft"],
                bottomRight=word_data["bottomRight"],
                bottomLeft=word_data["bottomLeft"],
                angleDegrees=word_data["angleDegrees"],
                angleRadians=word_data["angleRadians"],
                confidence=word_data["confidence"],
            )
            words.append(word_obj)
            for letter_id, letter_data in enumerate(word_data["letters"]):
                letter_id = letter_id + 1
                letter_obj = Letter(
                    image_id=image_id,
                    line_id=line_id,
                    word_id=word_id,
                    id=letter_id,
                    text=letter_data["text"],
                    boundingBox=letter_data["boundingBox"],
                    topRight=letter_data["topRight"],
                    topLeft=letter_data["topLeft"],
                    bottomRight=letter_data["bottomRight"],
                    bottomLeft=letter_data["bottomLeft"],
                    angleDegrees=letter_data["angleDegrees"],
                    angleRadians=letter_data["angleRadians"],
                    confidence=letter_data["confidence"],
                )
                letters.append(letter_obj)
    return lines, words, letters