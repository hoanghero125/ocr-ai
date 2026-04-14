FROM public.ecr.aws/lambda/python:3.11

# Install production dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY src/ ${LAMBDA_TASK_ROOT}/src/

# Default handler — overridden per-function by image_config.command in Terraform:
#   api    → src.lambda_handler.api_gateway_handler
#   worker → src.lambda_handler.worker_handler
CMD ["src.lambda_handler.handler"]
