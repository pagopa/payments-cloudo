#!/usr/bin/env python3
"""
Runbook Name: Key Vault Expiring Secrets
Description: Lists the secrets of a vault that have expired or expire soon, and fails if
             there are any. It only reads metadata: secret values are never fetched.
Trigger: Schedule.
Usage: python3 keyvault_expiring_secrets.py --vault-url <url> [--days 30]
Requires: The worker identity needs the Key Vault Reader role, which excludes values.
Schema: "runbook": "azure/keyvault_expiring_secrets.py",
        "run_args": "--vault-url https://kv-payments.vault.azure.net --days 30",
        "oncall": true
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# Only stdout reaches the execution log of a successful run.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
# Keep the SDK's request logs out of the execution log.
logging.getLogger("azure").setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(description="Key Vault expiring secrets")
    parser.add_argument("--vault-url", required=True)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    try:
        client = SecretClient(args.vault_url, DefaultAzureCredential())
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(days=args.days)

        total = expiring = without_expiry = 0
        for secret in client.list_properties_of_secrets():
            if not secret.enabled:
                continue
            total += 1

            if secret.expires_on is None:
                without_expiry += 1
            elif secret.expires_on <= deadline:
                expiring += 1
                state = "expired" if secret.expires_on <= now else "expiring"
                logger.error(
                    "%s is %s on %s",
                    secret.name,
                    state,
                    secret.expires_on.date().isoformat(),
                )

        logger.info(
            "%s enabled secrets: %s expired or expiring within %s days, "
            "%s without an expiry date.",
            total,
            expiring,
            args.days,
            without_expiry,
        )
        return 1 if expiring else 0
    except Exception:
        logger.exception("Fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
