# Use AWS Lambda's Python base image
FROM public.ecr.aws/lambda/python:3.12

# Install OpenCV and scikit-learn
RUN pip install --no-cache-dir opencv-python-headless scikit-learn

# Copy your application code
COPY cluster.py ${LAMBDA_TASK_ROOT}

# Command for the Lambda function
CMD ["cluster.lambda_handler"]
