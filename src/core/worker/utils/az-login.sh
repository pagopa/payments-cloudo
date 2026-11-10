#!/bin/bash

set -euo pipefail

log() { echo "[az-login] $*"; }

# Azure login (Managed Identity, non-interactive).
# Always run once per execution so any `az` command in a runbook has an
# authenticated session.
log "Azure login with Managed Identity..."
if [[ -n "${AZURE_CLIENT_ID:-}" ]]; then
  az login --identity --client-id "$AZURE_CLIENT_ID" 1>/dev/null
else
  az login --identity 1>/dev/null
fi

# Optional subscription
if [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
  log "Setting subscription: ${AZURE_SUBSCRIPTION_ID}"
  az account set --subscription "$AZURE_SUBSCRIPTION_ID"
fi

log "Azure login completed (non-interactive)"
