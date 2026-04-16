FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps kept minimal for faster, smaller image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install app dependencies first for better layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy source code.
COPY src /app/src
COPY scripts /app/scripts

EXPOSE 8000

# Run FastAPI app for Ubuntu/VPS deployment.
CMD ["uvicorn", "scripts.local_server:app", "--host", "0.0.0.0", "--port", "8000"]
