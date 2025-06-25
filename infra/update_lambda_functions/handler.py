"""
handler.py

This module contains the logic for updating AWS Lambda functions with new layers.
It is designed to handle the addition of a specified layer to Lambda functions that match
a certain environment tag, allowing for streamlined updates across multiple functions.

Functions include:
- Resolving the layer ARN from the provided layer name or using a default layer name
- Listing all Lambda functions and filtering them based on environment tags
- Updating the configuration of Lambda functions to include the new layer
- Ensuring that only one version of the same layer is used across functions

This script utilizes the boto3 library to interact with AWS Lambda services.
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)
STACK_NAME = os.environ.get("STACK_NAME", "dev")  # Default to 'dev' if not specified


def lambda_handler(event, context):
    layer_arn = event.get("layer_arn")
    layer_name = event.get("layer_name")

    if not layer_arn and not layer_name:
        layer_name = "receipt-dynamo"
        logger.info(
            "No layer_arn or layer_name provided. Defaulting to 'receipt-dynamo'."
        )

    lambda_client = boto3.client("lambda")

    # If no ARN is passed, resolve from layer name
    if not layer_arn and layer_name:
        response = lambda_client.list_layer_versions(LayerName=layer_name)
        if not response["LayerVersions"]:
            logger.error(f"No versions found for layer: {layer_name}")
            return {
                "statusCode": 400,
                "body": {"message": f"No versions found for layer '{layer_name}'"},
            }
        latest_version = response["LayerVersions"][0]
        layer_arn = latest_version["LayerVersionArn"]
        layer_version_number = latest_version["Version"]
    else:
        layer_version_number = layer_arn.split(":")[-1]

    logger.info(f"Resolved layer ARN: {layer_arn}")

    updated_functions = []

    paginator = lambda_client.get_paginator("list_functions")
    for page in paginator.paginate():
        for function in page["Functions"]:
            tags = lambda_client.list_tags(Resource=function["FunctionArn"]).get(
                "Tags", {}
            )
            if tags.get("environment") == STACK_NAME:
                layers = function.get("Layers", [])
                current_arns = [l["Arn"] for l in layers]

                # Ensure only one version of the same layer is used
                def same_layer_family(arn1, arn2):
                    return ":".join(arn1.split(":")[:-1]) == ":".join(
                        arn2.split(":")[:-1]
                    )

                # Remove any existing version of the same layer
                new_layers = [
                    l for l in current_arns if not same_layer_family(l, layer_arn)
                ]
                new_layers.append(layer_arn)

                lambda_client.update_function_configuration(
                    FunctionName=function["FunctionName"],
                    Layers=new_layers,
                )
                updated_functions.append(function["FunctionName"])
                logger.info(
                    f"Updated {function['FunctionName']} with new layers: {new_layers}"
                )
            else:
                logger.info(f"Skipping {function['FunctionName']} due to tag mismatch")

    return {
        "statusCode": 200,
        "body": {
            "layer_arn": layer_arn,
            "layer_version": layer_version_number,
            "updated_functions": updated_functions,
        },
    }
