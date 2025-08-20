"""
ChromaDB Compaction Lambda Handlers

This package contains the Lambda function handlers for ChromaDB compaction:

- enhanced_compaction_handler: Main compaction Lambda for processing stream messages
  Entry point: enhanced_compaction_handler.handle

- stream_processor: DynamoDB stream processor Lambda  
  Entry point: stream_processor.lambda_handler

Each handler module is designed to be imported independently by AWS Lambda runtime.
"""