#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Collect Diagnostics
# Description: Prints the state of the pod or deployment named by the alert into
#              the execution log. Read-only: a good first automation for an alert.
#              Application logs end up in the execution log: review what they contain.
# Trigger:     Alert. Nothing to do on Resolved.
# Usage:       ./collect-diagnostics.sh [LOG_TAIL_LINES]
# Schema:      "runbook": "aks/collect-diagnostics.sh",
#              "run_args": "200",
#              "aks_login_required": true
# ==============================================================================

LOG_TAIL_LINES=${1:-100}

NAMESPACE=${AKS_NAMESPACE:-}
POD=${AKS_POD:-}
DEPLOYMENT=${AKS_DEPLOYMENT:-}
CONDITION=${MONITOR_CONDITION:-Fired}

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

section() { printf '\n===== %s =====\n' "$1"; }

if [[ "$CONDITION" == "Resolved" ]]; then
    log_info "Alert is Resolved: nothing to do."
    exit 0
fi

if [[ -z "$NAMESPACE" || (-z "$POD" && -z "$DEPLOYMENT") ]]; then
    log_error "Missing required environment variables (AKS_NAMESPACE, and AKS_POD or AKS_DEPLOYMENT)"
    exit 1
fi

if [[ -n "$POD" ]]; then
    section "Pod $POD"
    kubectl describe pod "$POD" -n "$NAMESPACE"

    section "Logs (last $LOG_TAIL_LINES lines)"
    kubectl logs "$POD" -n "$NAMESPACE" --all-containers --tail="$LOG_TAIL_LINES"

    section "Logs of the previous container"
    # Only exists if a container has restarted, so this is not a failure.
    kubectl logs "$POD" -n "$NAMESPACE" --all-containers --previous \
        --tail="$LOG_TAIL_LINES" 2>&1 || log_info "No previous container logs."
else
    section "Deployment $DEPLOYMENT"
    kubectl describe deployment "$DEPLOYMENT" -n "$NAMESPACE"
fi

section "Pods in namespace $NAMESPACE"
kubectl get pods -n "$NAMESPACE" -o wide

section "Recent events in namespace $NAMESPACE"
kubectl get events -n "$NAMESPACE" --sort-by=.lastTimestamp | tail -n 30
