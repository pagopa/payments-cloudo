# Official base image for Azure Functions Python (Functions v4, Python 3.11)
FROM mcr.microsoft.com/azure-functions/python:4-python3.11

# Build-time app path (set this when building)
# Example:
#   docker build --build-arg APP_PATH=src/core/agent -t yourimage .
ARG APP_PATH=.

# Useful environment variables
ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true \
    FUNCTIONS_WORKER_RUNTIME=python \
    API_PREFIX=/api \
    PYTHONPATH=/home/site/wwwroot \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COPILOT_CLI_EXTRACT_DIR=/opt/copilot-cli \
    COPILOT_SKIP_CLI_DOWNLOAD=1

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

# Bake the Copilot SDK runtime binary at build time (COPILOT_CLI_EXTRACT_DIR
# above), so AGENT_LLM_PROVIDER=copilot_sdk never pays a cold download on the
# request path — and COPILOT_SKIP_CLI_DOWNLOAD=1 makes a broken bake fail
# loudly at container start instead of silently reaching for the network.
RUN python -m copilot download-runtime

# Copy the Function App code into the script root
COPY ${APP_PATH}/ /home/site/wwwroot/

WORKDIR /home/site/wwwroot

CMD ["python", "-m", "uvicorn", "--app-dir", "/home/site/wwwroot", "fastapi_app:app", "--host", "0.0.0.0", "--port", "80", "--no-access-log"]
