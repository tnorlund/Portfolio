import boto3
import os


def lambda_handler(event, context):
    layer_name = os.environ["LAYER_NAME"]
    bucket_name = os.environ["BUCKET_NAME"]
    s3_key = f"{layer_name}/layer.zip"
    description = os.environ.get(
        "LAYER_DESCRIPTION",
        f"Automatically built Lambda layer for {layer_name}",
    )
    # Parse compatible architectures as a comma-separated string
    arch_str = os.environ.get("COMPATIBLE_ARCHITECTURES", "x86_64,arm64")
    compatible_architectures = [arch.strip() for arch in arch_str.split(",")]

    lambda_client = boto3.client("lambda")

    response = lambda_client.publish_layer_version(
        LayerName=layer_name,
        Content={"S3Bucket": bucket_name, "S3Key": s3_key},
        CompatibleRuntimes=["python3.12"],
        Description=description,
        CompatibleArchitectures=compatible_architectures,
    )

    return {"LayerVersionArn": response["LayerVersionArn"]}
