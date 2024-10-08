import pulumi
import pulumi_aws as aws
import s3_website  # noqa: F401
import os
import subprocess
import shutil

# The DynamoDB table
dynamodb_table = aws.dynamodb.Table(
    "GHActionTable",
    attributes=[
        aws.dynamodb.TableAttributeArgs(
            name="PK",
            type="S",
        ),
        aws.dynamodb.TableAttributeArgs(
            name="SK",
            type="S",
        ),
    ],
    hash_key="PK",
    range_key="SK",
    billing_mode="PAY_PER_REQUEST",
    ttl=aws.dynamodb.TableTtlArgs(
        attribute_name="TimeToLive",
        enabled=True,
    ),
    stream_enabled=True,
    stream_view_type="NEW_IMAGE",
    tags={
        "Environment": "dev",
        "Name": "GHActionTable",
    },
)

pulumi.export("table_name", dynamodb_table.name)
pulumi.export("region", aws.config.region)


# Step 1: Package dependencies into a ZIP file
requirements = ["requests", "tesseract", "opencv-python"]  # Example packages
output_file = "package.zip"
subprocess.run(["pip", "install", "--target", "python", *requirements])
subprocess.run(["zip", "-r", output_file, "python"])
shutil.rmtree("python")

# Step 2: Create an S3 bucket to store the Lambda layer package
bucket = aws.s3.Bucket("lambda-layer-bucket")

# Step 3: Upload the ZIP file to the S3 bucket
layer_object = aws.s3.BucketObject("layer-zip",
    bucket=bucket.id,
    source=pulumi.FileAsset(output_file)
)

# aws.lambda_.LayerVersion()

# Step 4: Create the Lambda Layer
lambda_layer = aws.lambda_.LayerVersion(
    "mylambda-layer",
    layer_name="MyLambdaLayer",
    description="A Lambda layer with packages from PyPI",
    compatible_runtimes=["python3.10", "python3.9", "python3.11"],
    code=layer_object.bucket.apply(lambda bucket_name: f"s3://{bucket_name}/{layer_object.key}")
)

# Step 5: Clean up the ZIP file
# os.remove(output_file)

# open template readme and read contents into stack output
with open("./Pulumi.README.md") as f:
    pulumi.export("readme", f.read())
