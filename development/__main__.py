import pulumi
import pulumi_aws as aws

dynamodb_table = aws.dynamodb.Table(
    "my-dynamodb-table",
    attributes=[
        aws.dynamodb.TableAttributeArgs(
            name="Id",
            type="S",
        )
    ],
    hash_key="Id",
    billing_mode="PAY_PER_REQUEST",
    ttl=aws.dynamodb.TableTtlArgs(
        attribute_name="TimeToLive",
        enabled=True,
    ),
    stream_enabled=True,
    stream_view_type="NEW_IMAGE",
    tags={
        "Environment": "dev",
        "Name": "my-dynamodb-table",
    }
)

pulumi.export("table_name", dynamodb_table.name)