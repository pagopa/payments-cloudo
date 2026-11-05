#!/usr/bin/env python3
"""
Runbook Name: TLS Certificate Expiry
Description: Connects to each host and fails if its certificate expires soon, is not
             valid, or cannot be checked. Read-only, and it needs no Azure access.
Trigger: Schedule.
Usage: python3 tls_cert_expiry.py --host <host[:port]> [--host ...] [--min-days 30]
Schema: "runbook": "application/tls_cert_expiry.py",
        "run_args": "--host api.example.com --host checkout.example.com:8443 --min-days 30",
        "oncall": true
"""

import argparse
import logging
import socket
import ssl
import sys
import time

SECONDS_PER_DAY = 86400

# Only stdout reaches the execution log of a successful run.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def days_until_expiry(host: str, port: int) -> float:
    """Returns the days left. An invalid certificate fails the TLS handshake."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with (
        socket.create_connection((host, port), timeout=10) as raw_socket,
        context.wrap_socket(raw_socket, server_hostname=host) as tls_socket,
    ):
        not_after = tls_socket.getpeercert()["notAfter"]
    return (ssl.cert_time_to_seconds(not_after) - time.time()) / SECONDS_PER_DAY


def main() -> int:
    parser = argparse.ArgumentParser(description="TLS certificate expiry check")
    parser.add_argument("--host", action="append", required=True)
    parser.add_argument("--min-days", type=int, default=30)
    args = parser.parse_args()

    failures = 0
    for target in args.host:
        host, _, port = target.partition(":")
        try:
            days = days_until_expiry(host, int(port or 443))
        except (OSError, ValueError) as error:
            # Includes ssl.SSLError, which is an OSError.
            logger.error("%s: cannot verify the certificate: %s", target, error)
            failures += 1
            continue

        if days < args.min_days:
            logger.error("%s: certificate expires in %.0f days", target, days)
            failures += 1
        else:
            logger.info("%s: certificate expires in %.0f days", target, days)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
