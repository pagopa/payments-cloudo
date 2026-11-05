#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Rerun Failed Job
# Description: Starts a new run of the CronJob that owns the failed Job.
# Pattern:     The new Job has a name derived from the failed one, so each failure
#              gets a single retry, however many times the alert fires.
# Trigger:     Alert. Nothing to do on Resolved.
# Usage:       ./rerun-failed-job.sh
# Schema:      "runbook": "aks/rerun-failed-job.sh",
#              "aks_login_required": true,
#              "oncall": true
# ==============================================================================

NAMESPACE=${AKS_NAMESPACE:-}
JOB=${AKS_JOB:-}
CONDITION=${MONITOR_CONDITION:-Fired}

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

if [[ "$CONDITION" == "Resolved" ]]; then
    log_info "Alert is Resolved: nothing to do."
    exit 0
fi

if [[ -z "$NAMESPACE" || -z "$JOB" ]]; then
    log_error "Missing required environment variables (AKS_NAMESPACE, AKS_JOB)"
    exit 1
fi

CRONJOB=$(kubectl get job "$JOB" -n "$NAMESPACE" \
    -o jsonpath='{.metadata.ownerReferences[?(@.kind=="CronJob")].name}')
if [[ -z "$CRONJOB" ]]; then
    log_error "Job $JOB has no owning CronJob, so it cannot be recreated from it"
    exit 1
fi

# Job names are limited to 63 characters.
RETRY_JOB="${JOB:0:50}-cloudo-retry"

if kubectl get job "$RETRY_JOB" -n "$NAMESPACE" > /dev/null 2>&1; then
    log_info "Job $RETRY_JOB already exists: not retrying again."
    exit 0
fi

kubectl create job "$RETRY_JOB" --from="cronjob/$CRONJOB" -n "$NAMESPACE"
log_info "Started $RETRY_JOB from cronjob/$CRONJOB."
