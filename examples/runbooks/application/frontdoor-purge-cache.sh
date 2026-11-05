#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: Front Door Purge Cache
# Description: Purges the given paths from an Azure Front Door endpoint, for example
#              to take stale content down. Prints the plan unless --apply is set.
#              Purging a lot at once sends that traffic to the origin: prefer narrow
#              paths, and pass "/*" only on purpose.
# Trigger:     Manual.
# Usage:       ./frontdoor-purge-cache.sh <RESOURCE_GROUP> <PROFILE> <ENDPOINT> <PATH>... [--apply]
# Requires:    The worker identity needs the CDN Endpoint Contributor role, or equivalent.
# Schema:      "runbook": "application/frontdoor-purge-cache.sh",
#              "run_args": "rg-edge afd-payments checkout '/assets/*' /index.html --apply",
#              "require_approval": true
# ==============================================================================

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

usage() {
    echo "Usage: $0 <RESOURCE_GROUP> <PROFILE> <ENDPOINT> <PATH>... [--apply]"
    exit 1
}

if [[ $# -lt 4 ]]; then
    usage
fi

RESOURCE_GROUP=$1
PROFILE=$2
ENDPOINT=$3
shift 3

APPLY=false
PATHS=()

for arg in "$@"; do
    case "$arg" in
        --apply) APPLY=true ;;
        /*) PATHS+=("$arg") ;;
        *)
            log_error "Paths must start with '/': $arg"
            exit 1
            ;;
    esac
done

if [[ ${#PATHS[@]} -eq 0 ]]; then
    usage
fi

log_info "Plan: purge ${PATHS[*]} from $ENDPOINT ($PROFILE)."

if [[ "$APPLY" != "true" ]]; then
    log_info "Dry run: nothing changed. Add --apply to run it."
    exit 0
fi

if ! az account show > /dev/null 2>&1; then
    log_info "Logging into Azure with Managed Identity..."
    if [[ -n "${AZURE_CLIENT_ID:-}" ]]; then
        az login --identity --client-id "$AZURE_CLIENT_ID" > /dev/null
    else
        az login --identity > /dev/null
    fi

    if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
        az account set --subscription "$AZURE_SUBSCRIPTION_ID"
    fi
fi

az afd endpoint purge \
    --resource-group "$RESOURCE_GROUP" \
    --profile-name "$PROFILE" \
    --endpoint-name "$ENDPOINT" \
    --content-paths "${PATHS[@]}"
log_info "Purge completed."
