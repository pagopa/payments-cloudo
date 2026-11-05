#!/bin/bash
set -euo pipefail

# ==============================================================================
# Script Name: HTTP Health Check
# Description: Calls an application endpoint until it answers with the expected
#              status, and fails if it never does. Read-only, if the endpoint is.
# Trigger:     Schedule, or manual after a change.
# Usage:       ./http-health-check.sh <URL> [EXPECTED_STATUS] [ATTEMPTS] [DELAY_SECONDS]
# Schema:      "runbook": "application/http-health-check.sh",
#              "run_args": "https://api.example.com/health 200 5 10",
#              "oncall": true
# ==============================================================================

log_info() { echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"; }
log_error() { echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2; }

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <URL> [EXPECTED_STATUS] [ATTEMPTS] [DELAY_SECONDS]"
    exit 1
fi

URL=$1
EXPECTED_STATUS=${2:-200}
ATTEMPTS=${3:-5}
DELAY_SECONDS=${4:-10}

if [[ ! "$EXPECTED_STATUS" =~ ^[0-9]+$ || ! "$ATTEMPTS" =~ ^[1-9][0-9]*$ || ! "$DELAY_SECONDS" =~ ^[0-9]+$ ]]; then
    log_error "EXPECTED_STATUS, ATTEMPTS and DELAY_SECONDS must be integers"
    exit 1
fi

for ((attempt = 1; attempt <= ATTEMPTS; attempt++)); do
    # curl reports 000 if it gets no response, and that is just another failed attempt.
    STATUS=$(curl --silent --output /dev/null --max-time 10 --write-out '%{http_code}' "$URL" || true)
    log_info "Attempt $attempt of $ATTEMPTS: $URL answered $STATUS"

    if [[ "$STATUS" == "$EXPECTED_STATUS" ]]; then
        exit 0
    fi

    if ((attempt < ATTEMPTS)); then
        sleep "$DELAY_SECONDS"
    fi
done

log_error "$URL never answered $EXPECTED_STATUS"
exit 1
