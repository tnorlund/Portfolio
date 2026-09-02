import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
from receipt_upload.ocr import apple_vision_ocr_job
from receipt_upload.pulumi import load_env
from receipt_upload.utils import download_image_from_s3, upload_file_to_s3

from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import OCRRoutingDecision


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=str, default="dev")
    args = parser.parse_args()
    env = args.env
    pulumi_outputs = load_env(env)
    # raise an error if the pulumi_outputs is empty
    if not pulumi_outputs:
        raise ValueError("Pulumi outputs are empty")
    sqs_queue_url = pulumi_outputs["ocr_job_queue_url"]
    dynamo_table_name = pulumi_outputs["dynamodb_table_name"]
    ocr_results_queue_url = pulumi_outputs["ocr_results_queue_url"]

    dynamo_client = DynamoClient(dynamo_table_name)
    # Get the image from the queue
    sqs_client = boto3.client("sqs")
    response = sqs_client.receive_message(
        QueueUrl=sqs_queue_url,
        MaxNumberOfMessages=10,
        VisibilityTimeout=60,
    )
    if "Messages" not in response:
        print("No messages in the queue")
        return

    # image_details is a list of tuples, where each tuple contains the image_id
    # and the path to the image
    image_details: list[tuple[str, Path]] = []

    message_contexts = []

    print(f"Received {len(response['Messages'])} messages")
    with TemporaryDirectory() as temp_dir:
        for message in response["Messages"]:
            # Get the job_id and image_id from the message
            message_body = json.loads(message["Body"])
            job_id = message_body["job_id"]
            image_id = message_body["image_id"]

            message_contexts.append((message, image_id, job_id))

            # Grab the OCR Job from the DynamoDB table
            ocr_job = dynamo_client.get_ocr_job(
                image_id=image_id, job_id=job_id
            )
            image_s3_key = ocr_job.s3_key
            image_s3_bucket = ocr_job.s3_bucket

            # Download the image from the S3 bucket with a unique name per job
            # to avoid filename collisions when multiple receipts for the same
            # image are processed concurrently.
            image_path = download_image_from_s3(
                image_s3_bucket,
                image_s3_key,
                image_id,
                dest_dir=Path(temp_dir),
                unique_suffix=job_id,
            )
            image_details.append((image_id, image_path))

        # Run the OCR
        ocr_results = apple_vision_ocr_job(
            [path for (_, path) in image_details], Path(temp_dir)
        )

        # Now zip with original image_ids and job_ids
        for ocr_json_file, (message, image_id, job_id) in zip(
            ocr_results, message_contexts
        ):

            ocr_json_file_name = ocr_json_file.name
            ocr_json_file_s3_key = f"ocr_results/{ocr_json_file_name}"
            upload_file_to_s3(
                file_path=ocr_json_file,
                s3_bucket=image_s3_bucket,
                s3_key=ocr_json_file_s3_key,
            )
            dynamo_client.add_ocr_routing_decision(
                OCRRoutingDecision(
                    image_id=image_id,
                    job_id=job_id,
                    s3_bucket=image_s3_bucket,
                    s3_key=ocr_json_file_s3_key,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    receipt_count=0,
                    status=OCRStatus.PENDING,
                )
            )
            sqs_client.send_message(
                QueueUrl=ocr_results_queue_url,
                MessageBody=json.dumps(
                    {
                        "image_id": image_id,
                        "job_id": job_id,
                        "s3_key": ocr_json_file_s3_key,
                        "s3_bucket": image_s3_bucket,
                    }
                ),
            )
            print(
                f"Adding OCR routing decision for\nimage {image_id}\n"
                f"job {job_id}\ns3_bucket {image_s3_bucket}\n"
                f"s3_key {ocr_json_file_s3_key}"
            )
            ocr_job = dynamo_client.get_ocr_job(
                image_id=image_id, job_id=job_id
            )
            ocr_job.updated_at = datetime.now(timezone.utc)
            ocr_job.status = OCRStatus.COMPLETED.value
            dynamo_client.update_ocr_job(ocr_job)
    sqs_client.delete_message_batch(
        QueueUrl=sqs_queue_url,
        Entries=[
            {"Id": m["MessageId"], "ReceiptHandle": m["ReceiptHandle"]}
            for (m, _, _) in message_contexts
        ],
    )


if __name__ == "__main__":
    main()
