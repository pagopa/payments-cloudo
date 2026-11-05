#!/usr/bin/env python3
"""
Runbook Name: [Name]
Description: Python handler for ClouDO alerts
Trigger: Alert. The worker exports RESOURCE_*, AKS_* and MONITOR_CONDITION.
Schema: Set "aks_login_required": true if this runbook calls kubectl.
        The worker then exports KUBECONFIG, with a token valid for 10 minutes.
"""

import logging
import os
import sys

# Only stdout reaches the execution log of a successful run.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


class AlertContext:
    """Helper class to access environment variables provided by the alert system.

    The worker always exports these, as empty strings when the alert has no value:
    use `or` for defaults, because os.environ.get(name, default) would return "".
    """

    def __init__(self):
        # Azure Resource Context
        self.resource_id = os.environ.get("RESOURCE_ID")
        self.resource_rg = os.environ.get("RESOURCE_RG")
        self.resource_name = os.environ.get("RESOURCE_NAME")

        # AKS Context
        self.aks_job = os.environ.get("AKS_JOB")
        self.aks_deployment = os.environ.get("AKS_DEPLOYMENT")
        self.aks_namespace = os.environ.get("AKS_NAMESPACE") or "default"
        self.aks_pod = os.environ.get("AKS_POD")
        self.aks_hpa = os.environ.get("AKS_HPA")

        # Alert State
        self.condition = os.environ.get("MONITOR_CONDITION") or "Fired"

        # Execution Context
        self.exec_id = os.environ.get("CLOUDO_EXEC_ID") or "local"

    def validate(self):
        """Check if critical variables are present."""
        if not self.resource_rg or not self.resource_name:
            raise ValueError(
                "Missing critical context: RESOURCE_RG or RESOURCE_NAME not set."
            )


def perform_remediation(ctx: AlertContext) -> None:
    """
    Main logic based on the alert context.

    An alert can fire more than once: both branches must be safe to run again.
    Raise an exception to mark the execution as failed.
    """
    logger.info(
        "Execution %s: handling alert for %s (state: %s)",
        ctx.exec_id,
        ctx.resource_name,
        ctx.condition,
    )

    if ctx.condition == "Resolved":
        # Roll back what the Fired branch did, or log that there is nothing to do.
        raise NotImplementedError("Resolved branch not implemented")

    raise NotImplementedError("Fired branch not implemented")


def main():
    try:
        ctx = AlertContext()
        ctx.validate()
        perform_remediation(ctx)
        logger.info("Operation completed successfully.")
        return 0
    except Exception:
        logger.exception("Fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
