import platform
import sys
import os
import shutil
import subprocess
import zipfile
import pulumi
import pulumi_aws as aws
import pulumi_command as command

# Constants
PROJECT_DIR = os.path.dirname(__file__)
LAMBDA_LAYER_DIR = os.path.abspath(os.path.join(PROJECT_DIR, "lambda_layer"))
UPLOAD_DIR = os.path.join(PROJECT_DIR, "upload")
ZIP_FILE_PATH = os.path.join(PROJECT_DIR, "upload.zip")
PACKAGE_NAME = os.path.join(LAMBDA_LAYER_DIR, "python")
PYTHON_TARGET = os.path.join(UPLOAD_DIR, "python")
REQUIREMENTS_PATH = os.path.join(PACKAGE_NAME, "requirements.txt")
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
    """Install the dependencies for the Lambda Layer using Docker (Python 3.13)."""
    arch = platform.machine().lower()

    docker_command = [
        "docker", "run", "--rm",
    ]

    # Only force x86_64 architecture if on ARM (e.g., Apple Silicon)
    if "arm" in arch or "aarch" in arch:
        docker_command.extend(["--platform", "linux/amd64"])

    docker_command.extend([
        "-v", f"{PROJECT_DIR}:{PROJECT_DIR}",
        "--entrypoint", "bash",
        "-w", os.path.join(LAMBDA_LAYER_DIR, "python"),
        "public.ecr.aws/lambda/python:3.13",
        "-c",
        (
            "dnf install -y gcc python3-devel libjpeg-devel zlib-devel && "
            "pip install --upgrade pip && "
            f"pip install --target {PYTHON_TARGET} -r {REQUIREMENTS_PATH} && "
            f"pip install --target {PYTHON_TARGET} . && "
            f"chmod -R a+w {PYTHON_TARGET}"
        )
    ])

    try:
        subprocess.check_call(docker_command)
    except subprocess.CalledProcessError as e:
        print(f"Error installing dependencies: {e}")
        raise e


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
        key=os.path.basename(ZIP_FILE_PATH),
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
compatible_runtimes = ["python3.13"]

# 1) Build & package
prepare_lambda_layer()
s3_bucket, s3_key = upload_to_s3()

# 2) Create the Lambda Layer resource
lambda_layer = aws.lambda_.LayerVersion(
    layer_name,
    layer_name=layer_name,
    compatible_runtimes=compatible_runtimes,
    code=pulumi.FileArchive(ZIP_FILE_PATH),
    description="Lambda Layer for accessing the DynamoDB table",
)

# 3) Cleanup local build artifacts AFTER the layer is created
cleanup_local_artifacts = command.local.Command(
    "cleanup-local-build-artifacts",
    create=f"rm -rf {UPLOAD_DIR} {ZIP_FILE_PATH}",
    opts=pulumi.ResourceOptions(depends_on=[lambda_layer])
)