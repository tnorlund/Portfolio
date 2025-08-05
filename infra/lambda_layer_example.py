"""
Example of how to update existing Lambda functions to use latest layer versions.
"""

import pulumi
import pulumi_aws as aws
from lambda_layer_utils import DynamicLayerReference

# Get current configuration
stack = pulumi.get_stack()
account_id = aws.get_caller_identity().account_id
region = aws.get_region().name

# Initialize the dynamic layer helper
layer_helper = DynamicLayerReference(stack, account_id, region)

# Example 1: Update an existing Lambda function to use latest layers
# Instead of hardcoded layer ARNs like this:
# layers=[
#     f"arn:aws:lambda:{region}:{account_id}:layer:receipt-dynamo-layer-{stack}:5",
#     f"arn:aws:lambda:{region}:{account_id}:layer:receipt-label-layer-{stack}:3",
# ]

# Use dynamic references:
api_lambda = aws.lambda_.Function(
    "my-api-lambda",
    runtime="python3.12",
    handler="handler.main",
    role=my_lambda_role.arn,
    layers=[
        layer_helper.get_layer_arn("receipt-dynamo"),
        layer_helper.get_layer_arn("receipt-label"),
    ],
    # ... other configuration ...
)

# Example 2: Using the convenience function
from lambda_layer_utils import create_lambda_with_latest_layers

api_lambda_v2 = create_lambda_with_latest_layers(
    name="my-api-lambda-v2",
    handler="handler.main",
    role=my_lambda_role.arn,
    stack=stack,
    account_id=account_id,
    region=region,
    layers_to_use=["receipt-dynamo", "receipt-label"],
)

# Example 3: For your polling handler that creates deltas
polling_lambda = aws.lambda_.Function(
    "chromadb-polling-handler",
    runtime="python3.12",
    handler="poll_line_embedding_batch_handler_chromadb.poll_handler",
    role=polling_lambda_role.arn,
    layers=[
        # These will always point to the latest versions
        layer_helper.get_layer_arn("receipt-dynamo"),
        layer_helper.get_layer_arn("receipt-label"),
    ],
    environment=aws.lambda_.FunctionEnvironmentArgs(
        variables={
            "DELTA_BUCKET": chromadb_storage.bucket_name,
            "DELTA_QUEUE_URL": delta_queue.url,
        },
    ),
)

# Example 4: If you want to query the current version for monitoring
latest_dynamo_version = layer_helper.get_layer_arn("receipt-dynamo")
pulumi.export("latest_receipt_dynamo_layer", latest_dynamo_version)