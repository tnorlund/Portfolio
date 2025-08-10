"""
Optimized Step Function configuration with enhanced patterns.

This example shows how to improve the existing step functions with:
1. Hybrid Express/Standard workflows
2. Enhanced Map state with distributed processing
3. Optimized retry policies for container reuse
4. Error handling patterns
"""

import json
from pulumi_aws.sfn import StateMachine
from pulumi import Output

def create_optimized_embedding_workflow(lambda_arns, role_arn, name, parent):
    """Create an optimized embedding workflow with all enhancements."""
    
    return StateMachine(
        f"{name}-optimized-embedding-sm",
        role_arn=role_arn,
        # Can set type to EXPRESS for sub-workflows under 5 minutes
        type="STANDARD",  # Keep STANDARD for main workflow
        definition=Output.all(*lambda_arns).apply(
            lambda arns: json.dumps({
                "Comment": "Optimized embedding pipeline with enhanced patterns",
                "StartAt": "CheckBatchSize",
                "States": {
                    # Pre-processing to determine optimal strategy
                    "CheckBatchSize": {
                        "Type": "Task",
                        "Resource": arns[0],  # list_pending_lambda
                        "ResultPath": "$.batch_info",
                        "Next": "RouteBySize",
                        # Optimized retry for container reuse
                        "Retry": [
                            {
                                "ErrorEquals": ["Lambda.TooManyRequestsException"],
                                "IntervalSeconds": 0.5,  # Fast retry keeps container warm
                                "MaxAttempts": 2,
                                "BackoffRate": 1.2,
                                "JitterStrategy": "FULL"
                            }
                        ],
                        "Catch": [
                            {
                                "ErrorEquals": ["States.ALL"],
                                "Next": "HandleError",
                                "ResultPath": "$.error"
                            }
                        ]
                    },
                    
                    # Dynamic routing based on workload
                    "RouteBySize": {
                        "Type": "Choice",
                        "Choices": [
                            {
                                # Small batches: Use standard Map
                                "Variable": "$.batch_info.total_items",
                                "NumericLessThan": 100,
                                "Next": "ProcessSmallBatch"
                            },
                            {
                                # Medium batches: Use Map with batching
                                "Variable": "$.batch_info.total_items",
                                "NumericLessThan": 1000,
                                "Next": "ProcessMediumBatch"
                            },
                            {
                                # Large batches: Use Distributed Map
                                "Variable": "$.batch_info.total_items",
                                "NumericGreaterThanEquals": 1000,
                                "Next": "ProcessLargeBatch"
                            }
                        ],
                        "Default": "ProcessSmallBatch"
                    },
                    
                    # Small batch processing (< 100 items)
                    "ProcessSmallBatch": {
                        "Type": "Map",
                        "ItemsPath": "$.batch_info.items",
                        "MaxConcurrency": 10,
                        "Parameters": {
                            "batch.$": "$$.Map.Item.Value",
                            "index.$": "$$.Map.Item.Index",
                            "total.$": "$$.Map.Item.Size"
                        },
                        "Iterator": {
                            "StartAt": "ProcessSingleItem",
                            "States": {
                                "ProcessSingleItem": {
                                    "Type": "Task",
                                    "Resource": arns[1],  # line_polling_lambda
                                    "End": True,
                                    "Retry": [
                                        {
                                            "ErrorEquals": ["RateLimitError"],
                                            "IntervalSeconds": 5,
                                            "MaxAttempts": 5,
                                            "BackoffRate": 2,
                                            "MaxDelaySeconds": 30
                                        },
                                        {
                                            "ErrorEquals": ["States.TaskFailed"],
                                            "IntervalSeconds": 1,
                                            "MaxAttempts": 3,
                                            "BackoffRate": 1.5
                                        }
                                    ]
                                }
                            }
                        },
                        "ResultPath": "$.processed_items",
                        "Next": "CompactResults"
                    },
                    
                    # Medium batch processing (100-1000 items)
                    "ProcessMediumBatch": {
                        "Type": "Map",
                        "ItemsPath": "$.batch_info.items",
                        "MaxConcurrency": 25,
                        "ItemBatcher": {
                            "MaxBatchSize": 10,  # Process 10 items per Lambda
                            "MaxInputBytesPerBatch": 256000
                        },
                        "Parameters": {
                            "batched_items.$": "$$.Map.Item.Value",
                            "batch_index.$": "$$.Map.Item.Index"
                        },
                        "Iterator": {
                            "StartAt": "ProcessBatch",
                            "States": {
                                "ProcessBatch": {
                                    "Type": "Task",
                                    "Resource": arns[1],  # line_polling_lambda
                                    "End": True,
                                    "TimeoutSeconds": 300,  # 5 minute timeout
                                    "HeartbeatSeconds": 30,  # Health check every 30s
                                    "Retry": [
                                        {
                                            "ErrorEquals": ["Lambda.Unknown"],
                                            "IntervalSeconds": 0.5,
                                            "MaxAttempts": 2,
                                            "BackoffRate": 1.1
                                        }
                                    ]
                                }
                            }
                        },
                        "ResultPath": "$.processed_batches",
                        "Next": "CompactResults",
                        "ToleratedFailurePercentage": 5  # Allow 5% failure
                    },
                    
                    # Large batch processing (1000+ items) - Distributed Map
                    "ProcessLargeBatch": {
                        "Type": "Map",
                        "ItemProcessor": {
                            "ProcessorConfig": {
                                "Mode": "DISTRIBUTED",
                                "ExecutionType": "EXPRESS"  # Express for each item
                            },
                            "StartAt": "DistributedProcess",
                            "States": {
                                "DistributedProcess": {
                                    "Type": "Task",
                                    "Resource": arns[1],  # line_polling_lambda
                                    "End": True,
                                    "Retry": [
                                        {
                                            "ErrorEquals": ["States.ALL"],
                                            "IntervalSeconds": 2,
                                            "MaxAttempts": 3,
                                            "BackoffRate": 2
                                        }
                                    ]
                                }
                            }
                        },
                        "MaxConcurrency": 100,  # High concurrency for distributed
                        "Label": "DistributedProcessing",
                        "ItemReader": {
                            "Resource": "arn:aws:states:::dynamodb:getItem",
                            "Parameters": {
                                "TableName": "ReceiptsTable",
                                "ConsistentRead": false
                            }
                        },
                        "ResultWriter": {
                            "Resource": "arn:aws:states:::s3:putObject",
                            "Parameters": {
                                "Bucket": "embedding-results",
                                "Prefix": "distributed-output"
                            }
                        },
                        "ToleratedFailurePercentage": 10,
                        "Next": "CompactResults"
                    },
                    
                    # Compaction with optimized chunking
                    "CompactResults": {
                        "Type": "Task",
                        "Resource": arns[2],  # compaction_lambda
                        "Parameters": {
                            "operation": "compact",
                            "source_path.$": "$.processed_items",
                            "optimize_for": "query_performance"
                        },
                        "TimeoutSeconds": 900,  # 15 minutes
                        "HeartbeatSeconds": 60,  # Heartbeat every minute
                        "Retry": [
                            {
                                # Quick retry for transient errors
                                "ErrorEquals": ["Lambda.ServiceException"],
                                "IntervalSeconds": 1,
                                "MaxAttempts": 2,
                                "BackoffRate": 1.5
                            },
                            {
                                # Slower retry for resource errors
                                "ErrorEquals": ["ResourceNotFoundException"],
                                "IntervalSeconds": 5,
                                "MaxAttempts": 3,
                                "BackoffRate": 2
                            }
                        ],
                        "Catch": [
                            {
                                "ErrorEquals": ["CompactionError"],
                                "Next": "CompactionFallback",
                                "ResultPath": "$.compaction_error"
                            }
                        ],
                        "Next": "Success"
                    },
                    
                    # Fallback for compaction failures
                    "CompactionFallback": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
                        "Parameters": {
                            "FunctionName": arns[2],  # compaction_lambda
                            "Payload": {
                                "operation": "incremental_compact",
                                "error.$": "$.compaction_error",
                                "token.$": "$$.Task.Token"
                            }
                        },
                        "Next": "Success",
                        "TimeoutSeconds": 1800  # 30 minutes for recovery
                    },
                    
                    # Error handling state
                    "HandleError": {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::sns:publish",
                        "Parameters": {
                            "TopicArn": "arn:aws:sns:us-east-1:123456789012:embedding-errors",
                            "Message.$": "$.error",
                            "Subject": "Embedding Pipeline Error"
                        },
                        "Next": "Failed"
                    },
                    
                    "Success": {
                        "Type": "Succeed"
                    },
                    
                    "Failed": {
                        "Type": "Fail",
                        "Cause": "Pipeline failed after retries"
                    }
                }
            })
        )
    )


def create_express_subworkflow(lambda_arn, role_arn, name, parent):
    """Create an Express workflow for high-volume, short tasks."""
    
    return StateMachine(
        f"{name}-express-validator-sm",
        role_arn=role_arn,
        type="EXPRESS",  # Express for cost savings
        definition=json.dumps({
            "Comment": "Fast validation workflow using Express",
            "StartAt": "ValidateBatch",
            "States": {
                "ValidateBatch": {
                    "Type": "Task",
                    "Resource": lambda_arn,
                    "End": True,
                    "TimeoutSeconds": 60,  # 1 minute max
                    "Retry": [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "IntervalSeconds": 0.5,
                            "MaxAttempts": 2,
                            "BackoffRate": 1.5
                        }
                    ]
                }
            }
        })
    )


# Cost optimization settings for Pulumi
STEP_FUNCTION_OPTIMIZATIONS = {
    "standard_workflow": {
        "logging_level": "ERROR",  # Reduce CloudWatch costs
        "tracing_enabled": False,   # Disable X-Ray for cost
        "include_execution_data": False  # Minimize stored data
    },
    "express_workflow": {
        "logging_level": "OFF",  # No logging for Express
        "type": "EXPRESS"
    },
    "distributed_map": {
        "max_concurrency": 1000,
        "tolerated_failure_percentage": 10,
        "item_batcher": {
            "max_batch_size": 25,
            "max_input_bytes": 262144  # 256KB
        }
    }
}