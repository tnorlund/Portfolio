# Use AWS Lambda's Python base image
FROM public.ecr.aws/lambda/python:3.12

# First, install any OS-level/Python packages needed globally
RUN pip install --no-cache-dir opencv-python-headless scikit-learn

# Copy the "python" folder (which includes your requirements.txt) into the image
COPY lambda_layer/python ./dynamo

# Install dependencies from requirements.txt
# This step will install everything listed in lambda_layer/python/requirements.txt
RUN pip install --no-cache-dir -r ./dynamo/requirements.txt

# (Optional) If you have a setup.py in `dynamo` and want to install it as a package:
RUN pip install --no-cache-dir ./dynamo

# Set the working directory to Lambda's default task root
WORKDIR ${LAMBDA_TASK_ROOT}

# Copy your ingestion code into the container
COPY ingestion/cluster.py ${LAMBDA_TASK_ROOT}

# The default command will be your handler
CMD [ "cluster.lambda_handler" ]