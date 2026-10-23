"""Orchestrator configuration: table/queue names and environment settings."""

import logging
import os

import azure.functions as func

# Centralize configuration strings to avoid "magic strings"
TABLE_NAME = "RunbookLogs"
TABLE_SCHEMAS = "RunbookSchemas"
TABLE_WORKERS_SCHEMAS = "WorkersRegistry"
TABLE_USERS = "CloudoUsers"
TABLE_TEAMS = "CloudoTeams"
TABLE_SETTINGS = "CloudoSettings"
TABLE_AUDIT = "CloudoAuditLogs"
TABLE_SCHEDULES = "CloudoSchedules"
TABLE_AI_ANALYSIS = "CloudoAiAnalysis"
DEFAULT_TEAM = "default"
STORAGE_CONN = "AzureWebJobsStorage"
NOTIFICATION_QUEUE_NAME = os.environ.get(
    "NOTIFICATION_QUEUE_NAME", "cloudo-notification"
)
AI_ANALYSIS_QUEUE_NAME = os.environ.get("AI_ANALYSIS_QUEUE_NAME", "cloudo-ai-analysis")
STORAGE_CONNECTION = "AzureWebJobsStorage"
MAX_TABLE_CHARS = int(os.getenv("MAX_TABLE_LOG_CHARS", "32000"))
MAX_TABLE_ENTITY_BODY_BYTES = int(os.getenv("MAX_TABLE_ENTITY_BODY_BYTES", "62000"))
LOGS_BLOB_CONTAINER = os.getenv("LOGS_BLOB_CONTAINER", "runbook-logs")
LOGS_REF_PREFIX = "blobref://"
APPROVAL_TTL_MIN = int(os.getenv("APPROVAL_TTL_MIN", "60"))
APPROVAL_SECRET = (os.getenv("APPROVAL_SECRET") or "").strip()
SESSION_SECRET = (os.getenv("SESSION_SECRET") or "").strip()
if not SESSION_SECRET and os.getenv("LOCAL_DEV", "false").lower() != "true":
    logging.error("CRITICAL: SESSION_SECRET not configured. Authentication will fail.")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "pagopa/payments-cloudo")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_PATH_PREFIX = os.environ.get("GITHUB_PATH_PREFIX", "")

if os.getenv("LOCAL_DEV", "false").lower() != "true":
    AUTH = func.AuthLevel.FUNCTION
else:
    AUTH = func.AuthLevel.ANONYMOUS

STATUS_PRIORITY: dict[str, int] = {
    "succeeded": 5,
    "completed": 5,
    "failed": 4,
    "error": 4,
    "running": 3,
    "skipped": 3,
    "rejected": 3,
    "stopped": 3,
    "accepted": 2,
    "pending": 1,
}

SETTINGS_CACHE_TTL_SECONDS = int(os.getenv("SETTINGS_CACHE_TTL_SECONDS", "60"))
