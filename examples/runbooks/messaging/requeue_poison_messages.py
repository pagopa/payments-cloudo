#!/usr/bin/env python3
"""
Runbook Name: Requeue Poison Messages
Description: Moves messages from a poison queue back to the original Storage Queue, once
             the cause of the failures is fixed. Prints the plan unless --apply is set.
             Each message is sent before it is deleted, so a failure midway can duplicate
             a message but never loses one: the consumer must be idempotent. Message
             content is never logged because it can hold customer data.
Trigger: Manual, with approval.
Usage: python3 requeue_poison_messages.py --account-url <url> --queue <name>
       [--poison-queue <name>] [--limit 100] [--apply]
Requires: The worker identity needs the Storage Queue Data Contributor role.
Schema: "runbook": "messaging/requeue_poison_messages.py",
        "run_args": "--account-url https://stpayments.queue.core.windows.net --queue notifications --limit 50 --apply",
        "require_approval": true
"""

import argparse
import logging
import sys

from azure.identity import DefaultAzureCredential
from azure.storage.queue import QueueClient

# Long enough to send and delete a message, short enough to retry soon if this run dies.
VISIBILITY_TIMEOUT_SECONDS = 60

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


def requeue(source: QueueClient, target: QueueClient, limit: int) -> int:
    """Moves up to `limit` messages, which also bounds a run if they keep failing."""
    moved = 0
    while moved < limit:
        message = source.receive_message(visibility_timeout=VISIBILITY_TIMEOUT_SECONDS)
        if message is None:
            break
        # Neither client sets an encoding, so the content is copied exactly as stored.
        target.send_message(message.content)
        source.delete_message(message)
        moved += 1
        logger.info(
            "Requeued message %s (dequeued %s times)",
            message.id,
            message.dequeue_count,
        )
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description="Requeue poison messages")
    parser.add_argument("--account-url", required=True)
    parser.add_argument("--queue", required=True, help="Original queue")
    parser.add_argument("--poison-queue", help="Defaults to <queue>-poison")
    parser.add_argument("--limit", type=positive_int, default=100)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    poison_queue = args.poison_queue or f"{args.queue}-poison"

    try:
        credential = DefaultAzureCredential()
        source = QueueClient(args.account_url, poison_queue, credential=credential)
        target = QueueClient(args.account_url, args.queue, credential=credential)

        # The target must exist: this fails before any message is touched otherwise.
        target_count = target.get_queue_properties().approximate_message_count
        source_count = source.get_queue_properties().approximate_message_count
        logger.info(
            "About %s messages in %s, %s in %s.",
            source_count,
            poison_queue,
            target_count,
            args.queue,
        )

        if not args.apply:
            logger.info(
                "Dry run: would move up to %s messages. Add --apply to run it.",
                args.limit,
            )
            return 0

        moved = requeue(source, target, args.limit)
        logger.info("Moved %s messages to %s.", moved, args.queue)
        return 0
    except Exception:
        logger.exception("Fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
