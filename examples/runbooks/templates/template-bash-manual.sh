#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: [Action Name]
# Description: [Brief description]
# Usage:       ./script.sh <RESOURCE_GROUP> <RESOURCE_NAME> [EXTRA_ARG]
# Schema:      Pass the arguments through "run_args".
#              Consider "require_approval": true if the runbook changes anything.
# ==============================================================================

# --- 1. Arguments & Help ---
usage() {
    echo "Usage: $0 <RESOURCE_GROUP> <RESOURCE_NAME> [EXTRA_ARG]"
    echo ""
    echo "Arguments:"
    echo "  RESOURCE_GROUP  The Azure Resource Group name"
    echo "  RESOURCE_NAME   The name of the target resource (e.g., Cluster name, VM name)"
    echo "  EXTRA_ARG       (Optional) Additional parameter"
    echo ""
    exit 1
}

if [ "$#" -lt 2 ]; then
    usage
fi

RESOURCE_GROUP=$1
RESOURCE_NAME=$2
EXTRA_ARG=${3:-"default-value"} # Default value if 3rd arg is missing

# --- 2. Helper Functions ---
# Only stdout reaches the execution log of a successful run.
log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

# --- 3. Azure Login ---
# Reuses an existing session, so `az login` beforehand is enough to run it locally.
# The worker is headless: never fall back to an interactive login.
login_azure() {
    if az account show > /dev/null 2>&1; then
        log_info "Already logged into Azure."
    else
        log_info "Logging into Azure with Managed Identity..."
        if [[ -n "${AZURE_CLIENT_ID:-}" ]]; then
            if ! az login --identity --client-id "$AZURE_CLIENT_ID" > /dev/null; then
                log_error "Failed to login with User Assigned Managed Identity"
                exit 1
            fi
        elif ! az login --identity > /dev/null; then
            log_error "No Managed Identity found. To run locally, use 'az login' first."
            exit 1
        fi
    fi

    if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
        az account set --subscription "$AZURE_SUBSCRIPTION_ID"
    fi
}

# --- 4. Main Logic ---
main() {
    log_info "Starting manual operation..."
    log_info "Target: $RESOURCE_NAME in RG: $RESOURCE_GROUP (extra: $EXTRA_ARG)"

    login_azure

    # --- YOUR COMMANDS HERE ---
    # Exit code decides the outcome: non-zero marks the execution as failed.
    # Example:
    # az aks show --resource-group "$RESOURCE_GROUP" --name "$RESOURCE_NAME"
    log_error "Runbook not implemented"
    exit 1
}

main "$@"
