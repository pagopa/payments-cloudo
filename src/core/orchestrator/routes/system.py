"""Service endpoints: feature flags and health check."""

import json
import os

import azure.functions as func
from core.config import AUTH

bp = func.Blueprint()


@bp.route(route="features", auth_level=AUTH)
def features(req: func.HttpRequest) -> func.HttpResponse:
    """Expose feature flags to frontend"""

    feature_dev_enabled = os.getenv("FEATURE_DEV", "false").lower() == "true"

    body = json.dumps(
        {
            "FEATURE_DEV": feature_dev_enabled,
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(body, status_code=200, mimetype="application/json")


@bp.route(route="healthz", auth_level=AUTH)
def heartbeat(req: func.HttpRequest) -> func.HttpResponse:
    import utils

    now_utc = utils.utc_now_iso()
    body = json.dumps(
        {
            "status": "ok",
            "time": now_utc,
            "service": "Trigger",
        },
        ensure_ascii=False,
    )
    return func.HttpResponse(
        body,
        status_code=200,
        mimetype="application/json",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
