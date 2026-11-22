"""Helpers shared by every tool, so all tools report errors to the model the same way."""

from __future__ import annotations

from typing import Any

MAX_ERRORS_SHOWN = 15


def validation_error_message(exc: Any) -> str:
    """Turn a pydantic ValidationError into a message the model can act on.

    LangChain's default is only "Tool input validation error", which gives the
    model nothing to fix. This lists each bad field with its location.
    """
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return f"Tool input validation error: {exc}"
    lines = []
    for err in errors()[:MAX_ERRORS_SHOWN]:
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(input)"
        lines.append(f"- {loc}: {err.get('msg', 'invalid')}")
    extra = len(errors()) - MAX_ERRORS_SHOWN
    if extra > 0:
        lines.append(f"- ... and {extra} more")
    return "Tool input validation error. Fix these fields and call the tool again:\n" + "\n".join(lines)
