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

def lambda_handler(event, context):
    # read_characters_from_dynamodb(DYNAMODB_TABLE_NAME)

    print("triggered Lambda Function!")
    logger.info("Received event: %s", event)
    return {
            'statusCode': 200,
            'body': 'Health check passed'
        }
