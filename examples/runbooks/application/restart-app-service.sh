#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Restart App Service
# Description: Restarts the App Service named by the alert and checks that it comes
#              back. Restarting drops in-flight requests and runs again each time the
#              alert fires, so it asks for approval.
# Trigger:     Alert. Nothing to do on Resolved.
# Usage:       ./restart-app-service.sh [HEALTH_PATH]
# Requires:    The worker identity needs the Website Contributor role on the app.
# Schema:      "runbook": "application/restart-app-service.sh",
#              "run_args": "/health",
#              "require_approval": true,
#              "oncall": true
# ==============================================================================

HEALTH_PATH=${1:-/}
ATTEMPTS=12
DELAY_SECONDS=15

RG_NAME=${RESOURCE_RG:-}
APP_NAME=${RESOURCE_NAME:-}
CONDITION=${MONITOR_CONDITION:-Fired}

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

login_azure() {
    if az account show > /dev/null 2>&1; then
        return
    fi

    log_info "Logging into Azure with Managed Identity..."
    if [[ -n "${AZURE_CLIENT_ID:-}" ]]; then
        az login --identity --client-id "$AZURE_CLIENT_ID" > /dev/null
    else
        az login --identity > /dev/null
    fi

    if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
        az account set --subscription "$AZURE_SUBSCRIPTION_ID"
    fi
}

if [[ "$CONDITION" == "Resolved" ]]; then
    log_info "Alert is Resolved: nothing to do."
    exit 0
fi

if [[ -z "$RG_NAME" || -z "$APP_NAME" ]]; then
    log_error "Missing required environment variables (RESOURCE_RG, RESOURCE_NAME)"
    exit 1
fi

login_azure

HOSTNAME_APP=$(az webapp show --resource-group "$RG_NAME" --name "$APP_NAME" \
    --query defaultHostName --output tsv)

log_info "Restarting $APP_NAME..."
az webapp restart --resource-group "$RG_NAME" --name "$APP_NAME"

URL="https://${HOSTNAME_APP}${HEALTH_PATH}"
for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
    # curl reports 000 if it gets no response, and that is just another failed attempt.
    STATUS=$(curl --silent --output /dev/null --max-time 10 --write-out '%{http_code}' "$URL" || true)
    log_info "Attempt $attempt of $ATTEMPTS: $URL answered $STATUS"

    if [[ "$STATUS" == 2* ]]; then
        log_info "$APP_NAME is healthy again."
        exit 0
    fi

    if ((attempt < ATTEMPTS)); then
        sleep "$DELAY_SECONDS"
    fi
done

log_error "$APP_NAME did not become healthy after the restart"
exit 1
