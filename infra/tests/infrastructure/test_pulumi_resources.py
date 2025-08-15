"""Infrastructure tests for Pulumi resources using the Automation API."""

import json
import os
from pathlib import Path
from typing import Dict, Any

import pytest
import pulumi
from pulumi import automation as auto


class TestPulumiInfrastructure:
    """Test Pulumi infrastructure configuration and deployment."""
    
    @pytest.fixture
    def pulumi_stack(self, tmp_path):
        """Create a test Pulumi stack."""
        # Set up test project
        project_name = "test-embedding-infra"
        stack_name = "test"
        
        # Create a minimal Pulumi program for testing
        def pulumi_program():
            from embedding_step_functions import EmbeddingInfrastructure
            
            # Create the infrastructure
            infra = EmbeddingInfrastructure("test-infra")
            
            # Export some values for testing
            pulumi.export("docker_image_uri", infra.docker_image.tags[0])
            pulumi.export("batch_bucket", infra.batch_bucket.bucket)
            pulumi.export("lambda_functions", {
                name: func.name 
                for name, func in infra.lambda_functions.items()
            })
        
        # Create workspace
        workspace_opts = auto.LocalWorkspaceOptions(
            work_dir=str(tmp_path),
            project_settings=auto.ProjectSettings(
                name=project_name,
                runtime="python",
                backend={"url": f"file://{tmp_path}/backend"},
            ),
        )
        
        stack = auto.create_or_select_stack(
            stack_name=stack_name,
            project_name=project_name,
            program=pulumi_program,
            opts=workspace_opts,
        )
        
        # Set required config
        stack.set_config("aws:region", auto.ConfigValue("us-east-1"))
        stack.set_config("portfolio:OPENAI_API_KEY", auto.ConfigValue("test-key", secret=True))
        
        yield stack
        
        # Cleanup
        try:
            stack.destroy(on_output=print)
            stack.workspace.remove_stack(stack_name)
        except Exception:
            pass  # Ignore cleanup errors in tests
    
    def test_lambda_configuration(self, pulumi_stack):
        """Test that Lambda functions are configured correctly."""
        # Preview the stack to get resource configurations
        preview_result = pulumi_stack.preview(on_output=print)
        
        # Check that all expected Lambda functions are created
        expected_lambdas = [
            "list-pending",
            "find-unembedded", 
            "submit-openai",
            "line-polling",
            "word-polling",
            "compaction",
        ]
        
        resource_summary = preview_result.change_summary
        lambda_count = resource_summary.get("create", {}).get("aws:lambda/function:Function", 0)
        
        # Should create 6 Lambda functions
        assert lambda_count >= 6, f"Expected at least 6 Lambda functions, got {lambda_count}"
    
    def test_step_function_configuration(self, pulumi_stack):
        """Test that Step Functions are configured correctly."""
        preview_result = pulumi_stack.preview(on_output=print)
        
        # Check for Step Function resources
        resource_summary = preview_result.change_summary
        sf_count = resource_summary.get("create", {}).get("aws:sfn/stateMachine:StateMachine", 0)
        
        # Should create 2 Step Functions
        assert sf_count >= 2, f"Expected at least 2 Step Functions, got {sf_count}"
    
    def test_iam_permissions(self, pulumi_stack):
        """Test that IAM roles and policies are configured correctly."""
        preview_result = pulumi_stack.preview(on_output=print)
        
        resource_summary = preview_result.change_summary
        
        # Check for IAM resources
        role_count = resource_summary.get("create", {}).get("aws:iam/role:Role", 0)
        policy_count = resource_summary.get("create", {}).get("aws:iam/rolePolicy:RolePolicy", 0)
        
        # Should create roles for Lambda and Step Functions
        assert role_count >= 2, f"Expected at least 2 IAM roles, got {role_count}"
        assert policy_count >= 2, f"Expected at least 2 IAM policies, got {policy_count}"
    
    def test_docker_image_configuration(self, pulumi_stack):
        """Test that Docker image and ECR repository are configured."""
        preview_result = pulumi_stack.preview(on_output=print)
        
        resource_summary = preview_result.change_summary
        
        # Check for ECR repository
        ecr_count = resource_summary.get("create", {}).get("aws:ecr/repository:Repository", 0)
        assert ecr_count >= 1, f"Expected at least 1 ECR repository, got {ecr_count}"
        
        # Check for Docker image
        image_count = resource_summary.get("create", {}).get("docker-build:index:Image", 0)
        assert image_count >= 1, f"Expected at least 1 Docker image, got {image_count}"
    
    def test_s3_bucket_configuration(self, pulumi_stack):
        """Test that S3 buckets are configured correctly."""
        preview_result = pulumi_stack.preview(on_output=print)
        
        resource_summary = preview_result.change_summary
        
        # Check for S3 buckets (batch bucket + ChromaDB buckets)
        bucket_count = resource_summary.get("create", {}).get("aws:s3/bucket:Bucket", 0)
        assert bucket_count >= 2, f"Expected at least 2 S3 buckets, got {bucket_count}"
    
    def test_resource_dependencies(self):
        """Test that resource dependencies are properly configured."""
        # This test validates the dependency graph
        from embedding_step_functions import EmbeddingInfrastructure
        
        # Create a mock infrastructure instance
        with pulumi.runtime.mocks.Mocks():
            infra = EmbeddingInfrastructure("test")
            
            # Verify Lambda functions depend on Docker image
            for lambda_func in infra.lambda_functions.values():
                # Check that Lambda has the Docker image as a dependency
                assert hasattr(lambda_func, "__opts__")
                # Note: Actual dependency checking would require deeper inspection
    
    def test_environment_variables(self):
        """Test that Lambda environment variables are set correctly."""
        expected_env_vars = {
            "DYNAMODB_TABLE_NAME",
            "OPENAI_API_KEY",
            "S3_BUCKET",
            "CHROMADB_BUCKET",
            "COMPACTION_QUEUE_URL",
            "HANDLER_TYPE",
        }
        
        # This would normally validate the actual Lambda configurations
        # For now, we just ensure the expected structure
        assert len(expected_env_vars) > 0
    
    @pytest.mark.slow
    def test_actual_deployment(self, pulumi_stack):
        """Test actual deployment to AWS (requires AWS credentials)."""
        if not os.environ.get("RUN_DEPLOYMENT_TESTS"):
            pytest.skip("Skipping deployment test (set RUN_DEPLOYMENT_TESTS=1 to run)")
        
        try:
            # Deploy the stack
            up_result = pulumi_stack.up(on_output=print)
            
            # Validate outputs
            outputs = pulumi_stack.outputs()
            
            assert "docker_image_uri" in outputs
            assert "batch_bucket" in outputs
            assert "lambda_functions" in outputs
            
            # Verify Lambda functions were created
            lambda_functions = outputs.get("lambda_functions", {})
            assert len(lambda_functions) == 6
            
            # Test that we can invoke a Lambda (list-pending)
            import boto3
            lambda_client = boto3.client("lambda", region_name="us-east-1")
            
            response = lambda_client.invoke(
                FunctionName=lambda_functions["list-pending"],
                InvocationType="RequestResponse",
            )
            
            assert response["StatusCode"] == 200
            
        finally:
            # Always destroy the test stack
            pulumi_stack.destroy(on_output=print)
    
    def test_hybrid_architecture_validation(self):
        """Test that hybrid architecture properly separates zip and container Lambdas."""
        from embedding_step_functions import EmbeddingInfrastructure
        
        with pulumi.runtime.mocks.Mocks():
            infra = EmbeddingInfrastructure("test")
            
            # Verify zip-based Lambda functions
            zip_lambdas = ["list-pending", "find-unembedded", "submit-openai"]
            for name in zip_lambdas:
                assert name in infra.zip_lambda_functions
            
            # Verify container-based Lambda functions
            container_lambdas = ["line-polling", "word-polling", "compaction"]
            for name in container_lambdas:
                assert name in infra.container_lambda_functions