"""Bounded live activity logging for Windsor Slicer MCP.

The activity stream is intentionally separate from protocol output. It records
sanitized MCP tool calls/results and child-process commands/results so a
Codespace operator can watch what the AI is doing without retaining an
unbounded log.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_SECRET_KEY_RE = re.compile(
    r"(authorization|bearer|token|secret|password|credential|cookie)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(bearer\s+)[^\s\"']+", re.IGNORECASE)
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_DEFAULT_BACKUPS = 3
_DEFAULT_FIELD_CHARS = 4000

_logger = logging.getLogger("windsor_slicer.activity")
_logger.setLevel(logging.INFO)
_logger.propagate = False
_configured = False


def _enabled(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_VALUES


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    if key and _SECRET_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        if key and key.lower() == "base64":
            return f"[OMITTED base64: {len(value)} chars]"
        text = _BEARER_RE.sub(r"\1[REDACTED]", value)
        limit = _bounded_int(
            "SLICER_MCP_ACTIVITY_FIELD_CHARS",
            _DEFAULT_FIELD_CHARS,
            256,
            50000,
        )
        if len(text) > limit:
            return text[:limit] + f"... [truncated {len(text) - limit} chars]"
        return text
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return repr(value)


def _render(value: Any) -> str:
    return json.dumps(
        _sanitize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    formatter = logging.Formatter(
        "%(asctime)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    if _enabled("SLICER_MCP_ACTIVITY_STDERR", True):
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        _logger.addHandler(stream)

    log_path = os.environ.get("SLICER_MCP_ACTIVITY_LOG", "").strip()
    if log_path:
        path = Path(log_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = _bounded_int(
            "SLICER_MCP_ACTIVITY_MAX_BYTES",
            _DEFAULT_MAX_BYTES,
            64 * 1024,
            100 * 1024 * 1024,
        )
        backups = _bounded_int(
            "SLICER_MCP_ACTIVITY_BACKUPS",
            _DEFAULT_BACKUPS,
            1,
            10,
        )
        rotating = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backups,
            encoding="utf-8",
        )
        rotating.setFormatter(formatter)
        _logger.addHandler(rotating)


def tool_request(name: str, arguments: dict[str, Any]) -> float:
    _configure()
    started = time.monotonic()
    _logger.info("TOOL -> %s args=%s", name, _render(arguments))
    return started


def tool_response(name: str, result: Any, started: float) -> None:
    _configure()
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    _logger.info(
        "TOOL <- %s duration_ms=%s result=%s",
        name,
        elapsed_ms,
        _render(result),
    )


def tool_error(name: str, error: BaseException, started: float) -> None:
    _configure()
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    _logger.info(
        "TOOL !! %s duration_ms=%s error=%s",
        name,
        elapsed_ms,
        _render({"type": type(error).__name__, "message": str(error)}),
    )


def command_request(command: list[str], *, cwd: Path | None = None) -> float:
    _configure()
    started = time.monotonic()
    rendered = shlex.join(str(part) for part in command)
    _logger.info(
        "CMD  -> cwd=%s $ %s",
        str(cwd) if cwd is not None else "",
        _sanitize(rendered),
    )
    return started


def command_response(
    command: list[str],
    *,
    returncode: int,
    stdout: str | None,
    stderr: str | None,
    started: float,
) -> None:
    _configure()
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    _logger.info(
        "CMD  <- exit=%s duration_ms=%s output=%s",
        returncode,
        elapsed_ms,
        _render({"stdout": stdout or "", "stderr": stderr or ""}),
    )


def command_error(command: list[str], error: BaseException, started: float) -> None:
    _configure()
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    _logger.info(
        "CMD  !! duration_ms=%s error=%s",
        elapsed_ms,
        _render({"type": type(error).__name__, "message": str(error)}),
    )
