"""Response formatting utilities for ChromaDB compaction Lambda functions."""

import json
from typing import Any, Dict, Optional, Union


def format_response(
    result: Any,
    event: Dict[str, Any],
    is_error: bool = False,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Format Lambda response based on invocation source and result.

    Args:
        result: The result from the handler operation
        event: Original Lambda event
        is_error: Whether this is an error response
        correlation_id: Correlation ID for tracking

    Returns:
        Formatted response appropriate for the invocation source
    """
    # Detect invocation source
    invocation_source = detect_invocation_source(event)
    
    # Handle different result types
    if hasattr(result, "to_dict"):
        # Result has a to_dict method (like LambdaResponse)
        response_data = result.to_dict()
    elif isinstance(result, dict):
        # Result is already a dictionary
        response_data = result.copy()
    else:
        # Convert other types to basic response
        response_data = {
            "statusCode": 500 if is_error else 200,
            "message": str(result) if result else "Operation completed",
        }

    # Add correlation ID if provided
    if correlation_id:
        response_data["correlation_id"] = correlation_id

    # Add invocation metadata
    response_data["invocation_source"] = invocation_source
    
    # Format based on invocation source
    if invocation_source == "sqs":
        return format_sqs_response(response_data, is_error)
    elif invocation_source == "stepfunctions":
        return format_stepfunctions_response(response_data, is_error)
    elif invocation_source == "eventbridge":
        return format_eventbridge_response(response_data, is_error)
    else:
        # Default API Gateway/direct invoke format
        return format_api_response(response_data, is_error)


def detect_invocation_source(event: Dict[str, Any]) -> str:
    """Detect the source of Lambda invocation.

    Args:
        event: Lambda event dictionary

    Returns:
        Invocation source identifier
    """
    # SQS event
    if "Records" in event and event["Records"]:
        record = event["Records"][0]
        if "eventSource" in record and record["eventSource"] == "aws:sqs":
            return "sqs"
        elif "eventName" in record and "dynamodb" in record.get("eventSourceARN", "").lower():
            return "dynamodb_stream"

    # Step Functions
    if any(key.startswith("batch_") for key in event.keys()) or "operation" in event:
        return "stepfunctions"

    # EventBridge
    if "source" in event and "detail-type" in event:
        return "eventbridge"

    # API Gateway
    if "httpMethod" in event and "requestContext" in event:
        return "api_gateway"

    # Direct invoke or test
    return "direct_invoke"


def format_sqs_response(response_data: Dict[str, Any], is_error: bool) -> Dict[str, Any]:
    """Format response for SQS-triggered Lambda.

    For SQS, we need to handle partial failures appropriately.
    """
    if is_error:
        # For SQS, throwing an exception causes message retry
        # Return success but log the error details
        return {
            "statusCode": 200,  # Don't retry the message
            "message": response_data.get("message", "Partial failure"),
            "error": response_data.get("error"),
            "processed": response_data.get("processed_messages", 0),
            "failed": response_data.get("failed_messages", 0),
        }
    
    return response_data


def format_stepfunctions_response(
    response_data: Dict[str, Any], is_error: bool
) -> Dict[str, Any]:
    """Format response for Step Functions.

    Step Functions expect specific output format for state transitions.
    """
    if is_error:
        return {
            "error": response_data.get("error", "Operation failed"),
            "statusCode": response_data.get("statusCode", 500),
            "message": response_data.get("message", "Step function operation failed"),
            "correlation_id": response_data.get("correlation_id"),
        }

    # Clean response for Step Functions (remove Lambda-specific fields)
    clean_response = {}
    
    # Include relevant data for Step Functions
    step_function_fields = [
        "batch_id",
        "processed_messages", 
        "stream_messages",
        "delta_messages",
        "metadata_updates",
        "label_updates",
        "processed_deltas",
        "skipped_deltas",
        "collection",
        "operation",
        "correlation_id",
    ]
    
    for field in step_function_fields:
        if field in response_data and response_data[field] is not None:
            clean_response[field] = response_data[field]
    
    # Always include status info
    clean_response["status"] = "success"
    clean_response["message"] = response_data.get("message", "Operation completed")
    
    return clean_response


def format_eventbridge_response(
    response_data: Dict[str, Any], is_error: bool
) -> Dict[str, Any]:
    """Format response for EventBridge-triggered Lambda."""
    # EventBridge doesn't use return values, so format for logging
    return {
        "status": "error" if is_error else "success",
        "message": response_data.get("message", "EventBridge processing completed"),
        "details": response_data,
    }


def format_api_response(response_data: Dict[str, Any], is_error: bool) -> Dict[str, Any]:
    """Format response for API Gateway or direct invocation."""
    status_code = response_data.get("statusCode", 500 if is_error else 200)
    
    # API Gateway format
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "X-Correlation-ID": response_data.get("correlation_id", ""),
        },
        "body": json.dumps({
            "message": response_data.get("message", "Operation completed"),
            "data": response_data,
            "error": response_data.get("error") if is_error else None,
        }),
    }


def create_success_response(
    message: str,
    data: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a standardized success response.

    Args:
        message: Success message
        data: Optional additional data
        correlation_id: Optional correlation ID

    Returns:
        Formatted success response
    """
    response = {
        "statusCode": 200,
        "message": message,
    }
    
    if data:
        response.update(data)
    
    if correlation_id:
        response["correlation_id"] = correlation_id
    
    return response


def create_error_response(
    message: str,
    error: Optional[str] = None,
    status_code: int = 500,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a standardized error response.

    Args:
        message: Error message
        error: Optional detailed error information
        status_code: HTTP status code
        correlation_id: Optional correlation ID

    Returns:
        Formatted error response
    """
    response = {
        "statusCode": status_code,
        "message": message,
    }
    
    if error:
        response["error"] = error
    
    if correlation_id:
        response["correlation_id"] = correlation_id
    
    return response