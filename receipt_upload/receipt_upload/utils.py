from hashlib import sha256
from io import BytesIO
from os.path import join
from pathlib import Path
from typing import List, Optional, Tuple

from boto3 import client
from PIL import Image as PIL_Image
from PIL.Image import Resampling

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import (
    Letter,
    Line,
    OCRJob,
    OCRRoutingDecision,
    ReceiptLetter,
    ReceiptLine,
    ReceiptWord,
    Word,
)


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


def download_image_from_s3(s3_bucket: str, s3_key: str, image_id: str) -> Path:
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


def upload_jpeg_to_s3(
    image: PIL_Image.Image, s3_bucket: str, s3_key: str
) -> None:
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


def upload_png_to_s3(
    image: PIL_Image.Image, s3_bucket: str, s3_key: str
) -> None:
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


def upload_webp_to_s3(
    image: PIL_Image.Image, s3_bucket: str, s3_key: str, quality: int = 85
) -> None:
    """
    Upload a WebP image to S3.
    """
    s3_client = client("s3")
    with BytesIO() as buffer:
        image.convert("RGB").save(
            buffer, format="WEBP", quality=quality, method=6
        )
        buffer.seek(0)
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=buffer.getvalue(),
            ContentType="image/webp",
        )


class AVIFError(Exception):
    """Custom exception for AVIF-related errors."""


def upload_avif_to_s3(
    image: PIL_Image.Image, s3_bucket: str, s3_key: str, quality: int = 85
) -> None:
    """
    Upload an AVIF image to S3.
    Note: Requires pillow-avif-plugin to be installed.
    """
    s3_client = client("s3")

    try:
        # Try to import and register the AVIF plugin
        try:
            # pylint: disable=import-outside-toplevel,unused-import
            import pillow_avif  # noqa: F401

            # The import should auto-register the plugin
        except ImportError as import_error:
            raise AVIFError(
                f"pillow-avif-plugin is not installed: {import_error}"
            ) from import_error

        # Check if AVIF is supported
        if ".avif" not in PIL_Image.registered_extensions():
            available_formats = list(PIL_Image.registered_extensions().keys())
            raise AVIFError(
                f"AVIF format is not supported. "
                f"Available formats: {available_formats}"
            )

        with BytesIO() as buffer:
            try:
                # Convert to RGB mode for AVIF (no transparency support)
                if image.mode not in ("RGB", "L"):
                    rgb_image = image.convert("RGB")
                else:
                    rgb_image = image

                # Use more compatible AVIF encoding parameters
                rgb_image.save(
                    buffer,
                    format="AVIF",
                    quality=quality,
                    # Safari AVIF compatibility: Force 8-bit 420 subset
                    speed=6,  # Balance encoding speed and compression
                    chroma_subsampling="4:2:0",  # Standard (420 subset)
                    range="limited",  # Better compatibility
                    codec="aom",  # Use AOM codec for compatibility
                    # Force 8-bit depth for Safari compatibility
                    bit_depth=8,  # Explicitly force 8-bit (Safari req)
                    # Avoid advanced features that may not be supported
                    alpha_premultiplied=False,
                    # Use baseline profile for maximum Safari compatibility
                    avif_options={
                        "advanced": 0,  # Disable advanced features
                        "autotiling": False,  # Disable auto-tiling
                        "tiling": "1x1",  # Use single tile
                        "min_quantizer": 0,  # Disable min quantizer limits
                        "max_quantizer": 63,  # Use standard max quantizer
                    },
                )
                buffer.seek(0)
                avif_data = buffer.getvalue()

                if len(avif_data) == 0:
                    raise AVIFError("AVIF encoding produced empty data")

            except (OSError, ValueError, TypeError) as save_error:
                # If advanced options fail, fall back to basic encoding
                try:
                    buffer.seek(0)
                    buffer.truncate()

                    # Basic AVIF encoding without advanced options
                    rgb_image.save(
                        buffer,
                        format="AVIF",
                        quality=quality,
                        speed=6,  # Keep speed for reasonable encoding time
                    )
                    buffer.seek(0)
                    avif_data = buffer.getvalue()

                    if len(avif_data) == 0:
                        raise AVIFError(
                            "Basic AVIF encoding also produced empty data"
                        ) from save_error

                except (OSError, ValueError, TypeError) as fallback_error:
                    raise AVIFError(
                        f"Both advanced and basic AVIF encoding failed. "
                        f"Advanced: {save_error}, Basic: {fallback_error}"
                    ) from fallback_error

            try:
                # Upload to S3
                s3_client.put_object(
                    Bucket=s3_bucket,
                    Key=s3_key,
                    Body=avif_data,
                    ContentType="image/avif",
                )
            except Exception as s3_error:
                raise AVIFError(
                    f"Failed to upload AVIF to S3: {s3_error}"
                ) from s3_error

    except AVIFError:
        raise
    except Exception as e:
        # Re-raise with more context
        raise AVIFError(f"AVIF upload failed for {s3_key}: {e}") from e


def generate_image_sizes(
    image: PIL_Image.Image,
    target_sizes: dict[str, int],
) -> dict[str, PIL_Image.Image]:
    """
    Generate multiple sizes of an image while maintaining aspect ratio.

    Args:
        image: Original PIL Image object
        target_sizes: Dict mapping size name to max dimension
                     (e.g., {"thumbnail": 300, "small": 600})

    Returns:
        Dictionary with size names as keys and resized PIL Images as values
    """
    sizes = {}

    for size_name, max_dimension in target_sizes.items():
        # Calculate dimensions maintaining aspect ratio
        ratio = min(max_dimension / image.width, max_dimension / image.height)

        # Only resize if the image is larger than target
        if ratio < 1:
            new_width = int(image.width * ratio)
            new_height = int(image.height * ratio)

            # Use high-quality Lanczos resampling
            resized = image.resize((new_width, new_height), Resampling.LANCZOS)
            sizes[size_name] = resized
        else:
            # If image is already smaller, use original
            sizes[size_name] = image

    return sizes


def upload_all_cdn_formats(
    image: PIL_Image.Image,
    s3_bucket: str,
    base_key: str,
    *,
    webp_quality: int = 85,
    avif_quality: int = 85,
    generate_thumbnails: bool = True,
) -> dict[str, Optional[str]]:
    """
    Upload an image in all CDN formats (JPEG, WebP, AVIF) to S3.
    Optionally generates multiple sizes for efficient loading.

    Args:
        image: PIL Image object
        s3_bucket: S3 bucket name
        base_key: Base S3 key without extension (e.g., "assets/image_id")
        webp_quality: WebP compression quality (1-100)
        avif_quality: AVIF compression quality (1-100)
        generate_thumbnails: Whether to generate multiple sizes

    Returns:
        Dictionary with format names as keys and S3 keys as values.
        For thumbnails, keys will be like "jpeg_thumbnail", "webp_small", etc.
    """
    keys: dict[str, Optional[str]] = {}

    # Define target sizes for responsive images
    # Sizes chosen based on frontend needs:
    # - thumbnail: for ImageStack (150px) and ReceiptStack (100px) displays
    # - small: for medium density displays and hover previews
    # - medium: for high density displays and larger previews
    # - full: original size for detailed viewing
    size_configs = {
        "thumbnail": 300,  # 2x the largest display size (150px)
        "small": 600,  # 4x display size for retina
        "medium": 1200,  # For larger previews
    }

    # Generate different sizes if requested
    if generate_thumbnails:
        sizes = generate_image_sizes(image, size_configs)
        sizes["full"] = image  # Include original
    else:
        sizes = {"full": image}

    # Upload each size in all formats
    for size_name, sized_image in sizes.items():
        # Determine key suffix
        if size_name == "full":
            size_key = base_key
        else:
            size_key = f"{base_key}_{size_name}"

        # Upload JPEG version
        jpeg_key = f"{size_key}.jpg"
        upload_jpeg_to_s3(sized_image, s3_bucket, jpeg_key)
        keys[f"jpeg_{size_name}"] = jpeg_key

        # Upload WebP version
        webp_key = f"{size_key}.webp"
        upload_webp_to_s3(sized_image, s3_bucket, webp_key, webp_quality)
        keys[f"webp_{size_name}"] = webp_key

        # Try to upload AVIF version
        try:
            avif_key = f"{size_key}.avif"
            upload_avif_to_s3(sized_image, s3_bucket, avif_key, avif_quality)
            keys[f"avif_{size_name}"] = avif_key
        except AVIFError as e:
            print(f"Warning: Could not upload AVIF format for {size_key}: {e}")
            keys[f"avif_{size_name}"] = None

    # For backward compatibility, also include the original keys
    keys["jpeg"] = keys.get("jpeg_full")
    keys["webp"] = keys.get("webp_full")
    keys["avif"] = keys.get("avif_full")

    return keys


def upload_file_to_s3(file_path: Path, s3_bucket: str, s3_key: str) -> None:
    """
    Upload a file to S3.
    """
    s3_client = client("s3")
    s3_client.upload_file(str(file_path), s3_bucket, s3_key)


def calculate_sha256_from_bytes(data: bytes) -> str:
    """Calculate SHA256 hash from bytes."""
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
    return dynamo_client.get_ocr_job(image_id=image_id, job_id=job_id)


def get_ocr_routing_decision(
    dynamo_table_name: str, image_id: str, job_id: str
) -> OCRRoutingDecision:
    """
    Get an OCR routing decision from the DynamoDB table.
    """
    dynamo_client = DynamoClient(dynamo_table_name)
    return dynamo_client.get_ocr_routing_decision(
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

    This function takes OCR results from an image and associates them with a
    specific receipt ID by creating receipt-specific versions of the Line,
    Word, and Letter objects.

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
