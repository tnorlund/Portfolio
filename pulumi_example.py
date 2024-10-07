import pulumi
import pulumi_aws as aws

# Create a DynamoDB table
dynamodb_table = aws.dynamodb.Table(
    'my-dynamodb-table',
    attributes=[
        {
            'name': 'id',
            'type': 'S',  # 'S' means string, other types can be 'N' for number or 'B' for binary
        }
    ],
    hash_key='id',  # Partition key
    billing_mode='PAY_PER_REQUEST',  # PAY_PER_REQUEST for on-demand mode
    tags={
        'Environment': 'Dev',
        'Name': 'MyDynamoDBTable',
    }
)

# Export the table name
pulumi.export('table_name', dynamodb_table.name)