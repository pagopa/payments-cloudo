# Official base image for Azure Functions Python (Functions v4, Python 3.11)
FROM mcr.microsoft.com/azure-functions/python:4-python3.11

ARG APP_PATH=.
ARG TERRAFORM_VERSION=1.14.8

# Helpful environment variables
ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install tzdata to ensure proper zoneinfo behavior
USER root
RUN curl -sL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor | tee /etc/apt/trusted.gpg.d/microsoft.gpg > /dev/null && \
    AZ_REPO=$(lsb_release -cs) && \
    echo "deb [arch=amd64] https://packages.microsoft.com/repos/azure-cli/ $AZ_REPO main" | tee /etc/apt/sources.list.d/azure-cli.list && \
    apt-get update && apt-get upgrade -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    tzdata \
    azure-cli \
    apt-transport-https \
    ca-certificates \
    curl \
    jq \
    git \
    unzip \
    && curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && az aks install-cli \
    && rm -rf /var/lib/apt/lists/* kubectl

# Install terraform binary
RUN curl -fsSL https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip -o /tmp/terraform.zip \
  && unzip /tmp/terraform.zip -d /tmp \
  && mv /tmp/terraform /usr/local/bin/ \
  && rm -rf /tmp/* \
  && terraform version

# Copy only requirements first to leverage Docker layer cache
COPY ${APP_PATH}/requirements.txt /tmp/requirements.txt

# Install Python dependencies into the Functions runtime folder to avoid permission issues
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r /tmp/requirements.txt

# Copy the Function App code
COPY ${APP_PATH}/ /home/site/wwwroot/
