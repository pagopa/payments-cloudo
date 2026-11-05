#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Restart Deployment
# Description: Restarts the deployment named by the alert and waits for the rollout.
#              A failed rollout fails the execution, so that it can reach on-call.
# Trigger:     Alert. Nothing to do on Resolved.
# Usage:       ./restart-deployment.sh [ROLLOUT_TIMEOUT]
# Schema:      "runbook": "aks/restart-deployment.sh",
#              "run_args": "5m",
#              "aks_login_required": true,
#              "oncall": true
# ==============================================================================

# Keep this below the 10 minute lifetime of the token the worker provides.
ROLLOUT_TIMEOUT=${1:-5m}

NAMESPACE=${AKS_NAMESPACE:-}
DEPLOYMENT=${AKS_DEPLOYMENT:-}
CONDITION=${MONITOR_CONDITION:-Fired}

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

if [[ "$CONDITION" == "Resolved" ]]; then
    log_info "Alert is Resolved: nothing to do."
    exit 0
fi

if [[ -z "$NAMESPACE" || -z "$DEPLOYMENT" ]]; then
    log_error "Missing required environment variables (AKS_NAMESPACE, AKS_DEPLOYMENT)"
    exit 1
fi

log_info "Restarting deployment $DEPLOYMENT in namespace $NAMESPACE..."
kubectl rollout restart "deployment/$DEPLOYMENT" -n "$NAMESPACE"
kubectl rollout status "deployment/$DEPLOYMENT" -n "$NAMESPACE" --timeout="$ROLLOUT_TIMEOUT"
log_info "Rollout completed."
