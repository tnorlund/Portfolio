# Use AWS Lambda's Python base image
FROM public.ecr.aws/lambda/python:3.12

# Install OpenCV and scikit-learn
RUN pip install --no-cache-dir opencv-python-headless scikit-learn

COPY lambda_layer/python ./dynamo
RUN pip install ./dynamo

# Set the working directory to Lambda's default task root
WORKDIR ${LAMBDA_TASK_ROOT}

# Copy your application code
COPY ingestion/cluster.py ${LAMBDA_TASK_ROOT}

# Command for the Lambda function
CMD ["cluster.lambda_handler"]
