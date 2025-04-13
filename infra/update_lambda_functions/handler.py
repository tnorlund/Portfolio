import os
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)
STACK_NAME = os.environ.get(
    "STACK_NAME", "dev"
)  # Default to 'dev' if not specified


def lambda_handler(event, context):
    layer_name = event.get(
        "layer_name", "receipt-dynamo"
    )  # Allow flexibility via event input
    logger.info(f"Layer name: {layer_name}")
    logger.info(f"Stack name: {STACK_NAME}")

    lambda_client = boto3.client("lambda")
    layer_versions = lambda_client.list_layer_versions(LayerName=layer_name)

    if not layer_versions["LayerVersions"]:
        error_message = f"No versions found for layer '{layer_name}'."
        logger.error(error_message)
        return {
            "statusCode": 400,
            "body": {
                "message": error_message,
                "layer_name": layer_name,
            },
        }

    latest_version = layer_versions["LayerVersions"][0]
    latest_layer_arn = latest_version["LayerVersionArn"]
    layer_version_number = latest_version["Version"]

    logger.info(f"Latest layer ARN: {latest_layer_arn}")

    updated_functions = []

    paginator = lambda_client.get_paginator("list_functions")
    for page in paginator.paginate():
        for function in page["Functions"]:
            tags = lambda_client.list_tags(
                Resource=function["FunctionArn"]
            ).get("Tags", {})
            logger.info(
                f"Function '{function['FunctionName']}' has tags: {tags}"
            )

            if tags.get("environment") == STACK_NAME:
                logger.info(
                    f"Function '{function['FunctionName']}' matched stack tag '{STACK_NAME}'."
                )
                if "Layers" in function:
                    layers = function["Layers"]
                    uses_layer = any(
                        layer_name in layer["Arn"] for layer in layers
                    )

                    logger.info(
                        f"Function '{function['FunctionName']}' layers: {[layer['Arn'] for layer in layers]}"
                    )

                    if uses_layer:
                        new_layers = [
                            (
                                latest_layer_arn
                                if layer_name in layer["Arn"]
                                else layer["Arn"]
                            )
                            for layer in layers
                        ]
                        lambda_client.update_function_configuration(
                            FunctionName=function["FunctionName"],
                            Layers=new_layers,
                        )
                        updated_functions.append(function["FunctionName"])
                        logger.info(
                            f"Updated function: {function['FunctionName']} with layers: {new_layers}"
                        )
                    else:
                        logger.info(
                            f"Function '{function['FunctionName']}' does not use layer '{layer_name}'."
                        )
            else:
                logger.info(
                    f"Function '{function['FunctionName']}' skipped (stack mismatch)."
                )
    logger.info(f"Updated functions: {updated_functions}")

    return {
        "statusCode": 200,
        "body": {
            "layer_name": layer_name,
            "layer_version": layer_version_number,
            "layer_arn": latest_layer_arn,
            "updated_functions": updated_functions,
        },
    }
