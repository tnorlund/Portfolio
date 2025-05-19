from boto3 import client
from os.path import join
from pathlib import Path
from PIL import Image as PIL_Image
from io import BytesIO
from hashlib import sha256
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    OCRJob,
    OCRRoutingDecision,
    Line,
    Word,
    Letter,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
)
from typing import List, Tuple


def download_file_from_s3(s3_bucket: str, s3_key: str, temp_dir: Path) -> Path:
    """
    Download a file from S3 and save it to a temporary directory.
    """
    s3_client = client("s3")
    # Get the object data from S3
    response = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
    file_data = response["Body"].read()

    # Create the full directory path if it doesn't exist
    file_path = temp_dir / Path(s3_key)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Save to file
    with open(file_path, "wb") as f:
        f.write(file_data)

    return file_path


def download_image_from_s3(
    s3_bucket: str, s3_key: str, temp_dir: Path, image_id: str
) -> Path:
    """
    Download an image from S3 and save it to a temporary directory.
    """
    s3_client = client("s3")
    # Get the object data from S3
    response = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
    image_data = response["Body"].read()

    # Save to a temporary file
    image_path = join("/tmp", f"{image_id}.png")
    with open(image_path, "wb") as f:
        f.write(image_data)

    return Path(image_path)


def upload_jpeg_to_s3(image: PIL_Image, s3_bucket: str, s3_key: str) -> None:
    """
    Upload an image to S3.
    """
    s3_client = client("s3")
    with BytesIO() as buffer:
        image.convert("RGB").save(buffer, format="JPEG", quality=85)
        buffer.seek(0)
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=buffer.getvalue(),
            ContentType="image/jpeg",
        )


def upload_png_to_s3(image: PIL_Image, s3_bucket: str, s3_key: str) -> None:
    """
    Upload a PNG image to S3.
    """
    s3_client = client("s3")
    with BytesIO() as buffer:
        image.save(buffer, format="PNG")
        buffer.seek(0)
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=buffer.getvalue(),
            ContentType="image/png",
        )


def upload_file_to_s3(file_path: Path, s3_bucket: str, s3_key: str) -> None:
    """
    Upload a file to S3.
    """
    s3_client = client("s3")
    s3_client.upload_file(str(file_path), s3_bucket, s3_key)


def calculate_sha256_from_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def send_message_to_sqs(queue_url: str, message_body: str) -> None:
    """
    Send a message to an SQS queue.
    """
    sqs_client = client("sqs")
    sqs_client.send_message(QueueUrl=queue_url, MessageBody=message_body)


def get_ocr_job(dynamo_table_name: str, image_id: str, job_id: str) -> OCRJob:
    """
    Get an OCR job from the DynamoDB table.
    """
    dynamo_client = DynamoClient(dynamo_table_name)
    return dynamo_client.getOCRJob(image_id=image_id, job_id=job_id)


def get_ocr_routing_decision(
    dynamo_table_name: str, image_id: str, job_id: str
) -> OCRRoutingDecision:
    """
    Get an OCR routing decision from the DynamoDB table.
    """
    dynamo_client = DynamoClient(dynamo_table_name)
    return dynamo_client.getOCRRoutingDecision(
        image_id=image_id, job_id=job_id
    )


def image_ocr_to_receipt_ocr(
    lines: List[Line],
    words: List[Word],
    letters: List[Letter],
    receipt_id: int,
) -> Tuple[List[ReceiptLine], List[ReceiptWord], List[ReceiptLetter]]:
    """
    Convert image OCR to receipt OCR.

    This function takes OCR results from an image and associates them with a specific
    receipt ID by creating receipt-specific versions of the Line, Word, and Letter objects.

    Args:
        lines: List of Line objects from image OCR
        words: List of Word objects from image OCR
        letters: List of Letter objects from image OCR
        receipt_id: ID of the receipt these OCR results belong to

    Returns:
        Tuple containing:
        - List of ReceiptLine objects
        - List of ReceiptWord objects
        - List of ReceiptLetter objects
    """
    receipt_lines = []
    receipt_words = []
    receipt_letters = []

    for line in lines:
        receipt_line = ReceiptLine(
            image_id=line.image_id,
            line_id=line.line_id,
            text=line.text,
            bounding_box=line.bounding_box,
            top_right=line.top_right,
            top_left=line.top_left,
            bottom_right=line.bottom_right,
            bottom_left=line.bottom_left,
            angle_degrees=line.angle_degrees,
            angle_radians=line.angle_radians,
            confidence=line.confidence,
            receipt_id=receipt_id,
        )
        receipt_lines.append(receipt_line)

    for word in words:
        receipt_word = ReceiptWord(
            image_id=word.image_id,
            line_id=word.line_id,
            word_id=word.word_id,
            text=word.text,
            bounding_box=word.bounding_box,
            top_right=word.top_right,
            top_left=word.top_left,
            bottom_right=word.bottom_right,
            bottom_left=word.bottom_left,
            angle_degrees=word.angle_degrees,
            angle_radians=word.angle_radians,
            confidence=word.confidence,
            receipt_id=receipt_id,
        )
        receipt_words.append(receipt_word)

    for letter in letters:
        receipt_letter = ReceiptLetter(
            image_id=letter.image_id,
            line_id=letter.line_id,
            word_id=letter.word_id,
            letter_id=letter.letter_id,
            text=letter.text,
            bounding_box=letter.bounding_box,
            top_right=letter.top_right,
            top_left=letter.top_left,
            bottom_right=letter.bottom_right,
            bottom_left=letter.bottom_left,
            angle_degrees=letter.angle_degrees,
            angle_radians=letter.angle_radians,
            confidence=letter.confidence,
            receipt_id=receipt_id,
        )
        receipt_letters.append(receipt_letter)

    return receipt_lines, receipt_words, receipt_letters
