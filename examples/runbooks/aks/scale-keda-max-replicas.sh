#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Scale KEDA Max Replicas
# Description: Raises maxReplicaCount of the ScaledObject behind the alerting HPA
#              while the alert is Fired, and restores it when the alert is Resolved.
# Pattern:     The original value is stored in an annotation on the ScaledObject, so
#              that a repeated Fired does not keep scaling and Resolved restores the
#              exact value. If a GitOps tool manages the ScaledObject, it may revert
#              the change: exclude maxReplicaCount from its sync.
# Trigger:     Alert, on both Fired and Resolved.
# Usage:       ./scale-keda-max-replicas.sh [STEP] [HARD_LIMIT]
# Schema:      "runbook": "aks/scale-keda-max-replicas.sh",
#              "run_args": "2 20",
#              "aks_login_required": true
# ==============================================================================

STEP=${1:-1}
HARD_LIMIT=${2:-20}

NAMESPACE=${AKS_NAMESPACE:-}
HPA=${AKS_HPA:-}
CONDITION=${MONITOR_CONDITION:-Fired}

ANNOTATION="cloudo/original-max-replicas"

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

if [[ ! "$STEP" =~ ^[1-9][0-9]*$ || ! "$HARD_LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    log_error "STEP and HARD_LIMIT must be positive integers"
    exit 1
fi

if [[ -z "$NAMESPACE" || -z "$HPA" ]]; then
    log_error "Missing required environment variables (AKS_NAMESPACE, AKS_HPA)"
    exit 1
fi

SCALED_OBJECT=$(kubectl get hpa "$HPA" -n "$NAMESPACE" \
    -o jsonpath='{.metadata.labels.scaledobject\.keda\.sh/name}')
if [[ -z "$SCALED_OBJECT" ]]; then
    log_error "HPA $HPA is not managed by a KEDA ScaledObject"
    exit 1
fi

ORIGINAL_MAX=$(kubectl get scaledobject "$SCALED_OBJECT" -n "$NAMESPACE" \
    -o jsonpath="{.metadata.annotations.${ANNOTATION}}")

if [[ "$CONDITION" == "Resolved" ]]; then
    if [[ -z "$ORIGINAL_MAX" ]]; then
        log_info "ScaledObject $SCALED_OBJECT has no saved value: nothing to restore."
        exit 0
    fi

    kubectl patch scaledobject "$SCALED_OBJECT" -n "$NAMESPACE" --type=merge -p \
        "{\"metadata\":{\"annotations\":{\"${ANNOTATION}\":null}},\"spec\":{\"maxReplicaCount\":${ORIGINAL_MAX}}}"
    log_info "Restored maxReplicaCount of $SCALED_OBJECT to $ORIGINAL_MAX."
    exit 0
fi

if [[ -n "$ORIGINAL_MAX" ]]; then
    log_info "ScaledObject $SCALED_OBJECT is already scaled up (original: $ORIGINAL_MAX)."
    exit 0
fi

CURRENT_MAX=$(kubectl get scaledobject "$SCALED_OBJECT" -n "$NAMESPACE" \
    -o jsonpath='{.spec.maxReplicaCount}')
if [[ -z "$CURRENT_MAX" ]]; then
    log_error "ScaledObject $SCALED_OBJECT does not set maxReplicaCount"
    exit 1
fi

NEW_MAX=$((CURRENT_MAX + STEP))
if ((NEW_MAX > HARD_LIMIT)); then
    log_error "Refusing to scale $SCALED_OBJECT to $NEW_MAX: over the limit of $HARD_LIMIT"
    exit 1
fi

# A single patch, so that the value and its backup cannot get out of sync.
kubectl patch scaledobject "$SCALED_OBJECT" -n "$NAMESPACE" --type=merge -p \
    "{\"metadata\":{\"annotations\":{\"${ANNOTATION}\":\"${CURRENT_MAX}\"}},\"spec\":{\"maxReplicaCount\":${NEW_MAX}}}"
log_info "Raised maxReplicaCount of $SCALED_OBJECT from $CURRENT_MAX to $NEW_MAX."
