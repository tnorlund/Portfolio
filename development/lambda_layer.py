import sys
import os
import shutil
import zipfile
import pulumi
import pulumi_aws as aws


# Constants
project_dir = os.path.dirname(__file__)
lambda_layer_dir = os.path.abspath(os.path.join(project_dir, "../lambda_layer"))  # Path to lambda_layer
output_dir = os.path.join(project_dir, "upload")
zip_file_path = os.path.join(project_dir, "upload.zip")

layer_name = "dynamo-receipt"
compatible_runtimes = ["python3.9"]  # Adjust runtime as needed

# Step 1: Create the ZIP file with the lambda_layer directory
def prepare_lambda_layer():
    # Ensure clean output directory
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # Copy lambda_layer directory into the upload directory
    lambda_layer_target = os.path.join(output_dir, "python")
    shutil.copytree(lambda_layer_dir, lambda_layer_target, dirs_exist_ok=True)

    # Create the ZIP file
    with zipfile.ZipFile(zip_file_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(output_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, output_dir)
                zipf.write(file_path, arcname)

# Prepare the Lambda layer package
prepare_lambda_layer()

# Ensure the ZIP file exists
if not os.path.isfile(zip_file_path):
    raise FileNotFoundError(f"ZIP file not found: {zip_file_path}")

# Step 2: Create the Lambda Layer directly without S3
lambda_layer = aws.lambda_.LayerVersion(
    layer_name,
    layer_name=layer_name,
    compatible_runtimes=compatible_runtimes,
    code=pulumi.AssetArchive({
        ".": pulumi.FileArchive(zip_file_path)
    }),
    description="Lambda Layer for accessing the DynamoDB table",
)