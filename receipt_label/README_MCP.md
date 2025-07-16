# Receipt Validation MCP Server

A Model Context Protocol (MCP) server for validating receipt labels using DynamoDB and Pinecone. The server automatically fetches configuration from Pulumi stacks, eliminating the need for manual environment variable setup.

## Features

- **Automatic Configuration**: Fetches all necessary configuration from Pulumi stack
- **Label Validation**: Validates labels against CORE_LABELS schema
- **Similar Label Search**: Queries Pinecone for similar valid labels
- **DynamoDB Persistence**: Saves validated labels to DynamoDB
- **Stack Management**: Easy switching between dev/prod environments

## Installation

```bash
pip install -e ".[dev]"
```

## Usage

### Running the Server

```bash
python -m receipt_label.mcp_validation_server
```

Or using the installed script:

```bash
receipt-validation-server
```

### Available Tools

#### 1. `initialize_stack`
Initialize the server with a Pulumi stack configuration.

```json
{
  "stack_name": "tnorlund/portfolio/dev"
}
```

This tool:
- Fetches all configuration from the specified Pulumi stack
- Extracts API keys (OpenAI, Pinecone)
- Gets DynamoDB table name from stack outputs
- Initializes all client connections

#### 2. `validate_labels`
Validate receipt labels against CORE_LABELS schema.

```json
{
  "image_id": "550e8400-e29b-41d4-a716-446655440000",
  "receipt_id": 12345,
  "labels": [
    {"word_id": 1, "label": "MERCHANT_NAME", "text": "WALMART"},
    {"word_id": 2, "label": "INVALID_LABEL", "text": "something"}
  ]
}
```

#### 3. `query_similar_labels`
Query Pinecone for similar valid labels.

```json
{
  "text": "TOTAL",
  "merchant_name": "Walmart",
  "top_k": 5
}
```

Returns similar words from receipts that have valid labels assigned.

#### 4. `persist_validated_labels`
Save validated labels to DynamoDB.

```json
{
  "image_id": "550e8400-e29b-41d4-a716-446655440000",
  "receipt_id": 12345,
  "validated_labels": [
    {
      "word_id": 1,
      "line_id": 1,
      "label": "MERCHANT_NAME",
      "reasoning": "Top text identified as merchant",
      "confidence": 0.95
    }
  ]
}
```

#### 5. `get_receipt_labels`
Retrieve all labels for a receipt from DynamoDB.

```json
{
  "image_id": "550e8400-e29b-41d4-a716-446655440000",
  "receipt_id": 12345
}
```

#### 6. `get_stack_info`
Get current Pulumi stack configuration info.

```json
{}
```

## Example Workflow

1. Initialize with a Pulumi stack:
```json
{
  "tool": "initialize_stack",
  "arguments": {"stack_name": "tnorlund/portfolio/dev"}
}
```

2. Validate some labels:
```json
{
  "tool": "validate_labels",
  "arguments": {
    "image_id": "550e8400-e29b-41d4-a716-446655440000",
    "receipt_id": 12345,
    "labels": [
      {"word_id": 1, "label": "MERCHANT_NAME", "text": "WALMART"}
    ]
  }
}
```

3. Find similar labels for corrections:
```json
{
  "tool": "query_similar_labels",
  "arguments": {
    "text": "TOTAL",
    "merchant_name": "Walmart"
  }
}
```

4. Persist validated labels:
```json
{
  "tool": "persist_validated_labels",
  "arguments": {
    "image_id": "550e8400-e29b-41d4-a716-446655440000",
    "receipt_id": 12345,
    "validated_labels": [
      {"word_id": 1, "line_id": 1, "label": "GRAND_TOTAL"}
    ]
  }
}
```

## Pulumi Stack Requirements

The Pulumi stack must have the following configuration values:

- `portfolio:OPENAI_API_KEY`
- `portfolio:PINECONE_API_KEY`
- `portfolio:PINECONE_INDEX_NAME`
- `portfolio:PINECONE_HOST`

And the following stack output:

- `dynamoTableName`

## Development

The server uses the ClientManager from receipt_label to handle all external service connections. Configuration is automatically fetched from Pulumi, making it easy to switch between environments without code changes.

### Adding New Tools

1. Add the tool definition to `list_tools()`
2. Add the handler to `call_tool()`
3. Implement the tool logic as an async method

### Error Handling

The server includes comprehensive error handling:
- Validates Pulumi stack existence
- Checks client initialization before operations
- Provides clear error messages for missing configuration
- Handles DynamoDB and Pinecone errors gracefully