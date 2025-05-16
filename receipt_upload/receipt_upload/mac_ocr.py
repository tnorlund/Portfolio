import os
import argparse
import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import OCRRoutingDecision
from receipt_upload.pulumi import load_env
from receipt_upload.ocr import apple_vision_ocr_job
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from datetime import datetime, timezone
from receipt_dynamo.constants import OCRStatus
from receipt_upload.utils import download_image_from_s3, upload_file_to_s3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=str, default="dev")
    args = parser.parse_args()
    env = args.env
    pulumi_outputs = load_env(env)
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

    # image_details is a list of tuples, where each tuple contains the image_id and the path to the image
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
            ocr_job = dynamo_client.getOCRJob(image_id=image_id, job_id=job_id)
            image_s3_key = ocr_job.s3_key
            image_s3_bucket = ocr_job.s3_bucket

            # Download the image from the S3 bucket
            image_path = download_image_from_s3(
                image_s3_bucket, image_s3_key, temp_dir, image_id
            )
            image_details.append((image_id, image_path))

        # Run the OCR
        ocr_routing_decisions: list[OCRRoutingDecision] = []
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
                ocr_json_file, image_s3_bucket, ocr_json_file_s3_key
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
                f"Adding OCR routing decision for\nimage {image_id}\njob {job_id}\n"
                f"s3_bucket {image_s3_bucket}\n"
                f"s3_key {ocr_json_file_s3_key}"
            )
            dynamo_client.addOCRRoutingDecision(
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
    sqs_client.delete_message_batch(
        QueueUrl=sqs_queue_url,
        Entries=[
            {"Id": m["MessageId"], "ReceiptHandle": m["ReceiptHandle"]}
            for (m, _, _) in message_contexts
        ],
    )


if __name__ == "__main__":
    main()
