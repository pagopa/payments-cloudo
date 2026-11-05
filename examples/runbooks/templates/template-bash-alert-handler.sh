#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: [Action Name]
# Description: [Brief description]
# Trigger:     Alert. The worker exports RESOURCE_*, AKS_* and MONITOR_CONDITION.
# Schema:      Set "aks_login_required": true if this runbook calls kubectl.
#              The worker then exports KUBECONFIG, with a token valid for 10 minutes.
# ==============================================================================

# --- 1. Load Alert Context Variables ---
# The worker always exports these, as empty strings when the alert has no value.
# Azure Resource Context
RG_NAME=${RESOURCE_RG:-}
RES_NAME=${RESOURCE_NAME:-}
RES_ID=${RESOURCE_ID:-}

# AKS Context (if applicable)
NAMESPACE=${AKS_NAMESPACE:-default}
DEPLOYMENT=${AKS_DEPLOYMENT:-}
POD_NAME=${AKS_POD:-}
HPA_NAME=${AKS_HPA:-}
JOB_NAME=${AKS_JOB:-}

# Alert State
CONDITION=${MONITOR_CONDITION:-Fired}

# Execution Context
EXEC_ID=${CLOUDO_EXEC_ID:-local}

# Remove the variables you do not need.
export RES_ID NAMESPACE DEPLOYMENT POD_NAME HPA_NAME JOB_NAME

# --- 2. Helper Functions ---
# Only stdout reaches the execution log of a successful run.
log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

# Fails until replaced, so that an unfinished runbook cannot report a false success.
not_implemented() {
    log_error "$1 not implemented"
    return 1
}

# --- 3. Azure Login ---
# Remove this function if the runbook only calls kubectl.
login_azure() {
    log_info "Logging into Azure..."
    if [[ -n "${AZURE_CLIENT_ID:-}" ]]; then
        if ! az login --identity --client-id "$AZURE_CLIENT_ID" > /dev/null; then
            log_error "Failed to login with User Assigned Managed Identity"
            exit 1
        fi
    else
        if ! az login --identity > /dev/null; then
            log_error "Failed to login with System Assigned Managed Identity"
            exit 1
        fi
    fi

    if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
        az account set --subscription "$AZURE_SUBSCRIPTION_ID"
    fi
}

# --- 4. Main Logic ---
main() {
    log_info "Execution $EXEC_ID: alert on $RES_NAME ($RG_NAME) is currently $CONDITION"

    if [[ -z "$RG_NAME" || -z "$RES_NAME" ]]; then
        log_error "Missing required environment variables (RESOURCE_RG, RESOURCE_NAME)"
        exit 1
    fi

    login_azure

    # An alert can fire more than once: both branches must be safe to run again.
    if [[ "$CONDITION" == "Resolved" ]]; then
        # --- LOGIC FOR RESOLVED STATE ---
        # Roll back what the Fired branch did, or log that there is nothing to do.
        not_implemented "Resolved branch"
    else
        # --- LOGIC FOR FIRED STATE ---
        # Exit code decides the outcome: non-zero marks the execution as failed.
        not_implemented "Fired branch"
    fi

    log_info "Operation completed successfully."
}

main "$@"
