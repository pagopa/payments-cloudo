#!/usr/bin/env python3
"""
Runbook Name: PostgreSQL Health
Description: Prints connection usage, the longest running queries and blocked sessions,
             and fails if any of them goes over its threshold. Read-only: the session
             itself is read-only, and query text is never logged because it can hold
             customer data.
Trigger: Alert or schedule. Nothing to do on Resolved.
Usage: python3 postgres_health.py --host <host> --database <db> --user <role>
       [--max-connection-pct 80] [--max-query-minutes 15] [--max-blocked 10]
Requires: Microsoft Entra authentication on the server, and a role for the worker
          identity that is a member of pg_monitor, or other users' queries are hidden.
Schema: "runbook": "database/postgres_health.py",
        "run_args": "--host pg-prod.postgres.database.azure.com --database payments --user cloudo-worker",
        "oncall": true
"""

import argparse
import logging
import os
import sys

import psycopg2
from azure.identity import DefaultAzureCredential

ENTRA_POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

CONNECTIONS_SQL = """
    SELECT count(*), current_setting('max_connections')::int
    FROM pg_stat_activity
    WHERE backend_type = 'client backend'
"""

LONGEST_QUERIES_SQL = """
    SELECT pid,
           usename,
           application_name,
           EXTRACT(EPOCH FROM now() - query_start)::int AS seconds,
           substring(query from '^[[:space:]]*([A-Za-z]+)') AS command
    FROM pg_stat_activity
    WHERE state = 'active'
      AND backend_type = 'client backend'
      AND pid <> pg_backend_pid()
    ORDER BY query_start
    LIMIT 5
"""

BLOCKED_SQL = """
    SELECT count(*)
    FROM pg_stat_activity
    WHERE cardinality(pg_blocking_pids(pid)) > 0
"""

# Only stdout reaches the execution log of a successful run.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
# Keep the SDK's request logs out of the execution log.
logging.getLogger("azure").setLevel(logging.WARNING)


def connect(host: str, database: str, user: str):
    """Connects with a Microsoft Entra token for the worker's Managed Identity.

    TLS is required unless PGSSLMODE says otherwise.
    """
    token = DefaultAzureCredential().get_token(ENTRA_POSTGRES_SCOPE).token
    connection = psycopg2.connect(
        host=host,
        dbname=database,
        user=user,
        password=token,
        sslmode=os.environ.get("PGSSLMODE") or "require",
        connect_timeout=10,
        application_name="cloudo-runbook",
        options="-c statement_timeout=30000",
    )
    connection.set_session(readonly=True, autocommit=True)
    return connection


def check(connection, args) -> list[str]:
    """Logs the current state and returns the thresholds that were exceeded."""
    problems = []

    with connection.cursor() as cursor:
        cursor.execute(CONNECTIONS_SQL)
        used, maximum = cursor.fetchone()
        usage_pct = 100 * used / maximum
        logger.info("Connections: %s of %s (%.0f%%)", used, maximum, usage_pct)
        if usage_pct > args.max_connection_pct:
            problems.append(
                f"connection usage is {usage_pct:.0f}%, over {args.max_connection_pct}%"
            )

        cursor.execute(LONGEST_QUERIES_SQL)
        for pid, user, application, seconds, command in cursor.fetchall():
            logger.info(
                "Active query: pid=%s user=%s application=%s seconds=%s command=%s",
                pid,
                user,
                application,
                seconds,
                command,
            )
            if seconds > args.max_query_minutes * 60:
                problems.append(
                    f"pid {pid} has been running for {seconds}s, "
                    f"over {args.max_query_minutes} minutes"
                )

        cursor.execute(BLOCKED_SQL)
        blocked = cursor.fetchone()[0]
        logger.info("Blocked sessions: %s", blocked)
        if blocked > args.max_blocked:
            problems.append(f"{blocked} sessions blocked, over {args.max_blocked}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL health check")
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True, help="Role of the worker identity")
    parser.add_argument("--max-connection-pct", type=float, default=80)
    parser.add_argument("--max-query-minutes", type=float, default=15)
    parser.add_argument("--max-blocked", type=int, default=10)
    args = parser.parse_args()

    if os.environ.get("MONITOR_CONDITION") == "Resolved":
        logger.info("Alert is Resolved: nothing to do.")
        return 0

    try:
        connection = connect(args.host, args.database, args.user)
        try:
            problems = check(connection, args)
        finally:
            connection.close()
    except Exception:
        logger.exception("Fatal error")
        return 1

    for problem in problems:
        logger.error(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
