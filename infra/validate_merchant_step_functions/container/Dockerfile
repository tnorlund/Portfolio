# syntax=docker/dockerfile:1
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Copy and install local packages from the repository root
# COPY receipt_dynamo /tmp/receipt_dynamo
# COPY receipt_label /tmp/receipt_label
# RUN --mount=type=cache,target=/root/.cache/pip \
#     PIP_DISABLE_PIP_VERSION_CHECK=1 python -m pip install --no-cache-dir -t ${LAMBDA_TASK_ROOT} \
#       /tmp/receipt_dynamo \
#       "/tmp/receipt_label[full]" && \
#     rm -rf /tmp/receipt_dynamo /tmp/receipt_label

# Copy the merchant validation container handler from repo root context (changes here won't bust pip cache)
COPY infra/validate_merchant_step_functions/container/ ${LAMBDA_TASK_ROOT}/

# Default handler
CMD ["handler.lambda_handler"]

