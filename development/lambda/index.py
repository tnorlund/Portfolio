import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):

    print("triggered Lambda Function!")
    logger.info("Received event: %s", event)
    return {
            'statusCode': 200,
            'body': 'Health check passed'
        }
