from typing import Optional

import pulumi
import pulumi_aws as aws


class ChromaSecurity(pulumi.ComponentResource):
    """Security resources for Lambda ↔ Chroma and orchestration roles.

    Creates:
    - sg-lambda: no ingress, egress all
    - sg-chroma: ingress tcp/8000 from sg-lambda, egress all
    - ecs-task-role: assume by ECS tasks; basic exec policy attached
    - lambda-execution-role: basic execution
    - step-functions-role: minimal permissions for ECS update and logs
    """

    def __init__(
        self,
        name: str,
        *,
        vpc_id: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:security:ChromaSecurity", name, {}, opts)

        # Security Groups
        self.sg_lambda = aws.ec2.SecurityGroup(
            f"{name}-sg-lambda",
            vpc_id=vpc_id,
            description="Lambda egress-only security group",
            ingress=[],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={
                "Name": f"{name}-sg-lambda",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.sg_chroma = aws.ec2.SecurityGroup(
            f"{name}-sg-chroma",
            vpc_id=vpc_id,
            description="Chroma ingress from Lambda only",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=8000,
                    to_port=8000,
                    description="Allow Chroma HTTP from Lambda SG",
                    security_groups=[self.sg_lambda.id],
                )
            ],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={
                "Name": f"{name}-sg-chroma",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        # IAM Roles
        self.ecs_task_role = aws.iam.Role(
            f"{name}-ecs-task-role",
            assume_role_policy="""{
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }""",
            tags={"ManagedBy": "Pulumi", "Environment": pulumi.get_stack()},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Execution role for pulling from ECR, writing logs
        aws.iam.RolePolicyAttachment(
            f"{name}-ecs-exec-policy",
            role=self.ecs_task_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.lambda_execution_role = aws.iam.Role(
            f"{name}-lambda-execution-role",
            assume_role_policy="""{
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }""",
            tags={"ManagedBy": "Pulumi", "Environment": pulumi.get_stack()},
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-lambda-basic-exec",
            role=self.lambda_execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-lambda-vpc-access",
            role=self.lambda_execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.step_functions_role = aws.iam.Role(
            f"{name}-step-functions-role",
            assume_role_policy="""{
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "states.amazonaws.com"},
                    "Action": "sts:AssumeRole"
                }]
            }""",
            tags={"ManagedBy": "Pulumi", "Environment": pulumi.get_stack()},
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Minimal inline policy for ECS updates and CloudWatch logs
        aws.iam.RolePolicy(
            f"{name}-sfn-inline",
            role=self.step_functions_role.id,
            policy=pulumi.Output.from_input({}).apply(
                lambda _: (
                    '{"Version":"2012-10-17","Statement":['
                    '{"Effect":"Allow","Action":["ecs:UpdateService","ecs:DescribeServices","ecs:DescribeTasks","ecs:ListTasks"],"Resource":"*"},'
                    '{"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"}'
                    "]}"
                )
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Outputs
        self.sg_lambda_id = self.sg_lambda.id
        self.sg_chroma_id = self.sg_chroma.id
        self.ecs_task_role_arn = self.ecs_task_role.arn
        self.lambda_role_arn = self.lambda_execution_role.arn
        self.step_functions_role_arn = self.step_functions_role.arn
        # Also expose names for resources that require role name (not ARN)
        self.ecs_task_role_name = self.ecs_task_role.name
        self.lambda_role_name = self.lambda_execution_role.name
        self.step_functions_role_name = self.step_functions_role.name

        self.register_outputs(
            {
                "sg_lambda_id": self.sg_lambda_id,
                "sg_chroma_id": self.sg_chroma_id,
                "ecs_task_role_arn": self.ecs_task_role_arn,
                "ecs_task_role_name": self.ecs_task_role_name,
                "lambda_role_arn": self.lambda_role_arn,
                "lambda_role_name": self.lambda_role_name,
                "step_functions_role_arn": self.step_functions_role_arn,
                "step_functions_role_name": self.step_functions_role_name,
            }
        )
