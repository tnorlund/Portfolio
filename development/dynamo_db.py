import pulumi
import pulumi_aws as aws

# The DynamoDB table
dynamodb_table = aws.dynamodb.Table(
    "ReceiptsTable",
    attributes=[
        aws.dynamodb.TableAttributeArgs(
            name="PK",
            type="S",
        ),
        aws.dynamodb.TableAttributeArgs(
            name="SK",
            type="S",
        ),
    ],
    hash_key="PK",
    range_key="SK",
    billing_mode="PAY_PER_REQUEST",
    ttl=aws.dynamodb.TableTtlArgs(
        attribute_name="TimeToLive",
        enabled=True,
    ),
    stream_enabled=True,
    stream_view_type="NEW_IMAGE",
    tags={
        "Environment": "dev",
        "Name": "ReceiptsTable",
    },
)

pulumi.export("table_name", dynamodb_table.name)
