"""Disposable-container language-server queries for staged source.

The controller owns the container boundary.  This module only validates a small
request, asks that boundary to run a fixed helper, and validates the helper's
bounded JSON result.  No language server is ever started on the host.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

from .language_runner import LSP_HELPER

MAX_TIMEOUT_SECONDS = 30.0
MAX_REQUEST_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_RESULTS = 128
MAX_PATH_CHARS = 4096
MAX_LINE = 1_000_000
MAX_COLUMN = 1_000_000


_SUPPORTED_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
}
_ACTIONS = frozenset(("definition", "references", "diagnostics"))


class LanguageToolsError(ValueError):
    """A language-server query was rejected or could not complete safely."""


def _safe_relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_CHARS:
        return None
    if "\\" in value or value.startswith("/") or "\x00" in value:
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    try:
        candidate = PurePosixPath(value)
    except (TypeError, ValueError):
        return None
    if candidate.is_absolute() or str(candidate) != value:
        return None
    return value


def _before_identity(command_result: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Use the exact controller-owned identity of the queried snapshot."""
    before = command_result.get("before")
    if not isinstance(before, Mapping):
        return None, None
    candidate, image = before.get("candidate"), before.get("image")
    if not isinstance(candidate, str) or not isinstance(image, str):
        return None, None
    return candidate, image


def _error_text(value: Any) -> str:
    if not isinstance(value, str):
        return "language-server command failed"
    return value[:1024].replace("\x00", "")

def _sanitize_result(item: Mapping[str, Any], action: str) -> dict[str, Any] | None:
    result_path = _safe_relative_path(item.get("path"))
    if result_path is None:
        return None
    result: dict[str, Any] = {"path": result_path}

    def position(value: Any) -> dict[str, int]:
        if not isinstance(value, Mapping):
            raise LanguageToolsError("language-server result position is malformed")
        line = value.get("line")
        column = value.get("column")
        if (
            isinstance(line, bool)
            or not isinstance(line, int)
            or not 1 <= line <= MAX_LINE
            or isinstance(column, bool)
            or not isinstance(column, int)
            or not 1 <= column <= MAX_COLUMN
        ):
            raise LanguageToolsError("language-server result position is malformed")
        return {"line": line, "column": column}

    raw_range = item.get("range")
    if raw_range is not None:
        if not isinstance(raw_range, Mapping):
            raise LanguageToolsError("language-server result range is malformed")
        result["range"] = {
            "start": position(raw_range.get("start")),
            "end": position(raw_range.get("end")),
        }
    elif "line" in item or "column" in item:
        start = position({"line": item.get("line"), "column": item.get("column")})
        result.update(start)

    if action == "diagnostics":
        message = item.get("message")
        if not isinstance(message, str):
            raise LanguageToolsError("language-server diagnostic message is malformed")
        result["message"] = message[:4096]
        severity = item.get("severity")
        if severity is not None:
            if isinstance(severity, bool) or not isinstance(severity, int) or not 1 <= severity <= 4:
                raise LanguageToolsError("language-server diagnostic severity is malformed")
            result["severity"] = severity
        source = item.get("source")
        if source is not None:
            if not isinstance(source, str):
                raise LanguageToolsError("language-server diagnostic source is malformed")
            result["source"] = source[:256]
        code = item.get("code")
        if code is not None:
            if isinstance(code, bool) or not isinstance(code, (int, str)):
                raise LanguageToolsError("language-server diagnostic code is malformed")
            result["code"] = code[:256] if isinstance(code, str) else code
    return result


class LanguageTools:
    """Query real Python or TypeScript LSP servers inside a disposable runtime."""

    def __init__(self, runtime: Any, *, timeout_seconds: float = MAX_TIMEOUT_SECONDS) -> None:
        if not callable(getattr(runtime, "command", None)):
            raise LanguageToolsError("a container runtime command capability is required")
        self.runtime = runtime
        self.timeout_seconds = self._timeout(timeout_seconds)

    @staticmethod
    def _timeout(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LanguageToolsError("timeout_seconds must be numeric")
        result = float(value)
        if not math.isfinite(result) or result <= 0 or result > MAX_TIMEOUT_SECONDS:
            raise LanguageToolsError(
                f"timeout_seconds must be finite, positive, and at most {MAX_TIMEOUT_SECONDS:g}"
            )
        return result

    def _request(self, request: Mapping[str, Any]) -> tuple[str, str, int, int | None, float]:
        action = request.get("action")
        if not isinstance(action, str) or action not in _ACTIONS:
            raise LanguageToolsError("action must be definition, references, or diagnostics")
        path = _safe_relative_path(request.get("path"))
        if path is None:
            raise LanguageToolsError("path must be a source-relative POSIX path")
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in _SUPPORTED_LANGUAGES:
            raise LanguageToolsError("unsupported source file type")

        line = request.get("line", 1)
        if isinstance(line, bool) or not isinstance(line, int) or not 1 <= line <= MAX_LINE:
            raise LanguageToolsError("line must be a positive bounded integer")
        column_value = request.get("column")
        column: int | None
        if column_value is None:
            column = None
        elif isinstance(column_value, bool) or not isinstance(column_value, int) or not 1 <= column_value <= MAX_COLUMN:
            raise LanguageToolsError("column must be a positive bounded integer")
        else:
            column = column_value
        timeout = request.get("timeout_seconds", self.timeout_seconds)
        return str(action), path, line, column, self._timeout(timeout)

    def query(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise LanguageToolsError("language query must be an object")
        action, path, line, column, timeout = self._request(request)
        payload = {
            "action": action,
            "path": path,
            "line": line,
            "column": column,
            "timeout_seconds": timeout,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise LanguageToolsError("language query is oversized")
        arguments = {
            "argv": ["python3", "-c", LSP_HELPER, encoded],
            "timeout_seconds": timeout,
            "network": False,
        }
        try:
            command_result = self.runtime.command(arguments, publish=False)
        except LanguageToolsError:
            raise
        except Exception as exc:  # Runtime failures must remain visible to the tool caller.
            raise LanguageToolsError(f"language-server runtime failed: {_error_text(str(exc))}") from exc
        if not isinstance(command_result, Mapping):
            raise LanguageToolsError("language-server runtime returned a malformed receipt")

        returncode = command_result.get("exit_code")
        if isinstance(returncode, bool) or not isinstance(returncode, int):
            raise LanguageToolsError("language-server receipt has a malformed exit code")
        if any(command_result.get(key) for key in ("timed_out", "cancelled", "output_limited")):
            raise LanguageToolsError("language-server command did not complete within its limits")
        if returncode != 0:
            detail = _error_text(command_result.get("stderr"))
            raise LanguageToolsError(f"language-server command exited {returncode}: {detail}")
        stdout = command_result.get("stdout")
        if isinstance(stdout, bytes):
            try:
                stdout = stdout.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise LanguageToolsError("language-server output is not UTF-8") from exc
        if not isinstance(stdout, str):
            raise LanguageToolsError("language-server receipt has no stdout")
        if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise LanguageToolsError("language-server output is oversized")
        try:
            response = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LanguageToolsError("language-server output is malformed JSON") from exc
        if not isinstance(response, Mapping):
            raise LanguageToolsError("language-server output is not an object")
        if response.get("ok") is not True:
            raise LanguageToolsError(_error_text(response.get("error")))
        if response.get("action") != action or response.get("path") != path:
            raise LanguageToolsError("language-server output identity does not match the query")
        if response.get("complete") is not True:
            raise LanguageToolsError("language-server query did not complete")
        raw_results = response.get("results")
        if not isinstance(raw_results, list):
            raise LanguageToolsError("language-server results are malformed")

        results: list[dict[str, Any]] = []
        omitted = 0
        for index, item in enumerate(raw_results):
            if not isinstance(item, Mapping):
                raise LanguageToolsError("language-server result entry is malformed")
            sanitized = _sanitize_result(item, action)
            if sanitized is None:
                omitted += 1
                continue
            results.append(sanitized)
            if len(results) >= MAX_RESULTS:
                omitted += len(raw_results) - index - 1
                break
        candidate, image = _before_identity(command_result)
        if candidate is None or image is None:
            raise LanguageToolsError("language-server receipt is missing before candidate/image identity")
        output: dict[str, Any] = {
            "ok": True,
            "action": action,
            "path": path,
            "results": results,
            "complete": True,
            "truncated": bool(response.get("truncated")) or omitted > 0,
            "candidate": candidate,
            "image": image,
        }
        if omitted:
            output["omitted"] = omitted
        return output


