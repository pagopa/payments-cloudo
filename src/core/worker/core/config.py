"""Worker configuration: queue names, log limits, receiver and GitHub settings."""

import os

QUEUE_NAME = os.environ.get("QUEUE_NAME", "queue")
NOTIFICATION_QUEUE_NAME = os.environ.get(
    "NOTIFICATION_QUEUE_NAME", "cloudo-notification"
)
STORAGE_CONNECTION = "AzureWebJobsStorage"
MAX_INLINE_LOG_BYTES = int(
    os.environ.get("MAX_INLINE_LOG_BYTES", "48000")
)  # Keep queue message below Azure Table/queue limits when inlined
LOGS_BLOB_CONTAINER = os.environ.get("LOGS_BLOB_CONTAINER", "runbook-logs")
LOGS_REF_PREFIX = "blobref://"
LOG_STREAM_UPDATE_SECONDS = float(os.environ.get("LOG_STREAM_UPDATE_SECONDS", "3"))
WORKER_TEAM = os.environ.get("WORKER_TEAM", "default").strip().lower() or "default"

# Receiver endpoint configuration
RECEIVER_URL = os.environ.get("RECEIVER_URL", "http://orchestrator/api/receiver")
RECEIVER_HTTP_TIMEOUT = int(os.environ.get("RECEIVER_HTTP_TIMEOUT", "5"))

# GitHub fallback configuration
GITHUB_REPO = os.environ.get("GITHUB_REPO", "pagopa/payments-cloudo")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_PATH_PREFIX = os.environ.get("GITHUB_PATH_PREFIX", "src/runbooks")
