from utils import (
    _format_requested_at,
    _utc_now_iso,
    encode_logs,
    get_sanitized_env,
)


def test_time_utils():
    now_iso = _utc_now_iso()
    assert isinstance(now_iso, str)
    assert "T" in now_iso

    req_at = _format_requested_at()
    assert isinstance(req_at, str)


def test_encode_logs():
    assert encode_logs(None) == b""
    assert encode_logs("") == b""
    assert encode_logs("hello world") == b"aGVsbG8gd29ybGQ="


def test_get_sanitized_env():
    env = {
        "APP_ENV": "production",
        "API_TOKEN": "my-secret-token",
        "DB_PASSWORD": "supersecretpassword",
        "MY_SECRET": "shhh",
        "AUTH_HEADER": "Bearer xyz",
        "INTERNALAUTHAPISALLOWLIST": "allow-all",
        "SITETOKENISSUINGMODE": "enabled",
        "STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=...",
        "APP_NAME": "payment-service",
        "ALREADY_MASKED": "******",
        "EMPTY_KEY": "",
    }

    sanitized = get_sanitized_env(env)

    assert sanitized["APP_ENV"] == "production"
    assert sanitized["API_TOKEN"] == "******"
    assert sanitized["DB_PASSWORD"] == "******"
    assert sanitized["MY_SECRET"] == "******"
    assert sanitized["AUTH_HEADER"] == "******"
    assert sanitized["INTERNALAUTHAPISALLOWLIST"] == "******"
    assert sanitized["SITETOKENISSUINGMODE"] == "******"
    assert sanitized["STORAGE_CONNECTION_STRING"] == "******"
    assert sanitized["ALREADY_MASKED"] == "******"
    assert sanitized["EMPTY_KEY"] == ""
    assert sanitized["APP_NAME"] == "payment-service"
