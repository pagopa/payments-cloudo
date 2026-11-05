#!/usr/bin/env python3
"""
Runbook Name: [Name]
Description: Manual execution script for [Task]
Usage: python3 script.py --rg <resource-group> --name <resource-name>
Schema: Pass the arguments through "run_args".
        Consider "require_approval": true if the runbook changes anything.
"""

import argparse
import logging
import os
import subprocess
import sys

# Only stdout reaches the execution log of a successful run.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def check_azure_login():
    """Reuses an existing session, or logs in with the Managed Identity.

    The worker is headless, so this never falls back to an interactive login.
    To run locally, use `az login` first.
    """
    already_logged_in = subprocess.run(
        ["az", "account", "show"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if already_logged_in.returncode == 0:
        logger.info("Azure login verified.")
    else:
        logger.info("Logging into Azure with Managed Identity...")
        cmd = ["az", "login", "--identity"]
        client_id = os.environ.get("AZURE_CLIENT_ID")
        if client_id:
            cmd += ["--client-id", client_id]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)

    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
    if subscription_id:
        subprocess.run(
            ["az", "account", "set", "--subscription", subscription_id], check=True
        )


def perform_manual_task(rg: str, resource_name: str, extra_param: str) -> None:
    """
    Core logic for the manual task.

    Raise an exception to mark the execution as failed.
    """
    logger.info("Executing task on %s (%s), extra: %s", resource_name, rg, extra_param)

    # --- YOUR LOGIC HERE ---
    # Example:
    # subprocess.run(["az", "group", "exists", "-n", rg], check=True)
    raise NotImplementedError("Runbook not implemented")


def main():
    parser = argparse.ArgumentParser(description="Manual Runbook Execution")

    # Example: Define required arguments for manual run
    parser.add_argument("--rg", required=True, help="Azure Resource Group Name")
    parser.add_argument("--name", required=True, help="Resource Name")
    parser.add_argument(
        "--extra", default="default-val", help="Optional extra parameter"
    )
    parser.add_argument(
        "--skip-login", action="store_true", help="Skip Azure login check"
    )

    args = parser.parse_args()

    try:
        if not args.skip_login:
            check_azure_login()

        perform_manual_task(args.rg, args.name, args.extra)
        logger.info("Manual operation completed successfully.")
        return 0
    except Exception:
        logger.exception("Fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
