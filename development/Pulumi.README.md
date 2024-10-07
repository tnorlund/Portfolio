# Stack README

Full markdown support! Substitute stack outputs dynamically so that links can depend on your infrastructure! Link to dashboards, logs, metrics, and more.

1. DynamoDB Table Name: ${outputs.table_name}

[${outputs.table_name}](https://${outputs.region}.console.aws.amazon.com/dynamodbv2/home?region=${outputs.region}#table?name=${outputs.table_name})
