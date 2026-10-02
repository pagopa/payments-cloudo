import pytest
from models import Schema, User


def test_schema_model():
    entity = {
        "name": "Test Runbook",
        "description": "A description",
        "runbook": "my_runbook.py",
        "run_args": "--env prod",
        "worker": "worker-1",
        "group": "payments",
        "oncall": "true",
        "require_approval": "true",
        "enabled": "true",
        "tags": "infra,payments",
        "team": "team-a",
    }
    schema = Schema(id="schema-1", entity=entity)
    assert schema.id == "schema-1"
    assert schema.name == "Test Runbook"
    assert schema.runbook == "my_runbook.py"
    assert schema.require_approval is True
    assert schema.enabled is True
    assert schema.team == "team-a"

    # Validation errors
    with pytest.raises(ValueError, match="Schema id must be a non-empty str"):
        Schema(id="", entity=entity)

    with pytest.raises(ValueError, match="Entity not provided"):
        Schema(id="schema-2", entity=None)


def test_user_model():
    user = User(
        username="admin",
        password="secretpassword",
        email="admin@example.com",
        role="ADMIN",
        team="security",
    )
    assert user.username == "admin"
    assert user.role == "ADMIN"
    assert user.team == "security"

    # Validation errors
    with pytest.raises(ValueError, match="Username must be a non-empty string"):
        User(username="", password="pwd")

    with pytest.raises(ValueError, match="Password must be a non-empty string"):
        User(username="admin", password="")
