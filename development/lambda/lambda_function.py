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
    # if event['httpMethod'] == 'GET' and event['path'] == '/health_check':
    #     return {
    #         'statusCode': 200,
    #         'body': 'Health check passed'
    #     }
    # return {
    #     'statusCode': 400,
    #     'body': 'Bad request'
    # }