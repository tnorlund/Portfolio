# Portfolio Project

This project is managed using Pulumi. It creates a static website hosted on S3 and served through CloudFront. The website is a portfolio of projects and is built using React.

## Project Structure

### `__main__.py`

The main entry point for the Pulumi program. It defines the different stacks that are created.

### `s3_website.py`

Defines the infrastructure for hosting the static website (S3 + CloudFront).

### `process_ocr.py`

Has an S3 bucket for storing the raw data and a Lambda function for processing the data.

### `api_gateway.py`

Defines the API that's between the website and DynamoDB. This references the different routes in the `routes/` directory. Each route has a Lambda function that is triggered by the API Gateway.

### `dynamo_db.py`

The DynamoDB table that stores the data for the website.

### `lambda_layer.py` & `lambda_layer/`

Defines the Lambda Layer that is shared between the different Lambda functions.

## Outputs

| Name | Description | Value | File |
| --- | --- | --- | --- |
| `domains` | The URL of the website. | ${outputs.domains} | `s3_website.py` |
| `cdn_bucket_name` | The S3 bucket where the website is hosted. | ${outputs.cdn_bucket_name} | `s3_website.py` |
| `cdn_distribution_id` | The CloudFront distribution ID. | ${outputs.cdn_distribution_id} | `s3_website.py` |
| `raw_bucket_name` | The S3 bucket where the raw data is stored. | ${outputs.raw_bucket_name} | `process_ocr.py` |
| `cluster_lambda_function_name` | The name of the Lambda function that processes the data. | ${outputs.cluster_lambda_function_name} | `process_ocr.py` |
| `api_domain` | The URL of the API Gateway. | ${outputs.api_domain} | `api_gateway.py` |
| `dynamo_table_name` | The name of the DynamoDB table. | ${outputs.dynamodb_table_name} | `dynamo_db.py` |
| `region` | The region where the infrastructure is deployed. | ${outputs.region} | `__main__.py` |
