"""Utilities for enhancing step functions with error handling and monitoring."""

from typing import Any, Dict

import pulumi
from pulumi import Output


def add_error_handling_to_state(
    state_definition: Dict[str, Any],
    error_sns_topic_arn: Output[str] | str,
    state_name: str,
) -> Dict[str, Any]:
    """
    Add error handling to a step function state.

    Args:
        state_definition: The state definition dictionary
        error_sns_topic_arn: SNS topic ARN for error notifications
        state_name: Name of the state for error messages

    Returns:
        Modified state definition with error handling
    """
    # Add retry configuration
    if "Retry" not in state_definition:
        state_definition["Retry"] = [
            {
                "ErrorEquals": ["States.TaskFailed", "States.Timeout"],
                "IntervalSeconds": 2,
                "MaxAttempts": 3,
                "BackoffRate": 2.0,
            },
            {
                "ErrorEquals": ["States.ALL"],
                "IntervalSeconds": 5,
                "MaxAttempts": 2,
                "BackoffRate": 2.0,
            },
        ]

    # Add catch configuration to send to error handler
    if "Catch" not in state_definition:
        state_definition["Catch"] = [
            {
                "ErrorEquals": ["States.ALL"],
                "Next": f"{state_name}ErrorHandler",
                "ResultPath": "$.error",
            }
        ]

    return state_definition


def create_error_handler_state(
    state_name: str,
    error_sns_topic_arn: Output[str] | str,
    next_state: str = None,
) -> Dict[str, Any]:
    """
    Create an error handler state that sends notifications.

    Args:
        state_name: Name for the error handler state
        error_sns_topic_arn: SNS topic ARN for notifications
        next_state: Next state to transition to (defaults to Fail state)

    Returns:
        Error handler state definition
    """
    error_handler = {
        "Type": "Task",
        "Resource": "arn:aws:states:::sns:publish",
        "Parameters": {
            "TopicArn": error_sns_topic_arn,
            "Message.$": "$.error",
            "Subject": f"Step Function Error in {state_name}",
        },
        "ResultPath": "$.notificationResult",
        "Next": next_state or f"{state_name}Failed",
    }

    return error_handler


def create_fail_state(state_name: str) -> Dict[str, Any]:
    """
    Create a fail state for step function.

    Args:
        state_name: Name for the fail state

    Returns:
        Fail state definition
    """
    return {
        "Type": "Fail",
        "Error": f"{state_name}ExecutionFailed",
        "Cause": "Step function execution failed. Check CloudWatch logs for details.",
    }


def wrap_step_function_definition(
    definition: Dict[str, Any],
    error_sns_topic_arn: Output[str] | str,
    add_global_error_handling: bool = True,
) -> Dict[str, Any]:
    """
    Wrap an entire step function definition with error handling.

    Args:
        definition: The complete step function definition
        error_sns_topic_arn: SNS topic ARN for error notifications
        add_global_error_handling: Whether to add global error handling

    Returns:
        Modified definition with comprehensive error handling
    """
    states = definition.get("States", {})

    # Add error handling to each state
    for state_name, state_def in states.items():
        if state_def.get("Type") in ["Task", "Map", "Parallel"]:
            # Add error handling to the state
            states[state_name] = add_error_handling_to_state(
                state_def, error_sns_topic_arn, state_name
            )

            # Create corresponding error handler state
            error_handler_name = f"{state_name}ErrorHandler"
            if error_handler_name not in states:
                states[error_handler_name] = create_error_handler_state(
                    state_name, error_sns_topic_arn
                )

                # Create fail state
                fail_state_name = f"{state_name}Failed"
                if fail_state_name not in states:
                    states[fail_state_name] = create_fail_state(state_name)

    # Add global timeout if not present
    if "TimeoutSeconds" not in definition:
        definition["TimeoutSeconds"] = 3600  # 1 hour default timeout

    return definition


def create_retry_policy(
    error_types: list[str] = None,
    interval_seconds: int = 2,
    max_attempts: int = 3,
    backoff_rate: float = 2.0,
) -> Dict[str, Any]:
    """
    Create a retry policy for step function states.

    Args:
        error_types: List of error types to retry (defaults to common errors)
        interval_seconds: Initial retry interval
        max_attempts: Maximum number of retry attempts
        backoff_rate: Exponential backoff rate

    Returns:
        Retry policy configuration
    """
    if error_types is None:
        error_types = [
            "States.TaskFailed",
            "States.Timeout",
            "Lambda.ServiceException",
            "Lambda.AWSLambdaException",
        ]

    return {
        "ErrorEquals": error_types,
        "IntervalSeconds": interval_seconds,
        "MaxAttempts": max_attempts,
        "BackoffRate": backoff_rate,
    }
