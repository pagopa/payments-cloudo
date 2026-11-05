#!/usr/bin/env python3
"""
Runbook Name: PostgreSQL Terminate Long Queries
Description: Terminates the sessions holding a query or a transaction open for too long,
             oldest first. Prints the plan unless --apply is set. Terminating a session
             rolls back its open transaction. Query text is never logged because it can
             hold customer data.
Trigger: Manual, with approval. Nothing to do on Resolved.
Usage: python3 postgres_terminate_long_queries.py --host <host> --database <db> --user <role>
       [--min-minutes 30] [--limit 5] [--exclude-user <role>] [--exclude-app <name>] [--apply]
Requires: Microsoft Entra authentication on the server, and a role for the worker
          identity that is a member of pg_monitor and pg_signal_backend.
Schema: "runbook": "database/postgres_terminate_long_queries.py",
        "run_args": "--host pg-prod.postgres.database.azure.com --database payments --user cloudo-worker --exclude-app batch-settlement --apply",
        "require_approval": true
"""

import argparse
import logging
import os
import sys

import psycopg2
from azure.identity import DefaultAzureCredential

ENTRA_POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

# Backups must not be killed by a runbook, and neither must the platform's own sessions.
ALWAYS_EXCLUDED_APPS = ["pg_dump", "pg_basebackup"]
ALWAYS_EXCLUDED_USERS = ["azuresu", "azure_superuser", "replication"]

CANDIDATES_SQL = """
    SELECT pid,
           usename,
           application_name,
           state,
           EXTRACT(EPOCH FROM now() - COALESCE(xact_start, query_start))::int AS seconds,
           substring(query from '^[[:space:]]*([A-Za-z]+)') AS command
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND backend_type = 'client backend'
      AND state IN ('active', 'idle in transaction')
      AND pid <> pg_backend_pid()
      AND now() - COALESCE(xact_start, query_start) > make_interval(mins => %(minutes)s)
      AND usename <> ALL(%(excluded_users)s)
      AND application_name <> ALL(%(excluded_apps)s)
    ORDER BY COALESCE(xact_start, query_start)
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


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


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
    connection.autocommit = True
    return connection


def run(connection, args) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            CANDIDATES_SQL,
            {
                "minutes": args.min_minutes,
                "excluded_users": ALWAYS_EXCLUDED_USERS + args.exclude_user,
                "excluded_apps": ALWAYS_EXCLUDED_APPS + args.exclude_app,
            },
        )
        candidates = cursor.fetchall()

        if not candidates:
            logger.info("No sessions over %s minutes.", args.min_minutes)
            return 0

        selected = candidates[: args.limit]
        if len(candidates) > len(selected):
            logger.warning(
                "Found %s sessions: only the oldest %s are in scope of --limit.",
                len(candidates),
                len(selected),
            )

        failed = 0
        for pid, user, application, state, seconds, command in selected:
            logger.info(
                "%s pid=%s user=%s application=%s state=%s seconds=%s command=%s",
                "Terminating" if args.apply else "Would terminate",
                pid,
                user,
                application,
                state,
                seconds,
                command,
            )
            if not args.apply:
                continue

            cursor.execute("SELECT pg_terminate_backend(%s)", (pid,))
            if not cursor.fetchone()[0]:
                # The session can legitimately end by itself in the meantime.
                logger.warning("Session %s was not terminated.", pid)
                failed += 1

    if not args.apply:
        logger.info("Dry run: nothing changed. Add --apply to run it.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Terminate long running sessions")
    parser.add_argument("--host", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True, help="Role of the worker identity")
    parser.add_argument("--min-minutes", type=positive_int, default=30)
    parser.add_argument("--limit", type=positive_int, default=5)
    parser.add_argument("--exclude-user", action="append", default=[])
    parser.add_argument("--exclude-app", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if os.environ.get("MONITOR_CONDITION") == "Resolved":
        logger.info("Alert is Resolved: nothing to do.")
        return 0

    try:
        connection = connect(args.host, args.database, args.user)
        try:
            return run(connection, args)
        finally:
            connection.close()
    except Exception:
        logger.exception("Fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
