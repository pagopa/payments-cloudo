#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Application Gateway Backend Health
# Description: Prints the health of each backend server, and fails if any of them
#              is not healthy. Read-only, and it does not need AKS.
# Trigger:     Alert or schedule. Nothing to do on Resolved.
# Usage:       ./appgw-backend-health.sh
# Requires:    The worker identity needs a role that can read the gateway.
# Schema:      "runbook": "azure/appgw-backend-health.sh",
#              "oncall": true
# ==============================================================================

RG_NAME=${RESOURCE_RG:-}
APPGW_NAME=${RESOURCE_NAME:-}
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

if [[ -z "$RG_NAME" || -z "$APPGW_NAME" ]]; then
    log_error "Missing required environment variables (RESOURCE_RG, RESOURCE_NAME)"
    exit 1
fi

login_azure

log_info "Checking backend health of $APPGW_NAME ($RG_NAME)..."
SERVERS=$(az network application-gateway show-backend-health \
    --resource-group "$RG_NAME" \
    --name "$APPGW_NAME" \
    --query "backendAddressPools[].backendHttpSettingsCollection[].servers[]" \
    --output json)

echo "$SERVERS" | jq -r '.[] | "\(.health)\t\(.address)"'

TOTAL=$(echo "$SERVERS" | jq 'length')
NOT_HEALTHY=$(echo "$SERVERS" | jq '[.[] | select(.health != "Healthy")] | length')

if ((TOTAL == 0)); then
    log_error "No backend servers found on $APPGW_NAME"
    exit 1
fi

if ((NOT_HEALTHY > 0)); then
    log_error "$NOT_HEALTHY of $TOTAL backend servers are not healthy"
    exit 1
fi

log_info "All $TOTAL backend servers are healthy."
