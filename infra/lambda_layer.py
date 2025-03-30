import platform
import sys
import os
import shutil
import subprocess
import zipfile
import pulumi
import pulumi_aws as aws
import pulumi_command as command
import concurrent.futures
import time

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
        self.lambda_layer_dir = os.path.abspath(
            os.path.join(PROJECT_DIR, package_dir)
        )
        self.upload_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), f"upload_{layer_name}"
        )
        self.zip_file_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"{layer_name}_upload.zip",
        )
        self.python_target = os.path.join(self.upload_dir, "python")
        self.requirements_path = os.path.join(
            self.lambda_layer_dir, "requirements.txt"
        )

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
            "--network",
            "host",  # Use host networking to avoid proxy issues
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
            print(f"Installing dependencies for {self.layer_name}...")
            subprocess.check_call(docker_command)
            print(f"Dependencies installation completed for {self.layer_name}")
        except subprocess.CalledProcessError as e:
            print(f"Error installing dependencies for {self.layer_name}: {e}")
            raise e

    def create_zip_file(self):
        """Create the ZIP file with the Lambda Layer directory."""
        print(f"Creating ZIP file for {self.layer_name}...")
        with zipfile.ZipFile(
            self.zip_file_path, "w", zipfile.ZIP_DEFLATED
        ) as zipf:
            for root, _, files in os.walk(self.upload_dir):
                for file in files:
                    abs_path = os.path.join(root, file)
                    relative_path = os.path.relpath(abs_path, self.upload_dir)
                    zipf.write(abs_path, relative_path)
        print(f"ZIP file created for {self.layer_name}")

    def prepare_lambda_layer(self):
        """Prepare the Lambda Layer package - this can be done in parallel."""
        start_time = time.time()
        print(f"Starting preparation of Lambda layer: {self.layer_name}")

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

        elapsed_time = time.time() - start_time
        print(
            f"Lambda layer preparation completed for {self.layer_name} in {elapsed_time:.2f} seconds"
        )

        # Return the path to the ZIP file for further processing in the main thread
        return self.zip_file_path


def prepare_layer_in_parallel(layer_config):
    """Helper function to prepare a Lambda layer from configuration in parallel."""
    builder = LambdaLayerBuilder(
        package_dir=layer_config["package_dir"],
        layer_name=layer_config["layer_name"],
        python_version=layer_config.get("python_version", "3.13"),
    )
    return {
        "layer_name": layer_config["layer_name"],
        "zip_file_path": builder.prepare_lambda_layer(),
        "builder": builder,
    }


# Define the layers to build
layers_to_build = [
    {"package_dir": "receipt_dynamo", "layer_name": "receipt-dynamo"},
    {"package_dir": "receipt_label", "layer_name": "receipt-label"},
    # Add more layers here as needed
]

# Prepare layers in parallel using ThreadPoolExecutor
print(f"Starting parallel build of {len(layers_to_build)} Lambda layers...")
start_time = time.time()

# Use a dictionary to store results with named keys for clarity
prepared_layers = {}

# Use a ThreadPoolExecutor to build layers in parallel for the preparation phase only
with concurrent.futures.ThreadPoolExecutor() as executor:
    # Submit all preparation tasks and map configs to their futures
    future_to_config = {
        executor.submit(prepare_layer_in_parallel, layer_config): layer_config
        for layer_config in layers_to_build
    }

    # Process results as they complete
    for future in concurrent.futures.as_completed(future_to_config):
        config = future_to_config[future]
        layer_name = config["layer_name"]
        try:
            result = future.result()
            prepared_layers[layer_name] = result
            print(f"Successfully prepared layer: {layer_name}")
        except Exception as e:
            print(f"Error preparing layer {layer_name}: {str(e)}")
            raise e

elapsed_time = time.time() - start_time
print(f"All Lambda layers prepared in {elapsed_time:.2f} seconds")

# Now create the actual Pulumi resources in the main thread
lambda_layers = {}

for layer_name, layer_info in prepared_layers.items():
    print(f"Creating Pulumi resources for layer: {layer_name}")
    # Create the Lambda Layer resource
    lambda_layer = aws.lambda_.LayerVersion(
        layer_name,
        layer_name=layer_name,
        compatible_runtimes=[f"python{layer_info['builder'].python_version}"],
        code=pulumi.FileArchive(layer_info["zip_file_path"]),
        description=f"Lambda Layer for {layer_name}",
    )

    # Cleanup local build artifacts AFTER the layer is created
    cleanup_local_artifacts = command.local.Command(
        f"cleanup-{layer_name}-artifacts",
        create=f"rm -rf {layer_info['builder'].upload_dir} {layer_info['zip_file_path']}",
        opts=pulumi.ResourceOptions(depends_on=[lambda_layer]),
    )

    lambda_layers[layer_name] = lambda_layer
    print(f"Pulumi resources created for layer: {layer_name}")

# Access the built layers by name
dynamo_layer = lambda_layers["receipt-dynamo"]
label_layer = lambda_layers["receipt-label"]

# Export the layer ARNs for reference
pulumi.export("dynamo_layer_arn", dynamo_layer.arn)
pulumi.export("label_layer_arn", label_layer.arn)
