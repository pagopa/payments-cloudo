"""Shared-secret check for the worker's management endpoints."""

import os

import azure.functions as func

CLOUDO_SECRET_KEY = os.environ.get("CLOUDO_SECRET_KEY", "")


def is_authorized_request(req: func.HttpRequest) -> bool:
    request_key = req.headers.get("x-cloudo-key")
    return bool(CLOUDO_SECRET_KEY) and request_key == CLOUDO_SECRET_KEY
