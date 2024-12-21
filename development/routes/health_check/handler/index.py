import os
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Get environment variables
DYNAMODB_TABLE_NAME = os.environ['DYNAMODB_TABLE_NAME']


# read all characters from DynamoDB
def read_characters_from_dynamodb(dynamodb_table_name, aws_region="us-east-1"):
    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource("dynamodb", region_name=aws_region)
    table = dynamodb.Table(dynamodb_table_name)

    try:
        # Scan the table to get all items
        response = table.scan()
        items = response.get("Items", [])

        for item in items:
            print(item)

    except Exception as e:
        print(f"Error reading from DynamoDB: {e}")

def handler(event, context):
    print("triggered Lambda Function!")
    logger.info("Received event: %s", event)
    http_method = event['requestContext']['http']['method'].upper()

    if http_method == 'GET':
        read_characters_from_dynamodb(DYNAMODB_TABLE_NAME)
        return {
            'statusCode': 200,
            'body': 'Health check passed'
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
