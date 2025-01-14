

### ECR
```bash
export REGION='us-east-2'
export ACCOUNT_ID=`aws sts get-caller-identity --query "Account" --output text --region ${REGION}`
export PULUMI_STACK_NAME=`pulumi stack --show-name`
if ! aws ecr describe-repositories --repository-names cluster-ocr > /dev/null 2>&1; then
    aws ecr create-repository --repository-name cluster-ocr
fi
docker buildx build --platform=linux/amd64 -t cluster-ocr -f ingestion/Dockerfile .
docker tag cluster-ocr:${PULUMI_STACK_NAME} ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/cluster-ocr:${PULUMI_STACK_NAME}
docker push ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/cluster-ocr:${PULUMI_STACK_NAME}
```