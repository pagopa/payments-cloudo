# Official base image for Azure Functions Python (Functions v4, Python 3.11)
FROM mcr.microsoft.com/azure-functions/python:4-python3.11@sha256:7375d6f920c52f5c495080ad074fa463814d01abe6db03b7056c404058d76bb1

# Build-time app path (set this when building)
# Example:
#   docker build --build-arg APP_PATH=src/core/orchestrator/trigger -t yourimage .
ARG APP_PATH=.

# Useful environment variables
ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true \
    FUNCTIONS_WORKER_RUNTIME=python \
    API_PREFIX=/api \
    PYTHONPATH=/home/site/wwwroot \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Use root for system packages and pip install
USER root

# Install tzdata to ensure proper zoneinfo behavior
RUN apt-get update && apt-get upgrade -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

# Copy only requirements first to leverage Docker layer cache
COPY ${APP_PATH}/requirements.txt /tmp/requirements.txt

# Install Python dependencies into the Functions runtime folder to avoid permission issues
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r /tmp/requirements.txt

# Copy the Function App code into the script root
COPY ${APP_PATH}/ /home/site/wwwroot/

WORKDIR /home/site/wwwroot

CMD ["python", "-m", "uvicorn", "--app-dir", "/home/site/wwwroot", "fastapi_app:app", "--host", "0.0.0.0", "--port", "80", "--no-access-log"]
