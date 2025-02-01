FROM public.ecr.aws/lambda/python:3.13

# Install system-level dependencies
RUN pip install --no-cache-dir opencv-python-headless scikit-learn

# Copy your layer code to a temporary location
COPY lambda_layer/python /tmp/dynamo

# Install everything from your "dynamo" package
RUN pip install --no-cache-dir -r /tmp/dynamo/requirements.txt
RUN pip install --no-cache-dir /tmp/dynamo

# Remove the temporary copy (optional, but keeps container small)
RUN rm -rf /tmp/dynamo

# Then copy only your final .py code
COPY ingestion/cluster.py /var/task/cluster.py

# Set up the working directory, environment, and default handler
WORKDIR /var/task
CMD ["cluster.lambda_handler"]