"""Health check endpoint and the worker registration heartbeat timer."""

import json
import logging
import os

import azure.functions as func
import requests
from core.config import QUEUE_NAME, WORKER_TEAM
from utils import _utc_now_iso

bp = func.Blueprint()


@bp.route(route="healthz", auth_level=func.AuthLevel.ANONYMOUS)
def heartbeat(req: func.HttpRequest) -> func.HttpResponse:
    now_utc = _utc_now_iso()
    body = json.dumps(
        {
            "status": "ok",
            "time": now_utc,
            "service": "RunbookTest",
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(body, status_code=200, mimetype="application/json")


@bp.schedule(
    schedule="0 */1 * * * *",
    arg_name="HeartBeatTimer",
    run_on_startup=True,
    use_monitor=False,
)
def heartbeat_trigger(HeartBeatTimer: func.TimerRequest) -> None:
    if HeartBeatTimer.past_due:
        logging.debug("The timer is past due!")

    url = os.getenv("ORCHESTRATOR_URL", "http://orchestrator/api/workers/register")
    key = os.getenv("CLOUDO_SECRET_KEY")

    payload = {
        "capability": os.getenv("WORKER_CAPABILITY", "local"),
        "worker_id": os.getenv("WEBSITE_SITE_NAME", "azure-func-worker"),
        "queue": QUEUE_NAME,
        "region": os.getenv("REGION_NAME", "azure-cloud"),
        "team": WORKER_TEAM,
    }

    try:
        r = requests.post(url, json=payload, headers={"x-cloudo-key": key}, timeout=10)
        logging.debug(f"request {r.status_code}")
        logging.debug("Heartbeat sent successfully")
    except Exception as e:
        logging.error(f"Failed to send heartbeat: {e}")
