import boto3
from datetime import datetime
from datetime import timezone
import pulumi.automation as auto
import os
from dotenv import load_dotenv, dotenv_values
from pathlib import Path

# Load environment variables from .env file
env_vars = dotenv_values('.env')
# set the pulumi access token
os.environ['PULUMI_ACCESS_TOKEN'] = env_vars['PULUMI_ACCESS_TOKEN']



def get_stack_output(stack_name:str, project_name:str="tnorlund"):
    try:
        # Create or select the stack
        stack = auto.select_stack(stack_name, project_name, program=lambda: None)
        # Get the stack outputs
        outputs = stack.outputs()
        
        return outputs
    except Exception as e:
        print(f"Error getting stack output: {e}")

# Function to read a .box file and upload to DynamoDB
def upload_box_file_to_dynamodb(box_file_path, dynamodb_table_name, aws_region="us-east-1"):
    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource('dynamodb', region_name=aws_region)
    table = dynamodb.Table(dynamodb_table_name)

    try:
        # Open the .box file and read each line
        with open(box_file_path, 'r') as box_file:
            with table.batch_writer() as batch:
                for line in box_file:
                    # Each line in the .box file is structured as:
                    # character x1 y1 x2 y2 page_number
                    components = line.strip().split()
                    
                    if len(components) == 6:
                        character = components[0]
                        x1, y1, x2, y2 = map(int, components[1:5])
                        current_time = str(datetime.now(timezone.utc).isoformat())

                        PK = f'{box_file_path}#{character}#{current_time}'
                        SK = f'{current_time}'

                        # Prepare the item to insert into DynamoDB
                        item = {
                            'PK': PK,           # Partition Key
                            'SK': SK,        # Sort Key or an attribute
                            'file': box_file_path,
                            'BoundingBox': {
                                'X1': x1,
                                'Y1': y1,
                                'X2': x2,
                                'Y2': y2
                            }
                        }
                        batch.put_item(Item=item)
                    else:
                        print(f"Skipping invalid line: {line}")

    except Exception as e:
        print(f"Error uploading to DynamoDB: {e}")

# read all characters from DynamoDB
def read_characters_from_dynamodb(dynamodb_table_name, aws_region="us-east-1"):
    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource('dynamodb', region_name=aws_region)
    table = dynamodb.Table(dynamodb_table_name)

    try:
        # Scan the table to get all items
        response = table.scan()
        items = response.get('Items', [])

        for item in items:
            print(item)

    except Exception as e:
        print(f"Error reading from DynamoDB: {e}")



# Example usage
stack_name = "tnorlund/development/dev"
stack_outputs = get_stack_output(stack_name)

# iterate over the box files in the "out/" directory and upload them to DynamoDB
directory_path = 'out/'

upload_box_file_to_dynamodb('out/Rec86.box', stack_outputs['table_name'].value, stack_outputs['region'].value)