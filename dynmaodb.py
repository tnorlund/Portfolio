import boto3
import os
from datetime import datetime
from datetime import timezone


# Function to read a .box file and upload to DynamoDB
def upload_box_file_to_dynamodb(box_file_path, dynamodb_table_name, aws_region="us-east-1"):
    # Initialize a DynamoDB resource using boto3
    dynamodb = boto3.resource('dynamodb', region_name=aws_region)
    table = dynamodb.Table(dynamodb_table_name)

    try:
        # Open the .box file and read each line
        with open(box_file_path, 'r') as box_file:
            for line in box_file:
                # Each line in the .box file is structured as:
                # character x1 y1 x2 y2 page_number
                components = line.strip().split()
                
                if len(components) == 6:
                    character = components[0]
                    x1, y1, x2, y2 = map(int, components[1:5])
                    # page_number = int(components[5])
                    # get the Z format date
                    # Get the current date and time in ISO 8601 format with Z
                    current_time = str(datetime.now(timezone.utc).isoformat())

                    PK = f'{file_path}#{character}'  # Partition Key
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

                    # Insert the item into DynamoDB
                    table.put_item(Item=item)
                    print(f"Uploaded character '{character}' on page {box_file_path} with bounding box ({x1}, {y1}, {x2}, {y2})")
                else:
                    print(f"Skipping invalid line: {line}")

    except Exception as e:
        print(f"Error uploading to DynamoDB: {e}")

# Example usage
dynamodb_table_name = 'rec'

# iterate over the box files in the "out/" directory and upload them to DynamoDB
directory_path = 'out/'

# Upload the first .box file to DynamoDB
for filename in os.listdir(directory_path):
    if filename.endswith('.box'):
        file_path = os.path.join(directory_path, filename)
        upload_box_file_to_dynamodb(file_path, dynamodb_table_name)
        break  # Only upload the first .box file
