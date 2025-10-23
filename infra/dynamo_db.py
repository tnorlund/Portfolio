import pulumi
import pulumi_aws as aws

stack = pulumi.get_stack()


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
            name="GSI3PK",
            type="S",
        ),
        aws.dynamodb.TableAttributeArgs(
            name="GSI3SK",
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
        attribute_name="time_to_live",
        enabled=True,
    ),
    stream_enabled=True,
    stream_view_type="NEW_AND_OLD_IMAGES",
    point_in_time_recovery=aws.dynamodb.TablePointInTimeRecoveryArgs(
        enabled=True
    ),
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
            name="GSI3",
            hash_key="GSI3PK",
            range_key="GSI3SK",
            projection_type="ALL",
        ),
        aws.dynamodb.TableGlobalSecondaryIndexArgs(
            name="GSITYPE",
            hash_key="TYPE",
            projection_type="ALL",
        ),
    ],
    tags={
        "Pulumi_Stack": stack,
        "Pulumi_Project": pulumi.get_project(),
        "Environment": "dev",
        "Name": "ReceiptsTable",
    },
)

pulumi.export("dynamodb_table_name", dynamodb_table.name)
pulumi.export("dynamodb_table_arn", dynamodb_table.arn)
pulumi.export("dynamodb_stream_arn", dynamodb_table.stream_arn)
