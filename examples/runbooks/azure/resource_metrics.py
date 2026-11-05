#!/usr/bin/env python3
"""
Runbook Name: Resource Metrics
Description: Prints an Azure Monitor metric of the alerting resource using the Azure
             SDK, and optionally fails if it goes over a threshold. Read-only.
Trigger: Alert or schedule. Nothing to do on Resolved.
Usage: python3 resource_metrics.py --metric <name> [--aggregation Average]
       [--minutes 60] [--fail-above <value>]
Requires: The worker identity needs the Monitoring Reader role on the resource.
Schema: "runbook": "azure/resource_metrics.py",
        "run_args": "--metric Percentage_CPU --fail-above 90",
        "oncall": true
"""

import argparse
import json
import logging
import os
import sys
from datetime import timedelta

from azure.identity import DefaultAzureCredential
from azure.monitor.query import MetricsQueryClient

# Only stdout reaches the execution log of a successful run.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
# Keep the SDK's request logs out of the execution log.
logging.getLogger("azure").setLevel(logging.WARNING)


def resource_info() -> dict:
    """Returns the full resource_info of the alert, as received by the worker."""
    try:
        info = json.loads(os.environ.get("CLOUDO_PAYLOAD") or "{}")
        # resource_info can reach the worker as a JSON string, encoded once more.
        if isinstance(info, str):
            info = json.loads(info)
    except json.JSONDecodeError:
        logger.warning("CLOUDO_PAYLOAD is not valid JSON: ignoring it.")
        return {}
    return info if isinstance(info, dict) else {}


def fetch_values(
    resource_id: str, metric: str, aggregation: str, minutes: int
) -> list[tuple]:
    """Returns the (timestamp, value) points that have a value, oldest first."""
    # DefaultAzureCredential picks up the Managed Identity from AZURE_CLIENT_ID,
    # and also works locally after `az login`.
    client = MetricsQueryClient(DefaultAzureCredential())
    response = client.query_resource(
        resource_id,
        metric_names=[metric],
        timespan=timedelta(minutes=minutes),
        granularity=timedelta(minutes=5),
        aggregations=[aggregation],
    )

    points = []
    for result in response.metrics:
        for series in result.timeseries:
            for point in series.data:
                value = getattr(point, aggregation.lower())
                if value is not None:
                    points.append((point.timestamp, value))
    return points


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metric", required=True, help="Azure Monitor metric name")
    parser.add_argument(
        "--aggregation",
        default="Average",
        choices=["Average", "Maximum", "Minimum", "Total", "Count"],
    )
    parser.add_argument("--minutes", type=int, default=60, help="Time window")
    parser.add_argument(
        "--fail-above", type=float, help="Fail if the latest value is over this"
    )
    args = parser.parse_args()

    if os.environ.get("MONITOR_CONDITION") == "Resolved":
        logger.info("Alert is Resolved: nothing to do.")
        return 0

    try:
        resource_id = os.environ.get("RESOURCE_ID") or resource_info().get(
            "resource_id"
        )
        if not resource_id:
            raise ValueError("Missing resource id (RESOURCE_ID, CLOUDO_PAYLOAD)")

        points = fetch_values(resource_id, args.metric, args.aggregation, args.minutes)
        if not points:
            logger.warning("No data for %s in the time window.", args.metric)
            return 0

        for timestamp, value in points:
            logger.info("%s %s=%s", timestamp.isoformat(), args.metric, value)

        latest = points[-1][1]
        if args.fail_above is not None and latest > args.fail_above:
            logger.error(
                "%s is %s, over the threshold of %s",
                args.metric,
                latest,
                args.fail_above,
            )
            return 1

        return 0
    except Exception:
        logger.exception("Fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
