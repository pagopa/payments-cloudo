import atexit
import logging
import os
import tempfile
import threading
import time

_INVOKE_TIMEOUT_SECONDS = 120
_STARTUP_TIMEOUT_SECONDS = 180

_lock = threading.Lock()
_runtime = None  # type: "_CopilotRuntime" | None


class _CopilotRuntime:
    """Owns one long-lived Copilot SDK client/session on a background thread."""

    def __init__(self, token: str, model: str):
        self.token = token or None
        self.model = model or None
        self.workdir = os.path.join(tempfile.gettempdir(), "cloudo-agent-copilot-sdk")
        self._asyncio_loop = None
        self._thread = None
        self._client = None
        self._session = None
        self._start_lock = threading.Lock()

    def _ensure_started(self) -> None:
        import asyncio

        with self._start_lock:
            if self._session is not None:
                return

            started_at = time.monotonic()
            logging.info(
                "Agent: starting copilot_sdk runtime (model=%s)...",
                self.model or "<unset, Copilot default>",
            )

            try:
                import copilot as copilot_sdk
            except ImportError as exc:
                logging.error(
                    "Agent: `copilot` package is not importable — the image "
                    "likely needs rebuilding/redeploying with "
                    "github-copilot-sdk in requirements.txt (see Dockerfile)."
                )
                raise ValueError(
                    "AGENT_LLM_PROVIDER=copilot_sdk requires the official SDK: "
                    "`pip install github-copilot-sdk` followed by "
                    "`python -m copilot download-runtime` (already baked into "
                    "this service's Dockerfile)."
                ) from exc

            os.makedirs(self.workdir, exist_ok=True)

            self._asyncio_loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._asyncio_loop.run_forever,
                daemon=True,
                name="agent-copilot-sdk-loop",
            )
            self._thread.start()

            async def _boot():
                client = copilot_sdk.CopilotClient(
                    github_token=self.token,
                    # Never fall back to an interactive machine login: this
                    # runs in a container, there is none, and silently trying
                    # would just hang until the startup timeout.
                    use_logged_in_user=False,
                    working_directory=self.workdir,
                    log_level="error",
                )
                await client.start()
                session = await client.create_session(
                    model=self.model,
                    available_tools=[],  # pure text triage, no tool use
                    streaming=False,
                )
                return client, session

            try:
                self._client, self._session = asyncio.run_coroutine_threadsafe(
                    _boot(), self._asyncio_loop
                ).result(_STARTUP_TIMEOUT_SECONDS)
            except Exception:
                logging.error(
                    "Agent: copilot_sdk runtime failed to start after %.2fs",
                    time.monotonic() - started_at,
                    exc_info=True,
                )
                raise
            logging.info(
                "Agent: copilot_sdk runtime started in %.2fs (model=%s)",
                time.monotonic() - started_at,
                self.model or "<unset, Copilot default>",
            )
            atexit.register(self.close)

    @staticmethod
    def _extract_text(event) -> str:
        if event is None:
            raise ValueError(
                "Copilot SDK returned no event (session went idle with no reply)"
            )
        data = getattr(event, "data", None)
        content = getattr(data, "content", None)
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [
                getattr(block, "text", None)
                or (block.get("text") if isinstance(block, dict) else None)
                for block in content
            ]
            joined = "".join(p for p in parts if p)
            if joined.strip():
                return joined
        raise ValueError("Copilot SDK event carried no usable text")

    def invoke(self, prompt: str) -> str:
        import asyncio

        self._ensure_started()
        started_at = time.monotonic()
        try:
            event = asyncio.run_coroutine_threadsafe(
                self._session.send_and_wait(prompt, timeout=_INVOKE_TIMEOUT_SECONDS),
                self._asyncio_loop,
            ).result(_INVOKE_TIMEOUT_SECONDS + 30)
            text = self._extract_text(event)
            logging.info(
                "Agent: copilot_sdk invoke completed in %.2fs (%d chars returned)",
                time.monotonic() - started_at,
                len(text or ""),
            )
            return text
        except Exception:
            logging.error(
                "Agent: copilot_sdk invoke failed after %.2fs",
                time.monotonic() - started_at,
                exc_info=True,
            )
            raise

    def close(self) -> None:
        """Best-effort shutdown. Safe to call more than once."""
        import asyncio

        with self._start_lock:
            client, loop = self._client, self._asyncio_loop
            self._client = self._session = self._asyncio_loop = None
        if client is not None and loop is not None:
            logging.info("Agent: shutting down copilot_sdk runtime")
            try:
                asyncio.run_coroutine_threadsafe(client.stop(), loop).result(30)
            except Exception:
                logging.warning(
                    "Agent: copilot_sdk client.stop() raised", exc_info=True
                )
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass


def get_runtime(token: str, model: str) -> _CopilotRuntime:
    """Return the process-wide Copilot SDK runtime, restarting it if the
    token or model configured via the admin UI/env changed since it was
    started."""
    global _runtime
    with _lock:
        if _runtime is not None and (
            _runtime.token != (token or None) or _runtime.model != (model or None)
        ):
            logging.info("Agent: copilot_sdk token/model changed, restarting runtime")
            _runtime.close()
            _runtime = None
        if _runtime is None:
            _runtime = _CopilotRuntime(token=token, model=model)
        return _runtime


def invoke(prompt: str, token: str, model: str) -> str:
    if not token:
        raise ValueError(
            "COPILOT_GITHUB_TOKEN must be configured (admin UI -> AI Agent, or env var) "
            "when AGENT_LLM_PROVIDER=copilot_sdk"
        )
    return get_runtime(token=token, model=model).invoke(prompt)
