from boto3 import client
from os.path import join
from pathlib import Path
from PIL import Image as PIL_Image
from io import BytesIO
from hashlib import sha256


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
