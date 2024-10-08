# Stack README

This is the Pulumi used to generate the static site and the backend API. The stack outputs are used to generate links to the resources created by the stack.

1. DynamoDB Table: [${outputs.table_name}](https://${outputs.region}.console.aws.amazon.com/dynamodbv2/home?region=${outputs.region}#table?name=${outputs.table_name})

## URLs
1. [Bucket URL](${bucket_url})
2. [CDN URL](${cdn_url})
