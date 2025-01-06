import os
import logging
import json
from dynamo import DynamoClient


logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_table_name = os.environ['DYNAMODB_TABLE_NAME']


def handler(event, context):
    logger.info("Received event: %s", event)
    http_method = event['requestContext']['http']['method'].upper()

    if http_method == 'GET':
        query_params = event.get("queryStringParameters") or {}
        image_id = query_params.get("image_id")
        if image_id is not None:
            return {
                'statusCode': 400,
                'body': 'Bad request: image_id is required'
            }
        try:
            client = DynamoClient(dynamodb_table_name)
            image, lines, words, letters, scaled_images = client.getImageDetails(int(image_id))
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'image': dict(image),
                    'lines': [dict(line) for line in lines],
                    'words': [dict(word) for word in words],
                    'letters': [dict(letter) for letter in letters],
                    'scaled_images': [dict(scaled_image) for scaled_image in scaled_images]
                })
            }
        except Exception as e:
            return {
                'statusCode': 500,
                'body': f'Internal server error: {str(e)}'
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