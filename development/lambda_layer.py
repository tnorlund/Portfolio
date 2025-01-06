import sys
import os
import shutil
import subprocess
import zipfile
import pulumi
import pulumi_aws as aws

# Constants
PROJECT_DIR = os.path.dirname(__file__)
LAMBDA_LAYER_DIR = os.path.abspath(os.path.join(PROJECT_DIR, "../lambda_layer"))
UPLOAD_DIR = os.path.join(PROJECT_DIR, "upload")
ZIP_FILE_PATH = os.path.join(PROJECT_DIR, "upload.zip")
PACKAGE_NAME = os.path.join(LAMBDA_LAYER_DIR, "python")
PYTHON_TARGET = os.path.join(UPLOAD_DIR, "python")
S3_BUCKET_NAME = "lambdalayerpulumi"

def ensure_directory_exists(directory):
    """Ensure the directory exists."""
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)

def clean_previous_artifacts():
    """Ensure clean output directory."""
    if os.path.exists(UPLOAD_DIR):
        shutil.rmtree(UPLOAD_DIR)
    if os.path.exists(ZIP_FILE_PATH):
        os.remove(ZIP_FILE_PATH)

def install_dependencies():
    """Install the dependencies for the Lambda Layer."""
    try:
        subprocess.check_call([
            "pip", "install", PACKAGE_NAME, "--target", PYTHON_TARGET
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        print(f"Error installing dependencies: {e}")
        raise

def create_zip_file():
    """Create the ZIP file with the Lambda Layer directory."""
    with zipfile.ZipFile(ZIP_FILE_PATH, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(UPLOAD_DIR):
            for file in files:
                abs_path = os.path.join(root, file)
                relative_path = os.path.relpath(abs_path, UPLOAD_DIR)
                zipf.write(abs_path, relative_path)

def upload_to_s3():
    """Uploads the packaged .zip file to the specified S3 bucket."""
    s3_object = aws.s3.BucketObject(
        "lambda-layer-zip",
        bucket=S3_BUCKET_NAME,
        source=ZIP_FILE_PATH,
        key=os.path.basename(ZIP_FILE_PATH)
    )
    return s3_object.bucket, s3_object.key


def prepare_lambda_layer():
    """Prepare the Lambda Layer package."""
    # Ensure clean output directory
    clean_previous_artifacts()
    ensure_directory_exists(PYTHON_TARGET)

    # Install dependencies
    install_dependencies()

    # Create the ZIP file
    create_zip_file()

layer_name = "dynamo-receipt"
compatible_runtimes = ["python3.9"]  # Adjust runtime as needed

# Prepare the Lambda layer package
prepare_lambda_layer()
s3_bucket, s3_key = upload_to_s3()

lambda_layer = aws.lambda_.LayerVersion(
    layer_name,
    layer_name=layer_name,
    compatible_runtimes=compatible_runtimes,
    code=pulumi.FileArchive(ZIP_FILE_PATH),
    description="Lambda Layer for accessing the DynamoDB table",
)
