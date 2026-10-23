# python
"""Tests for the approval gate decision notification."""

from unittest.mock import MagicMock

import escalation
import pytest

from core import notify


@pytest.fixture
def slack(monkeypatch):
    send = MagicMock(return_value=True)
    monkeypatch.setattr(escalation, "send_slack_execution", send)
    monkeypatch.setattr(
        notify, "get_default_notification_settings", lambda: ("tok", "#chan")
    )
    monkeypatch.setenv("NEXTJS_URL", "https://ui.example")
    return send


def _view_url(send):
    actions = [b for b in send.call_args.kwargs["blocks"] if b["type"] == "actions"]
    return actions[0]["elements"][0]["url"]


def test_decision_link_uses_the_given_partition(slack):
    notify.notify_slack_decision(
        "exec-1", "schema-1", "approved", "alice", partition_key="19990101"
    )

    assert _view_url(slack) == (
        "https://ui.example/executions?execId=exec-1&partitionKey=19990101"
    )


def test_decision_link_defaults_to_the_log_row_partition(slack, monkeypatch):
    # Same (Europe/Rome) partition used for log rows, not the UTC date.
    monkeypatch.setattr(notify, "today_partition_key", lambda: "20000101")

    notify.notify_slack_decision("exec-1", "schema-1", "rejected", "bob")

    assert _view_url(slack).endswith("&partitionKey=20000101")


def test_decision_is_not_sent_without_slack_token(slack, monkeypatch):
    monkeypatch.setattr(notify, "get_default_notification_settings", lambda: ("", ""))

    notify.notify_slack_decision("exec-1", "schema-1", "approved", "alice")

    slack.assert_not_called()
