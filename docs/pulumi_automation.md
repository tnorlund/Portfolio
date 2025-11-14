Excellent point! The real cost killer isn't just the NAT Gateway - it's the persistent
infrastructure (VPC endpoints, EC2 instances, EFS, etc.) running 24/7 when you're not training.

🎯 Scale-to-Zero Solutions with Pulumi

Option A: Pulumi Automation API (Recommended)

Create/destroy entire training stack on-demand:

# training_stack_manager.py

import pulumi
from pulumi import automation as auto

class TrainingStackManager:
def **init**(self):
self.stack_name = "training-infrastructure"
self.project_name = "receipt-trainer"

      async def deploy_training_infrastructure(self, job_config):
          """Deploy training stack when needed"""

          def pulumi_program():
              # Create VPC + endpoints only when training
              vpc = aws.ec2.Vpc("training-vpc", cidr_block="10.0.0.0/16")

              # VPC Endpoints (only exist during training)
              s3_endpoint = aws.ec2.VpcEndpoint("s3",
                  vpc_id=vpc.id,
                  service_name="com.amazonaws.us-east-1.s3"
              )

              # Training instances
              training_instances = create_training_cluster(job_config)

              return {
                  "vpc_id": vpc.id,
                  "instance_ids": [i.id for i in training_instances]
              }

          # Deploy stack
          stack = auto.create_or_select_stack(
              stack_name=self.stack_name,
              project_name=self.project_name,
              program=pulumi_program
          )

          result = await stack.up()
          return result.outputs

      async def destroy_training_infrastructure(self):
          """Tear down everything when training completes"""
          stack = auto.select_stack(
              stack_name=self.stack_name,
              project_name=self.project_name
          )

          await stack.destroy()
          return True

Option B: Serverless Training (Ultimate Scale-to-Zero)

Use AWS Batch + Fargate - only pay when jobs run:

# serverless_training.py

training_job_definition = aws.batch.JobDefinition("training-job",
type="container",
platform_capabilities=["FARGATE"],
container_properties={
"image": "your-account.dkr.ecr.region.amazonaws.com/receipt-trainer:latest",
"vcpus": 4,
"memory": 8192,
"jobRoleArn": training_role.arn,
"executionRoleArn": execution_role.arn,
"resourceRequirements": [{
"type": "GPU",
"value": "1"
}]
}
)

# Batch compute environment - scales to 0

compute_environment = aws.batch.ComputeEnvironment("training-compute",
type="MANAGED",
state="ENABLED",
compute_resources={
"type": "FARGATE",
"maxVcpus": 256,
"minVcpus": 0, # Scale to zero!
}
)

Option C: Spot Fleet Auto-Scaling

Keep minimal infrastructure, scale instances to 0:

# minimal_persistent_infra.py

# Only keep the bare minimum running 24/7

persistent_resources = [
dynamodb_table, # ~$1/month
s3_bucket, # ~$1/month
sqs_queue, # ~$0/month
]

# Everything else scales to 0

training_asg = aws.autoscaling.Group("training-asg",
min_size=0, # Scale to zero
max_size=10,
desired_capacity=0, # Start at zero

      # Scale up trigger
      tags=[{
          "key": "TrainingActive",
          "value": "false",
          "propagate_at_launch": True
      }]

)

🚀 Complete Scale-to-Zero Workflow

# receipt_trainer/infrastructure/on_demand.py

class OnDemandTrainingOrchestrator:

      async def start_training_job(self, model_config):
          """Complete training workflow with infrastructure provisioning"""

          # 1. Deploy infrastructure (5-10 minutes)
          print("🏗️  Deploying training infrastructure...")
          infra = await self.deploy_training_stack(model_config)

          try:
              # 2. Submit training job
              print("🚀 Starting training job...")
              job_id = await self.submit_training_job(infra, model_config)

              # 3. Monitor training
              print("📊 Monitoring training progress...")
              result = await self.monitor_job(job_id)

              # 4. Save model artifacts
              print("💾 Saving model artifacts...")
              await self.save_model(job_id, result)

              return result

          finally:
              # 5. Always tear down infrastructure
              print("🔥 Tearing down infrastructure...")
              await self.destroy_training_stack()

      async def deploy_training_stack(self, config):
          """Deploy VPC + endpoints + instances on-demand"""
          return await TrainingStackManager().deploy_training_infrastructure(config)

      async def destroy_training_stack(self):
          """Destroy everything to avoid ongoing costs"""
          return await TrainingStackManager().destroy_training_infrastructure()

💰 Cost Comparison

| Approach         | When Idle  | When Training (4 hours) | Monthly (2 trainings) |
| ---------------- | ---------- | ----------------------- | --------------------- |
| Always-On VPC    | $120/month | $120 + compute          | $120 + compute        |
| On-Demand Stack  | $3/month\* | $3 + compute            | $6 + compute          |
| Serverless Batch | $0/month   | Compute only            | Compute only          |
| Spot Auto-Scale  | $3/month\* | $3 + compute            | $6 + compute          |

\*Only DynamoDB + S3 + minimal monitoring

🎯 Recommended Implementation

Hybrid: On-Demand Pulumi + Serverless Monitoring

# Keep minimal 24/7 infrastructure

permanent_stack = {
"dynamodb_table": receipts_table,
"s3_buckets": [model_artifacts, training_data],
"lambda_monitor": training_monitor_lambda, # Watches for jobs
}

# Deploy on-demand when training needed

temporary_stack = {
"vpc": training_vpc,
"vpc_endpoints": essential_endpoints,
"training_cluster": gpu_instances,
"efs": checkpoint_storage,
}

# Trigger via:

# 1. Manual: `receipt-cli train --model layoutlm --config config.yaml`

# 2. Scheduled: CloudWatch Event -> Lambda -> Pulumi deploy

# 3. API: REST endpoint that triggers deployment

🔧 Implementation Plan

1. Refactor existing receipt_trainer to support on-demand deployment
2. Create Pulumi Automation API wrapper for infrastructure
3. Build simple CLI/API trigger system
4. Test end-to-end workflow with small training job

This approach gives you true scale-to-zero - only paying for compute when actively training, while
maintaining the sophisticated job management and fault tolerance of the original system.
