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
        aws.dynamodb.TableAttributeArgs(
            name="GSI1PK",
            type="S",
        ),
        aws.dynamodb.TableAttributeArgs(
            name="GSI1SK",
            type="S",
        ),
        aws.dynamodb.TableAttributeArgs(
            name="GSI2PK",
            type="S",
        ),
        aws.dynamodb.TableAttributeArgs(
            name="GSI2SK",
            type="S",
        ),
        aws.dynamodb.TableAttributeArgs(
            name="TYPE",
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
    global_secondary_indexes=[
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="GSI1",
            hash_key="GSI1PK",
            range_key="GSI1SK",
            projection_type="ALL",
        ),
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="GSI2",
            hash_key="GSI2PK",
            range_key="GSI2SK",
            projection_type="ALL",
        ),
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="GSITYPE",
            hash_key="TYPE",
            projection_type="ALL",
        ),
    ],
    tags={
        "Environment": "dev",
        "Name": "ReceiptsTable",
    },
)

pulumi.export("table_name", dynamodb_table.name)
