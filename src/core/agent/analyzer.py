import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import copilot_provider
import utils
from models import FailedRunbookAlert

# =========================
# Configuration
# =========================
#
# "openai" (default), "azure_openai" or "copilot_sdk" (GitHub Copilot token).
DEFAULT_LLM_PROVIDER = os.getenv("AGENT_LLM_PROVIDER", "openai")
DEFAULT_LLM_MODEL = os.getenv("AGENT_LLM_MODEL", "gpt-4o-mini")
DEFAULT_LLM_TIMEOUT_SECONDS = int(os.getenv("AGENT_LLM_TIMEOUT_SECONDS", "30"))
DEFAULT_MAX_LOG_CHARS = int(os.getenv("AGENT_MAX_LOG_CHARS", "8000"))

# Backward/test-friendly module-level defaults (see functions below for the
# live, settings-aware values actually used at request time).
LLM_PROVIDER = DEFAULT_LLM_PROVIDER
LLM_MODEL = DEFAULT_LLM_MODEL
LLM_TIMEOUT_SECONDS = DEFAULT_LLM_TIMEOUT_SECONDS
MAX_LOG_CHARS = DEFAULT_MAX_LOG_CHARS

SYSTEM_PROMPT = (
    "You are ClouDO's on-call assistant. You analyze failed runbook executions "
    "and the alert that triggered them, and produce a short, actionable triage "
    "note for the engineer who will be paged. Always answer with a single JSON "
    "object with keys: summary (string), probable_root_cause (string), "
    "recommended_actions (array of strings), confidence (one of 'low', "
    "'medium', 'high'). Do not include any text outside the JSON object."
)


def _llm_provider() -> str:
    return utils.get_setting("AGENT_LLM_PROVIDER", DEFAULT_LLM_PROVIDER)


def _llm_model() -> str:
    return utils.get_setting("AGENT_LLM_MODEL", DEFAULT_LLM_MODEL)


def _llm_timeout_seconds() -> int:
    try:
        return int(
            utils.get_setting(
                "AGENT_LLM_TIMEOUT_SECONDS", str(DEFAULT_LLM_TIMEOUT_SECONDS)
            )
        )
    except ValueError:
        return DEFAULT_LLM_TIMEOUT_SECONDS


def _max_log_chars() -> int:
    try:
        return int(utils.get_setting("AGENT_MAX_LOG_CHARS", str(DEFAULT_MAX_LOG_CHARS)))
    except ValueError:
        return DEFAULT_MAX_LOG_CHARS


def _copilot_token() -> str:
    return utils.get_setting("COPILOT_GITHUB_TOKEN")


def _copilot_model() -> str:
    return utils.get_setting("COPILOT_MODEL", "")


@dataclass
class AnalysisResult:
    summary: str
    probable_root_cause: str
    recommended_actions: list
    confidence: str = "low"
    raw_response: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "probable_root_cause": self.probable_root_cause,
            "recommended_actions": self.recommended_actions,
            "confidence": self.confidence,
        }


def _truncate_tail(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


def build_prompt(alert: FailedRunbookAlert) -> str:
    resource_info = json.dumps(alert.resource_info, ensure_ascii=False, indent=2)
    # Alert logs/resource_info come from arbitrary upstream systems and can
    # carry invisible Unicode whitespace.
    logs = utils.sanitize_setting_value(_truncate_tail(alert.logs, _max_log_chars()))
    resource_info = utils.sanitize_setting_value(resource_info)
    return (
        f"Runbook execution status: {alert.status}\n"
        f"Schema id: {alert.id}\n"
        f"Schema name: {alert.name}\n"
        f"Runbook: {alert.runbook} (args: {alert.run_args})\n"
        f"Monitor condition: {alert.monitor_condition}\n"
        f"Severity: {alert.severity}\n"
        f"Resource info:\n{resource_info}\n\n"
        f"Execution logs (tail):\n{logs}\n"
    )


def _get_client():
    """Lazily build the LLM client so importing this module never requires
    API credentials."""
    provider = _llm_provider()
    if provider == "azure_openai":
        from openai import AzureOpenAI

        api_key = utils.get_setting("AZURE_OPENAI_API_KEY")
        endpoint = utils.get_setting("AZURE_OPENAI_ENDPOINT")
        if not api_key or not endpoint:
            raise ValueError(
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be configured "
                "(admin UI -> AI Agent, or env vars) when AGENT_LLM_PROVIDER=azure_openai"
            )
        return AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=utils.get_setting(
                "AZURE_OPENAI_API_VERSION", "2024-08-01-preview"
            ),
        )

    from openai import OpenAI

    api_key = utils.get_setting("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY must be configured (admin UI -> AI Agent, or env var)"
        )
    return OpenAI(api_key=api_key, timeout=_llm_timeout_seconds())


def _strip_markdown_fence(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    stripped = stripped[3:]
    if stripped.lower().startswith("json"):
        stripped = stripped[4:]
    stripped = stripped.strip()
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    return stripped.strip()


def _parse_response(text: str) -> AnalysisResult:
    text = _strip_markdown_fence(text)
    snippet = (text or "").strip().replace("\n", " ")[:300]
    try:
        data = json.loads(text)
        logging.debug(
            "Agent: LLM response parsed OK (%d chars): %s", len(text or ""), snippet
        )
        return AnalysisResult(
            summary=str(data.get("summary", "")).strip(),
            probable_root_cause=str(data.get("probable_root_cause", "")).strip(),
            recommended_actions=[
                str(a)
                for a in (data.get("recommended_actions") or [])
                if str(a).strip()
            ],
            confidence=str(data.get("confidence", "low")).strip().lower() or "low",
            raw_response=text,
        )
    except (TypeError, ValueError, AttributeError) as exc:
        logging.warning(
            "Agent: failed to parse LLM response as JSON: %s (response was %d chars: %r)",
            exc,
            len(text or ""),
            snippet,
        )
        return AnalysisResult(
            summary=(text or "").strip()[:500],
            probable_root_cause="",
            recommended_actions=[],
            confidence="low",
            raw_response=text,
            error=f"unparsable_response: {exc}",
        )


def analyze(alert: FailedRunbookAlert) -> AnalysisResult:
    """Send the failed runbook execution to the configured LLM and return a
    structured triage result.
    """
    provider = _llm_provider()
    started_at = time.monotonic()
    logging.info(
        "[%s] Agent: starting LLM analysis (provider=%s, model=%s)",
        alert.exec_id,
        provider,
        _copilot_model() if provider == "copilot_sdk" else _llm_model(),
    )
    try:
        if provider == "copilot_sdk":
            result = _analyze_with_copilot(alert)
        else:
            result = _analyze_with_openai_compatible(alert)
        logging.info(
            "[%s] Agent: LLM analysis finished in %.2fs (confidence=%s, error=%s)",
            alert.exec_id,
            time.monotonic() - started_at,
            result.confidence,
            result.error,
        )
        return result
    except Exception as exc:
        # exc_info=True: this is the single most useful place in the service
        # to see a full traceback, since analyze() never raises past here.
        logging.error(
            "[%s] Agent: LLM analysis failed after %.2fs (provider=%s): %s",
            alert.exec_id,
            time.monotonic() - started_at,
            provider,
            exc,
            exc_info=True,
        )
        return AnalysisResult(
            summary="AI analysis unavailable.",
            probable_root_cause="",
            recommended_actions=[],
            confidence="low",
            error=str(exc),
        )


def _analyze_with_openai_compatible(alert: FailedRunbookAlert) -> AnalysisResult:
    prompt = build_prompt(alert)
    client = _get_client()
    logging.debug(
        "[%s] Agent: sending prompt to %s (%d chars)",
        alert.exec_id,
        _llm_provider(),
        len(prompt),
    )
    response = client.chat.completions.create(
        model=_llm_model(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    text = response.choices[0].message.content or ""
    return _parse_response(text)


def _analyze_with_copilot(alert: FailedRunbookAlert) -> AnalysisResult:
    # The Copilot SDK has no separate system-message concept for a single
    # text-completion turn, so the instructions are prepended to the prompt.
    prompt = f"{SYSTEM_PROMPT}\n\n{build_prompt(alert)}"
    logging.debug(
        "[%s] Agent: sending prompt to copilot_sdk (%d chars, model=%s)",
        alert.exec_id,
        len(prompt),
        _copilot_model() or "<default>",
    )
    text = copilot_provider.invoke(
        prompt, token=_copilot_token(), model=_copilot_model()
    )
    return _parse_response(text)
