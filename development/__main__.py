import pulumi
import pulumi_aws as aws
import s3_website  # noqa: F401
import api_gateway  # noqa: F401
import lambda_layer  # noqa: F401
import ingestion  # noqa: F401
from routes.health_check.infra import health_check_lambda
from dynamo_db import dynamodb_table


# S3 bucket to store raw images
bucket = aws.s3.Bucket("raw-image-bucket")

pulumi.export("region", aws.config.region)
pulumi.export("image_bucket_name", bucket.bucket)

# Open template readme and read contents into stack output
with open("./Pulumi.README.md") as f:
    pulumi.export("readme", f.read())
