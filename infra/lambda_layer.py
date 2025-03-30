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
PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)  # Now points to the root directory
S3_BUCKET_NAME = "lambdalayerpulumi"


class LambdaLayerBuilder:
    def __init__(self, package_dir, layer_name, python_version="3.13"):
        self.package_dir = package_dir
        self.layer_name = layer_name
        self.python_version = python_version
        self.lambda_layer_dir = os.path.abspath(os.path.join(PROJECT_DIR, package_dir))
        self.upload_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), f"upload_{layer_name}"
        )
        self.zip_file_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), f"{layer_name}_upload.zip"
        )
        self.python_target = os.path.join(self.upload_dir, "python")
        self.requirements_path = os.path.join(self.lambda_layer_dir, "requirements.txt")

    def ensure_directory_exists(self, directory):
        """Ensure the directory exists."""
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

    def clean_previous_artifacts(self):
        """Ensure clean output directory."""
        if os.path.exists(self.upload_dir):
            shutil.rmtree(self.upload_dir)
        if os.path.exists(self.zip_file_path):
            os.remove(self.zip_file_path)

    def install_dependencies(self):
        """Install the dependencies for the Lambda Layer using Docker."""
        arch = platform.machine().lower()

        docker_command = [
            "docker",
            "run",
            "--rm",
        ]

        # Only force x86_64 architecture if on ARM (e.g., Apple Silicon)
        if "arm" in arch or "aarch" in arch:
            docker_command.extend(["--platform", "linux/amd64"])

        docker_command.extend(
            [
                "-v",
                f"{PROJECT_DIR}:{PROJECT_DIR}",
                "--entrypoint",
                "bash",
                "-w",
                self.lambda_layer_dir,
                f"public.ecr.aws/lambda/python:{self.python_version}",
                "-c",
                (
                    "dnf install -y gcc python3-devel libjpeg-devel zlib-devel && "
                    "pip install --upgrade pip && "
                    f"pip install --target {self.python_target} -r {self.requirements_path} --upgrade --root-user-action=ignore && "
                    f"pip install --target {self.python_target} . --upgrade --root-user-action=ignore && "
                    f"chmod -R a+w {self.python_target}"
                ),
            ]
        )

        try:
            subprocess.check_call(docker_command)
        except subprocess.CalledProcessError as e:
            print(f"Error installing dependencies: {e}")
            raise e

    def create_zip_file(self):
        """Create the ZIP file with the Lambda Layer directory."""
        with zipfile.ZipFile(self.zip_file_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(self.upload_dir):
                for file in files:
                    abs_path = os.path.join(root, file)
                    relative_path = os.path.relpath(abs_path, self.upload_dir)
                    zipf.write(abs_path, relative_path)

    def upload_to_s3(self):
        """Uploads the packaged .zip file to the specified S3 bucket."""
        s3_object = aws.s3.BucketObject(
            f"{self.layer_name}-lambda-layer-zip",
            bucket=S3_BUCKET_NAME,
            source=self.zip_file_path,
            key=os.path.basename(self.zip_file_path),
        )
        return s3_object.bucket, s3_object.key

    def prepare_lambda_layer(self):
        """Prepare the Lambda Layer package."""
        # Ensure clean output directory
        self.clean_previous_artifacts()
        self.ensure_directory_exists(self.python_target)

        # Clean Python target directory if it exists
        if os.path.exists(self.python_target):
            print(f"Cleaning existing content in {self.python_target}")
            for item in os.listdir(self.python_target):
                item_path = os.path.join(self.python_target, item)
                if os.path.isfile(item_path):
                    os.remove(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)

        # Install dependencies
        self.install_dependencies()

        # Create the ZIP file
        self.create_zip_file()

    def build(self):
        """Build the Lambda Layer and return the resource."""
        # 1) Build & package
        self.prepare_lambda_layer()
        s3_bucket, s3_key = self.upload_to_s3()

        # 2) Create the Lambda Layer resource
        lambda_layer = aws.lambda_.LayerVersion(
            self.layer_name,
            layer_name=self.layer_name,
            compatible_runtimes=[f"python{self.python_version}"],
            code=pulumi.FileArchive(self.zip_file_path),
            description=f"Lambda Layer for {self.layer_name}",
        )

        # 3) Cleanup local build artifacts AFTER the layer is created
        cleanup_local_artifacts = command.local.Command(
            f"cleanup-{self.layer_name}-artifacts",
            create=f"rm -rf {self.upload_dir} {self.zip_file_path}",
            opts=pulumi.ResourceOptions(depends_on=[lambda_layer]),
        )

        return lambda_layer


# Create the receipt_dynamo lambda layer
dynamo_layer = LambdaLayerBuilder(
    package_dir="receipt_dynamo", layer_name="receipt-dynamo"
).build()

# Create the receipt_label lambda layer
label_layer = LambdaLayerBuilder(
    package_dir="receipt_label", layer_name="receipt-label"
).build()

# Export the layer ARNs for reference
pulumi.export("dynamo_layer_arn", dynamo_layer.arn)
pulumi.export("label_layer_arn", label_layer.arn)
