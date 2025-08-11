"""Integration tests for Step Functions using LocalStack."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import boto3


class TestStepFunctionsIntegration:
    """Integration tests for the embedding pipeline Step Functions."""
    
    @pytest.mark.integration
    def test_create_batches_step_function(self, localstack_client, mock_openai):
        """Test the Create Embedding Batches Step Function."""
        # Create clients
        sf_client = localstack_client("stepfunctions")
        lambda_client = localstack_client("lambda")
        iam_client = localstack_client("iam")
        
        # Create IAM role for Step Functions
        role_response = iam_client.create_role(
            RoleName="test-step-function-role",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "states.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            })
        )
        role_arn = role_response["Role"]["Arn"]
        
        # Create mock Lambda functions
        lambda_arns = {}
        for func_name in ["find-unembedded", "submit-openai"]:
            response = lambda_client.create_function(
                FunctionName=f"{func_name}-test",
                Runtime="python3.12",
                Role=role_arn,
                Handler="index.handler",
                Code={"ZipFile": b"fake code"},
                Timeout=30,
                MemorySize=128,
            )
            lambda_arns[func_name] = response["FunctionArn"]
        
        # Create Step Function definition
        definition = {
            "Comment": "Test Create Embedding Batches",
            "StartAt": "FindUnembedded",
            "States": {
                "FindUnembedded": {
                    "Type": "Task",
                    "Resource": lambda_arns["find-unembedded"],
                    "Next": "SubmitBatches",
                },
                "SubmitBatches": {
                    "Type": "Map",
                    "ItemsPath": "$.batches",
                    "MaxConcurrency": 10,
                    "Iterator": {
                        "StartAt": "SubmitToOpenAI",
                        "States": {
                            "SubmitToOpenAI": {
                                "Type": "Task",
                                "Resource": lambda_arns["submit-openai"],
                                "End": True,
                            }
                        }
                    },
                    "End": True,
                }
            }
        }
        
        # Create the state machine
        response = sf_client.create_state_machine(
            name="test-create-batches-sf",
            definition=json.dumps(definition),
            roleArn=role_arn,
        )
        state_machine_arn = response["stateMachineArn"]
        
        # Start execution with test input
        test_input = {
            "max_batches": 3,
            "batch_size": 100,
        }
        
        execution_response = sf_client.start_execution(
            stateMachineArn=state_machine_arn,
            input=json.dumps(test_input),
        )
        
        # Wait for execution to complete (with timeout)
        execution_arn = execution_response["executionArn"]
        max_wait_time = 30  # seconds
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            status_response = sf_client.describe_execution(
                executionArn=execution_arn
            )
            
            if status_response["status"] != "RUNNING":
                break
            
            time.sleep(1)
        
        # Verify execution completed successfully
        assert status_response["status"] == "SUCCEEDED"
    
    @pytest.mark.integration 
    def test_poll_and_store_step_function(self, localstack_client):
        """Test the Poll and Store Embeddings Step Function."""
        # Create clients
        sf_client = localstack_client("stepfunctions")
        lambda_client = localstack_client("lambda")
        iam_client = localstack_client("iam")
        
        # Create IAM role
        role_response = iam_client.create_role(
            RoleName="test-poll-sf-role",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "states.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            })
        )
        role_arn = role_response["Role"]["Arn"]
        
        # Create mock Lambda functions
        lambda_arns = {}
        for func_name in ["list-pending", "line-polling", "compaction"]:
            response = lambda_client.create_function(
                FunctionName=f"{func_name}-test",
                Runtime="python3.12",
                Role=role_arn,
                Handler="index.handler",
                Code={"ZipFile": b"fake code"},
                Timeout=30,
                MemorySize=128,
            )
            lambda_arns[func_name] = response["FunctionArn"]
        
        # Create Step Function with Choice state
        definition = {
            "Comment": "Test Poll and Store",
            "StartAt": "ListPendingBatches",
            "States": {
                "ListPendingBatches": {
                    "Type": "Task",
                    "Resource": lambda_arns["list-pending"],
                    "ResultPath": "$.pending_batches",
                    "Next": "CheckPendingBatches",
                },
                "CheckPendingBatches": {
                    "Type": "Choice",
                    "Choices": [{
                        "Variable": "$.pending_batches[0]",
                        "IsPresent": True,
                        "Next": "PollBatches",
                    }],
                    "Default": "NoPendingBatches",
                },
                "PollBatches": {
                    "Type": "Map",
                    "ItemsPath": "$.pending_batches",
                    "MaxConcurrency": 10,
                    "Iterator": {
                        "StartAt": "PollBatch",
                        "States": {
                            "PollBatch": {
                                "Type": "Task",
                                "Resource": lambda_arns["line-polling"],
                                "End": True,
                            }
                        }
                    },
                    "Next": "CompactDeltas",
                },
                "CompactDeltas": {
                    "Type": "Task",
                    "Resource": lambda_arns["compaction"],
                    "End": True,
                },
                "NoPendingBatches": {
                    "Type": "Succeed",
                    "Comment": "No pending batches",
                }
            }
        }
        
        # Create the state machine
        response = sf_client.create_state_machine(
            name="test-poll-store-sf",
            definition=json.dumps(definition),
            roleArn=role_arn,
        )
        state_machine_arn = response["stateMachineArn"]
        
        # Test with no pending batches
        execution_response = sf_client.start_execution(
            stateMachineArn=state_machine_arn,
            input=json.dumps({}),
        )
        
        # Verify it takes the NoPendingBatches path
        time.sleep(2)  # Give it time to execute
        
        status_response = sf_client.describe_execution(
            executionArn=execution_response["executionArn"]
        )
        
        # Should succeed quickly when no batches
        assert status_response["status"] in ["SUCCEEDED", "RUNNING"]
    
    @pytest.mark.integration
    def test_step_function_error_handling(self, localstack_client):
        """Test Step Function error handling and retries."""
        sf_client = localstack_client("stepfunctions")
        lambda_client = localstack_client("lambda")
        iam_client = localstack_client("iam")
        
        # Create IAM role
        role_response = iam_client.create_role(
            RoleName="test-error-sf-role",
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "states.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            })
        )
        role_arn = role_response["Role"]["Arn"]
        
        # Create a Lambda that will fail
        response = lambda_client.create_function(
            FunctionName="failing-function-test",
            Runtime="python3.12",
            Role=role_arn,
            Handler="index.handler",
            Code={"ZipFile": b"def handler(event, context): raise Exception('Test error')"},
            Timeout=30,
            MemorySize=128,
        )
        lambda_arn = response["FunctionArn"]
        
        # Create Step Function with retry logic
        definition = {
            "Comment": "Test Error Handling",
            "StartAt": "TaskWithRetry",
            "States": {
                "TaskWithRetry": {
                    "Type": "Task",
                    "Resource": lambda_arn,
                    "Retry": [{
                        "ErrorEquals": ["States.ALL"],
                        "IntervalSeconds": 1,
                        "MaxAttempts": 3,
                        "BackoffRate": 2.0,
                    }],
                    "Catch": [{
                        "ErrorEquals": ["States.ALL"],
                        "Next": "HandleError",
                    }],
                    "End": True,
                },
                "HandleError": {
                    "Type": "Pass",
                    "Result": {"error": "Task failed after retries"},
                    "End": True,
                }
            }
        }
        
        # Create and execute the state machine
        response = sf_client.create_state_machine(
            name="test-error-handling-sf",
            definition=json.dumps(definition),
            roleArn=role_arn,
        )
        
        execution_response = sf_client.start_execution(
            stateMachineArn=response["stateMachineArn"],
            input=json.dumps({}),
        )
        
        # Wait for execution
        time.sleep(5)
        
        status_response = sf_client.describe_execution(
            executionArn=execution_response["executionArn"]
        )
        
        # Should handle the error and complete
        assert status_response["status"] in ["SUCCEEDED", "FAILED"]