#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Service Bus Queue Depth
# Description: Prints the backlog and dead-letter counts of a queue, and fails if
#              either goes over its threshold. Read-only.
# Trigger:     Alert or schedule. Nothing to do on Resolved.
# Usage:       ./servicebus-queue-depth.sh <RESOURCE_GROUP> <NAMESPACE> <QUEUE> [MAX_ACTIVE] [MAX_DEAD_LETTER]
# Requires:    The worker identity needs a role that can read the namespace.
# Schema:      "runbook": "messaging/servicebus-queue-depth.sh",
#              "run_args": "rg-messaging sb-payments payments-events 5000 0",
#              "oncall": true
# ==============================================================================

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

if [[ "${MONITOR_CONDITION:-}" == "Resolved" ]]; then
    log_info "Alert is Resolved: nothing to do."
    exit 0
fi

if [[ $# -lt 3 ]]; then
    echo "Usage: $0 <RESOURCE_GROUP> <NAMESPACE> <QUEUE> [MAX_ACTIVE] [MAX_DEAD_LETTER]"
    exit 1
fi

RESOURCE_GROUP=$1
NAMESPACE=$2
QUEUE=$3
MAX_ACTIVE=${4:-1000}
MAX_DEAD_LETTER=${5:-0}

if [[ ! "$MAX_ACTIVE" =~ ^[0-9]+$ || ! "$MAX_DEAD_LETTER" =~ ^[0-9]+$ ]]; then
    log_error "MAX_ACTIVE and MAX_DEAD_LETTER must be non-negative integers"
    exit 1
fi

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

login_azure

COUNTS=$(az servicebus queue show \
    --resource-group "$RESOURCE_GROUP" \
    --namespace-name "$NAMESPACE" \
    --name "$QUEUE" \
    --query countDetails \
    --output json)

ACTIVE=$(echo "$COUNTS" | jq -r '.activeMessageCount')
DEAD_LETTER=$(echo "$COUNTS" | jq -r '.deadLetterMessageCount')
log_info "Queue $QUEUE: $ACTIVE active, $DEAD_LETTER dead-lettered."

FAILED=false
if ((ACTIVE > MAX_ACTIVE)); then
    log_error "Active messages are over the threshold of $MAX_ACTIVE"
    FAILED=true
fi
if ((DEAD_LETTER > MAX_DEAD_LETTER)); then
    log_error "Dead-lettered messages are over the threshold of $MAX_DEAD_LETTER"
    FAILED=true
fi

if [[ "$FAILED" == "true" ]]; then
    exit 1
fi
