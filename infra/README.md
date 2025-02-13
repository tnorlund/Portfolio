# Portfolio Project

This project is managed using Pulumi. It creates a static website hosted on S3 and served through CloudFront. The website is a portfolio of projects and is built using React.

## Project Structure

### `__main__.py`

The main entry point for the Pulumi program. It defines the different stacks that are created.

### `s3_website.py`

Defines the infrastructure for hosting the static website (S3 + CloudFront).

### `raw_bucket.py`

Has an S3 bucket for storing the raw data.

### `api_gateway.py`

Defines the API that's between the website and DynamoDB. This references the different routes in the `routes/` directory. Each route has a Lambda function that is triggered by the API Gateway.

### `dynamo_db.py`

The DynamoDB table that stores the data for the website.

### `lambda_layer.py` & `lambda_layer/`

Defines the Lambda Layer that is shared between the different Lambda functions.