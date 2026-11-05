#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Scale AKS Node Pool
# Description: Adds nodes to a node pool. If the pool uses the cluster autoscaler,
#              it raises the maximum instead. Prints the plan unless --apply is set.
# Trigger:     Manual.
# Usage:       ./scale-node-pool.sh <RESOURCE_GROUP> <CLUSTER_NAME> <NODE_POOL> [--step N] [--apply]
# Requires:    The worker identity needs write access to the cluster. Uses the Azure
#              API, so it does not need "aks_login_required".
# Schema:      "runbook": "azure/scale-node-pool.sh",
#              "run_args": "rg-aks-prod aks-prod user01 --step 2 --apply",
#              "require_approval": true
# ==============================================================================

usage() {
    echo "Usage: $0 <RESOURCE_GROUP> <CLUSTER_NAME> <NODE_POOL> [--step N] [--apply]"
    exit 1
}

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

if [[ $# -lt 3 ]]; then
    usage
fi

RESOURCE_GROUP=$1
CLUSTER_NAME=$2
NODE_POOL=$3
shift 3

STEP=1
APPLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --step)
            [[ $# -ge 2 ]] || usage
            STEP=$2
            shift 2
            ;;
        --apply)
            APPLY=true
            shift
            ;;
        *)
            usage
            ;;
    esac
done

if [[ ! "$STEP" =~ ^[1-9][0-9]*$ ]]; then
    log_error "--step must be a positive integer"
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

POOL=$(az aks nodepool show \
    --resource-group "$RESOURCE_GROUP" \
    --cluster-name "$CLUSTER_NAME" \
    --name "$NODE_POOL" \
    --output json)

AUTOSCALING=$(echo "$POOL" | jq -r '.enableAutoScaling // false')
COUNT=$(echo "$POOL" | jq -r '.count')
MIN_COUNT=$(echo "$POOL" | jq -r '.minCount // empty')
MAX_COUNT=$(echo "$POOL" | jq -r '.maxCount // empty')

if [[ "$AUTOSCALING" == "true" ]]; then
    NEW_MAX=$((MAX_COUNT + STEP))
    log_info "Plan: raise the autoscaler maximum of $NODE_POOL from $MAX_COUNT to $NEW_MAX."
    COMMAND=(az aks nodepool update --update-cluster-autoscaler
        --min-count "$MIN_COUNT" --max-count "$NEW_MAX")
else
    NEW_COUNT=$((COUNT + STEP))
    log_info "Plan: scale $NODE_POOL from $COUNT to $NEW_COUNT nodes."
    COMMAND=(az aks nodepool scale --node-count "$NEW_COUNT")
fi

if [[ "$APPLY" != "true" ]]; then
    log_info "Dry run: nothing changed. Add --apply to run it."
    exit 0
fi

"${COMMAND[@]}" \
    --resource-group "$RESOURCE_GROUP" \
    --cluster-name "$CLUSTER_NAME" \
    --name "$NODE_POOL" \
    --output none
log_info "Node pool $NODE_POOL updated."
