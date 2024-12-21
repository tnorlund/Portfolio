import pulumi
import pulumi_aws as aws
import s3_website  # noqa: F401
import api_gateway  # noqa: F401
from routes.health_check.infra import health_check_lambda
from dynamo_db import dynamodb_table

pulumi.export("region", aws.config.region)

# Open template readme and read contents into stack output
with open("./Pulumi.README.md") as f:
    pulumi.export("readme", f.read())
