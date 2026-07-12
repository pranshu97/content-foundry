"""LLM provider protocol + shared helpers (Ch. 3.4).

Agents depend on the :class:`LLMProvider` protocol — never on a vendor SDK. Concrete adapters
import their SDKs lazily (inside methods) so the package imports cleanly without them installed.
"""

from __future__ import annotations

import re
import threading
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class LLMResponse(BaseModel):
    text: str
    model: str
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


@runtime_checkable
class LLMProvider(Protocol):
    """One method, two impls (Anthropic/OpenAI) + a fallback wrapper."""

    name: str

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> LLMResponse: ...


def extract_json(text: str) -> str:
    """Best-effort extraction of a single JSON object from an LLM response.

    Strips ``` fences and slices between the first ``{`` and last ``}``.
    """
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    return t


def run_interruptible(call):
    """Run a blocking callable in a daemon thread so Ctrl+C (SIGINT) is honored PROMPTLY even while a
    provider SDK / HTTP client is blocked on a socket read. A blocking network read does not process
    the signal until it returns (notably on Windows), which is why a long call otherwise seems to
    ignore Ctrl+C. The main thread waits in a short interruptible loop and returns AT ONCE on
    KeyboardInterrupt (the worker is a daemon, so it never delays process exit). On interrupt we also
    ASK THE WORKER TO UNWIND: an async exception is scheduled into it so the abandoned request retires
    the instant its blocking read returns, instead of running to completion in the background. The
    extra thread + 0.1s poll add no measurable overhead on the happy path."""
    box: dict = {}

    def worker() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - surfaced on the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(timeout=0.1)  # back to the interpreter ~10x/s so a pending SIGINT is seen fast
    except BaseException:  # Ctrl+C (or similar) landed on the main thread while we waited
        _raise_in_thread(thread, SystemExit)  # retire the abandoned worker when its call returns
        raise
    if "error" in box:
        raise box["error"]
    return box["value"]


def _raise_in_thread(thread: threading.Thread, exc_type: type) -> None:
    """Best-effort: schedule ``exc_type`` to be raised inside ``thread`` at its next bytecode. Used to
    retire an abandoned daemon worker once its in-flight blocking call returns (it cannot interrupt the
    C-level socket read itself, only the moment control comes back to Python). A no-op if the thread
    already finished or the CPython C-API is unavailable."""
    tid = thread.ident
    if tid is None or not thread.is_alive():
        return
    try:
        import ctypes

        count = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(tid), ctypes.py_object(exc_type)
        )
        if count > 1:  # somehow targeted >1 thread — undo so we don't corrupt an unrelated one
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
    except Exception:  # pragma: no cover - ctypes/pythonapi missing (non-CPython)
        pass
