import os
import logging
import json
from dynamo import Image, Line, Word, Letter, DynamoClient # type: ignore

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get the environment variables
dynamodb_table_name = os.environ['DYNAMODB_TABLE_NAME']

def handler(event, _):
    logger.info("Received event: %s", event)
    http_method = event['requestContext']['http']['method'].upper()

    if http_method == 'GET':
        client = DynamoClient(dynamodb_table_name)
        query_params = event.get("queryStringParameters") or {}
        if "limit" not in query_params:
            images, _ = client.listImages()
            return {
                'statusCode': 200,
                'body': json.dumps({'images': [dict(image) for image in images]})
            }
        else:
            limit = int(query_params["limit"])
            if "last_evaluated_key" in query_params:
                last_evaluated_key = json.loads(query_params["last_evaluated_key"])
                images, last_evaluated_key = client.listImages(limit, last_evaluated_key)
                return {
                    'statusCode': 200,
                    'body': json.dumps({'images': [dict(image) for image in images], 'last_evaluated_key': last_evaluated_key})
                }
            images, last_evaluated_key = client.listImages(limit)
            return {
                'statusCode': 200,
                'body': json.dumps({'images': [dict(image) for image in images], 'last_evaluated_key': last_evaluated_key})
            }
    elif http_method == 'POST':
        return {
            'statusCode': 405,
            'body': 'Method not allowed'
        }
    else:
        return {
            'statusCode': 405,
            'body': f'Method {http_method} not allowed'
        }
