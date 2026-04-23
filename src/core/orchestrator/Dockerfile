# Official base image for Azure Functions Python (Functions v4, Python 3.11)
FROM mcr.microsoft.com/azure-functions/python:4-python3.11@sha256:740d66eed9ff2ae54f8a96b8453a5927b0170755544a6a1db48878499b93ce77

# Build-time app path (set this when building)
# Example:
#   docker build --build-arg APP_PATH=src/core/orchestrator/trigger -t yourimage .
ARG APP_PATH=.

# Useful environment variables
ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true \
    FUNCTIONS_WORKER_RUNTIME=python \
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
