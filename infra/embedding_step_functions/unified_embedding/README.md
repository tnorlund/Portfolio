# Unified Embedding Lambda Container

This is the new, cleaner structure for the embedding Lambda functions.

## Key Features

- **Single Docker Container**: All handlers run from one image
- **Runtime Handler Selection**: Uses `HANDLER_TYPE` environment variable
- **Clean Separation**: Business logic separated from Lambda boilerplate
- **Smart Response Formatting**: Automatically handles Step Functions vs API Gateway

## Structure

- `handler.py` - Lambda entry point
- `router.py` - Routes to appropriate handler
- `config.py` - Centralized configuration
- `handlers/` - Pure business logic
- `utils/` - Shared utilities

## How It Works

1. AWS Lambda invokes `handler.lambda_handler()`
2. Handler delegates to `router.route_request()`
3. Router checks `HANDLER_TYPE` environment variable
4. Appropriate handler function is called
5. Response is formatted based on invocation source

## Adding a New Handler

1. Create handler file in `handlers/`
2. Export a `handle(event, context)` function
3. Add to router's `HANDLER_MAP`
4. Add configuration to `config.py`

## Benefits Over Old Structure

- No complex class hierarchies
- No module-level state
- Easier to test
- Cleaner code organization
- Better separation of concerns