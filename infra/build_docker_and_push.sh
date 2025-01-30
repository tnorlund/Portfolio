export REGION='us-east-1'
export ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text --region ${REGION})
export PULUMI_STACK_NAME=$(pulumi stack --show-name)

# Authenticate with ECR
aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

# Ensure ECR repository exists
aws ecr describe-repositories --repository-names cluster-ocr --region ${REGION} || \
aws ecr create-repository --repository-name cluster-ocr --region ${REGION}

# Build the Docker image
docker buildx build --platform=linux/amd64 --load -t cluster-ocr:${PULUMI_STACK_NAME} -f ingestion/Dockerfile .

# Tag the image for ECR
docker tag cluster-ocr:${PULUMI_STACK_NAME} ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/cluster-ocr:${PULUMI_STACK_NAME}

# Push the image to ECR
docker push ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/cluster-ocr:${PULUMI_STACK_NAME}

# Get the Lambda function name from Pulumi
export LAMBDA_FUNCTION_NAME=$(pulumi stack output cluster_lambda_function_name)

# Update the Lambda function with the new image
aws --no-cli-pager lambda update-function-code \
    --function-name "${LAMBDA_FUNCTION_NAME}" \
    --image-uri "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/cluster-ocr:${PULUMI_STACK_NAME}"