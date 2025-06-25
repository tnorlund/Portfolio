# Portfolio Project

This project is managed using Pulumi. It creates a static website hosted on S3 and served through CloudFront. The website is a portfolio of projects and is built using React.

## Running Pulumi

The Pulumi infrastructure code is located in the `infra/` directory. You can run Pulumi commands from either:

1. **From the `infra/` directory (Recommended):**

   ```bash
   cd infra/
   pulumi up
   ```

2. **From the repository root:**
   ```bash
   pulumi -C infra up
   ```

The code automatically detects the correct project root directory regardless of where you run it from, making it work seamlessly in both local development and CI environments.

### Path Resolution

The Lambda Layer builder automatically resolves paths to find the correct package directories (`receipt_dynamo`, `receipt_label`, `receipt_upload`) whether you're running:

- Locally from the `infra/` directory
- Locally from the repository root
- In CI/CD environments (using `GITHUB_WORKSPACE`)

This is handled by the `_find_project_root()` function in `utils.py`.

## Project Structure

### Core Infrastructure Files

#### `__main__.py`

The main entry point for the Pulumi program. It:

- Defines the different stacks (dev, prod, etc.)
- Sets up the core infrastructure components
- Configures the ML training environment
- Creates VPC endpoints for AWS services
- Manages IAM roles and policies

#### `networking.py`

Defines the VPC and networking infrastructure:

- Creates a VPC with public and private subnets
- Sets up Internet Gateway and NAT Gateways
- Configures route tables and security groups
- Creates VPC endpoints for AWS services (including DynamoDB)
- Manages network access for CodeBuild and ML training instances

#### `ml_packages.py`

Pulumi component for building and deploying ML packages using AWS CodeBuild. This component:

- Automatically detects changes in ML package source code
- Builds packages in a suitable environment with Python and CUDA support
- Deploys built packages to EFS for use by ML training instances
- Stores build artifacts and state in S3
- Avoids unnecessary rebuilds by tracking package state

#### `efs_storage.py`

Manages EFS (Elastic File System) storage for ML training:

- Creates EFS file system and mount targets
- Sets up access points for training data and checkpoints
- Configures security groups for EFS access
- Manages IAM policies for EFS access

#### `job_queue.py`

Manages the job queue infrastructure:

- Creates SQS queues for job processing
- Sets up dead letter queues for failed jobs
- Configures queue policies and access controls
- Manages queue monitoring and metrics

#### `instance_registry.py`

Handles instance registration and management:

- Creates DynamoDB table for instance tracking
- Manages instance lifecycle and state
- Configures auto-registration of instances
- Handles instance cleanup and deregistration

#### `spot_interruption.py`

Manages spot instance interruption handling:

- Creates SNS topics for spot interruption notifications
- Sets up Lambda functions to handle interruptions
- Configures CloudWatch event rules
- Manages spot instance lifecycle events

#### `step_function.py`

Defines AWS Step Functions workflows:

- Creates state machines for workflow orchestration
- Manages workflow execution and monitoring
- Configures IAM roles for step function execution
- Handles workflow error handling and retries

### Website Infrastructure

#### `s3_website.py`

Defines the infrastructure for hosting the static website:

- Creates S3 bucket for website content
- Configures CloudFront distribution
- Sets up SSL certificates
- Manages website routing and caching

#### `raw_bucket.py`

Manages the S3 bucket for raw data storage:

- Creates and configures the raw data bucket
- Sets up appropriate access policies
- Manages bucket lifecycle rules

### API and Data Infrastructure

#### `api_gateway.py`

Defines the API infrastructure:

- Creates API Gateway and routes
- Integrates with Lambda functions
- Manages API stages and deployments
- Configures API access controls

For complete API documentation, see [API_DOCUMENTATION.md](API_DOCUMENTATION.md).
For recent fixes and deployment issues, see [API_GATEWAY_FIXES.md](API_GATEWAY_FIXES.md).

#### `dynamo_db.py`

Manages the DynamoDB infrastructure:

- Creates and configures DynamoDB tables
- Sets up table indexes and throughput
- Manages table backups and encryption
- Configures table access policies

### Lambda Infrastructure

#### `lambda_layer.py`

Manages shared Lambda layer:

- Creates Lambda layer for shared code
- Manages layer versions
- Configures layer permissions
- Updates layers through Codebuild when any files in the package changes

#### `routes/`

Directory containing individual API route definitions:

- Each route has its own Lambda function
- Routes are integrated with API Gateway
- Includes health check and other utility routes

### Configuration Files

#### `Pulumi.yaml`

Main Pulumi project configuration:

- Defines project name and runtime
- Specifies project dependencies
- Configures backend settings

#### `Pulumi.dev.yaml` & `Pulumi.prod.yaml`

Stack-specific configurations:

- Environment-specific settings
- Stack variables and secrets
- Resource configurations

#### `requirements.txt`

Python dependencies for the infrastructure code:

- Lists required Python packages
- Specifies package versions
- Used for dependency management

## Usage

### ML Package Building

To force rebuild packages:

```bash
pulumi config set ml-training:force-rebuild true --stack <stack>
pulumi up --stack <stack>
```

### Lambda Layer Rebuilds

To force a rebuild of the Lambda layer (for example in CI):

```bash
pulumi config set lambda-layer:force-rebuild true --stack <stack>
pulumi up --stack <stack>
pulumi config set lambda-layer:force-rebuild false --stack <stack>
```

### Stack Management

To create a new stack:

```bash
pulumi stack init <stack-name>
pulumi up --stack <stack-name>
```

To switch between stacks:

```bash
pulumi stack select <stack-name>
```

## Outputs

| Name                           | Description                                              | Value                                   | File             |
| ------------------------------ | -------------------------------------------------------- | --------------------------------------- | ---------------- |
| `domains`                      | The URL of the website.                                  | ${outputs.domains}                      | `s3_website.py`  |
| `cdn_bucket_name`              | The S3 bucket where the website is hosted.               | ${outputs.cdn_bucket_name}              | `s3_website.py`  |
| `cdn_distribution_id`          | The CloudFront distribution ID.                          | ${outputs.cdn_distribution_id}          | `s3_website.py`  |
| `raw_bucket_name`              | The S3 bucket where the raw data is stored.              | ${outputs.raw_bucket_name}              | `process_ocr.py` |
| `cluster_lambda_function_name` | The name of the Lambda function that processes the data. | ${outputs.cluster_lambda_function_name} | `process_ocr.py` |
| `api_domain`                   | The URL of the API Gateway.                              | ${outputs.api_domain}                   | `api_gateway.py` |
| `dynamo_table_name`            | The name of the DynamoDB table.                          | ${outputs.dynamodb_table_name}          | `dynamo_db.py`   |
| `region`                       | The region where the infrastructure is deployed.         | ${outputs.region}                       | `__main__.py`    |
