"""Fixed in-container JSON-RPC runner used by :mod:`language_tools`."""
# This is deliberately a fixed, controller-owned program.  User/model input is
# JSON data in argv[1], never Python source or a shell command.
LSP_HELPER = r'''import json
import math
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

MAX_MESSAGE_BYTES = 512 * 1024
MAX_RESULTS = 128
MAX_TEXT_CHARS = 4096
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 60 * 1024
SUPPORTED = {
    ".py": "python", ".pyi": "python", ".ts": "typescript",
    ".tsx": "typescriptreact", ".js": "javascript", ".jsx": "javascriptreact",
}

class ProtocolError(Exception):
    pass


def fail(message):
    print(json.dumps({"ok": False, "error": str(message)[:1024]}, ensure_ascii=False, separators=(",", ":")))
    return 1


def safe_path(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if "\\" in value or value.startswith("/") or "\x00" in value:
        return None
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return value


def utf16_character(line, column):
    if not isinstance(column, int) or isinstance(column, bool) or column < 1:
        raise ProtocolError("column must be a positive integer")
    if column > len(line) + 1:
        raise ProtocolError("column is outside the source line")
    return len(line[:column - 1].encode("utf-16-le")) // 2


def codepoint_column(line, units):
    if not isinstance(units, int) or isinstance(units, bool) or units < 0:
        raise ProtocolError("server returned an invalid UTF-16 character")
    consumed = 0
    for index, character in enumerate(line):
        width = 2 if ord(character) > 0xffff else 1
        if units < consumed + width:
            return index + 1
        consumed += width
    if units > consumed:
        raise ProtocolError("server returned a UTF-16 character outside the source line")
    return len(line) + 1


def lines_for(text):
    return [part[:-1] if part.endswith("\r") else part for part in text.split("\n")]


class Connection:
    def __init__(self, process, deadline):
        self.process = process
        self.deadline = deadline
        self.buffer = bytearray()
        self.fd = process.stdout.fileno()

    def _fill(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("language server timed out")
        ready, _, _ = select.select([self.fd], [], [], remaining)
        if not ready:
            raise TimeoutError("language server timed out")
        chunk = os.read(self.fd, 65536)
        if not chunk:
            raise ProtocolError("language server closed stdout")
        self.buffer.extend(chunk)
        if len(self.buffer) > MAX_MESSAGE_BYTES * 2:
            raise ProtocolError("language server framing buffer is oversized")

    def _read_bytes(self, count):
        while len(self.buffer) < count:
            self._fill()
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value

    def send(self, message):
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_MESSAGE_BYTES:
            raise ProtocolError("language server message is oversized")
        frame = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
        try:
            self.process.stdin.write(frame)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ProtocolError("language server stdin closed") from exc

    def read(self):
        while b"\r\n\r\n" not in self.buffer:
            self._fill()
        marker = self.buffer.index(b"\r\n\r\n")
        header = bytes(self.buffer[:marker])
        del self.buffer[:marker + 4]
        length = None
        for line in header.split(b"\r\n"):
            if b":" not in line:
                raise ProtocolError("malformed language server header")
            key, value = line.split(b":", 1)
            if key.lower() == b"content-length":
                if length is not None:
                    raise ProtocolError("duplicate language server content length")
                try:
                    length = int(value.strip())
                except ValueError as exc:
                    raise ProtocolError("malformed language server content length") from exc
        if length is None or length <= 0 or length > MAX_MESSAGE_BYTES:
            raise ProtocolError("invalid language server content length")
        try:
            message = json.loads(self._read_bytes(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("malformed language server JSON") from exc
        if not isinstance(message, dict):
            raise ProtocolError("language server message is not an object")
        return message


def request(connection, next_id, method, params, diagnostics, target_uri):
    connection.send({"jsonrpc": "2.0", "id": next_id, "method": method, "params": params})
    while True:
        message = connection.read()
        method_name = message.get("method")
        if method_name is not None:
            handle_message(connection, message, diagnostics, target_uri)
            continue
        if message.get("id") != next_id:
            continue
        if "error" in message:
            error = message.get("error")
            raise ProtocolError("language server request failed: " + str(error)[:512])
        if "result" not in message:
            raise ProtocolError("language server response has neither result nor error")
        return message["result"]


def handle_message(connection, message, diagnostics, target_uri):
    method = message.get("method")
    params = message.get("params")
    if method == "textDocument/publishDiagnostics" and isinstance(params, dict):
        uri = params.get("uri")
        if uri == target_uri:
            values = params.get("diagnostics")
            if not isinstance(values, list):
                raise ProtocolError("language server diagnostics are malformed")
            diagnostics["seen"] = True
            diagnostics["items"] = values[:MAX_RESULTS]
            diagnostics["truncated"] = len(values) > MAX_RESULTS
    if "id" in message:
        request_id = message.get("id")
        if method == "workspace/configuration":
            items = params.get("items", []) if isinstance(params, dict) else []
            result = [{} for _ in items] if isinstance(items, list) else []
        else:
            result = None
        connection.send({"jsonrpc": "2.0", "id": request_id, "result": result})


def uri_path(root, uri):
    if not isinstance(uri, str):
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    raw = unquote(parsed.path)
    if not raw.startswith("/"):
        return None
    candidate = Path(raw).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    return relative.as_posix()


def location_result(root, location, cache):
    if not isinstance(location, dict):
        return None
    uri = location.get("targetUri", location.get("uri"))
    range_value = location.get("targetSelectionRange", location.get("range"))
    relative = uri_path(root, uri)
    if relative is None or not isinstance(range_value, dict):
        return None
    start = range_value.get("start")
    end = range_value.get("end", start)
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    try:
        if relative not in cache["lines"]:
            target = root / relative
            size = target.stat().st_size
            if size > MAX_FILE_BYTES or size + cache["bytes"] > MAX_CACHE_BYTES:
                return None
            with target.open("rb") as source:
                data = source.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES or len(data) + cache["bytes"] > MAX_CACHE_BYTES:
                return None
            cache["lines"][relative] = lines_for(data.decode("utf-8"))
            cache["bytes"] += len(data)
        lines = cache["lines"][relative]
        start_line = start["line"]
        end_line = end["line"]
        if not isinstance(start_line, int) or isinstance(start_line, bool) or not 0 <= start_line < len(lines):
            return None
        if not isinstance(end_line, int) or isinstance(end_line, bool) or not 0 <= end_line < len(lines):
            return None
        result_range = {
            "start": {"line": start_line + 1, "column": codepoint_column(lines[start_line], start["character"])},
            "end": {"line": end_line + 1, "column": codepoint_column(lines[end_line], end["character"])},
        }
    except (KeyError, OSError, UnicodeError, ProtocolError):
        return None
    return {"path": relative, "range": result_range, "line": result_range["start"]["line"], "column": result_range["start"]["column"]}


def diagnostic_result(root, uri, diagnostic, cache):
    if not isinstance(diagnostic, dict):
        return None
    item = location_result(root, {"uri": uri, "range": diagnostic.get("range")}, cache)
    if item is None:
        return None
    message = diagnostic.get("message")
    if not isinstance(message, str):
        return None
    item["message"] = message[:MAX_TEXT_CHARS]
    for key in ("severity", "source", "code"):
        value = diagnostic.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            item[key] = value if not isinstance(value, str) else value[:256]
    return item


def ts_diagnostic(value):
    if not isinstance(value, dict) or not isinstance(value.get("message"), str):
        raise ProtocolError("tsserver diagnostic is malformed")
    severity = {"error": 1, "warning": 2, "message": 3, "suggestion": 4}.get(value.get("category"))
    if severity is None:
        raise ProtocolError("tsserver diagnostic category is unknown")
    def position(name):
        point = value.get(name)
        if not isinstance(point, dict):
            raise ProtocolError("tsserver diagnostic position is missing")
        line, column = point.get("line"), point.get("offset")
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in (line, column)):
            raise ProtocolError("tsserver diagnostic position is malformed")
        return {"line": line - 1, "character": column - 1}
    return {
        "range": {"start": position("startLocation"), "end": position("endLocation")},
        "message": value["message"], "severity": severity,
        "source": "typescript", "code": value.get("code"),
    }


def main():
    try:
        payload = json.loads(sys.argv[1])
        if not isinstance(payload, dict):
            return fail("helper payload is not an object")
        action = payload.get("action")
        path = safe_path(payload.get("path"))
        if action not in ("definition", "references", "diagnostics") or path is None:
            return fail("invalid language query")
        suffix = Path(path).suffix.lower()
        language_id = SUPPORTED.get(suffix)
        if language_id is None:
            return fail("unsupported source file type")
        timeout = payload.get("timeout_seconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(float(timeout)) or not 0 < float(timeout) <= 30:
            return fail("invalid language query timeout")
        root = Path.cwd().resolve()
        source = (root / path).resolve()
        try:
            source.relative_to(root)
        except ValueError:
            return fail("source path is outside candidate")
        if not source.is_file() or source.stat().st_size > MAX_FILE_BYTES:
            return fail("source file is missing or oversized")
        text = source.read_text(encoding="utf-8")
        source_lines = lines_for(text)
        line = payload.get("line", 1)
        column = payload.get("column")
        if not isinstance(line, int) or isinstance(line, bool) or not 1 <= line <= len(source_lines):
            return fail("line is outside the source file")
        if column is None:
            column = 1
        if not isinstance(column, int) or isinstance(column, bool):
            return fail("column is invalid")
        position = {"line": line - 1, "character": utf16_character(source_lines[line - 1], column)}
        target_uri = source.as_uri()
        command = ["pyright-langserver", "--stdio"] if language_id == "python" else ["typescript-language-server", "--stdio"]
        process = subprocess.Popen(command, cwd=str(root), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + float(timeout)
        connection = Connection(process, deadline)
        diagnostics = {"seen": False, "items": [], "truncated": False}
        next_id = 1
        try:
            initialize = {
                "processId": None,
                "clientInfo": {"name": "goal-native-language-tools", "version": "1"},
                "rootUri": root.as_uri(),
                "workspaceFolders": [{"uri": root.as_uri(), "name": root.name or "candidate"}],
                "capabilities": {
                    "workspace": {"workspaceFolders": True, "configuration": True},
                    "textDocument": {
                        "definition": {"linkSupport": True},
                        "references": {},
                        "publishDiagnostics": {"relatedInformation": True},
                        "synchronization": {"dynamicRegistration": False, "didSave": False},
                    },
                },
                "trace": "off",
            }
            if language_id != "python":
                initialize["initializationOptions"] = {"tsserver": {"useSyntaxServer": "never"}}
            initialize_result = request(connection, next_id, "initialize", initialize, diagnostics, target_uri)
            next_id += 1
            if not isinstance(initialize_result, dict):
                raise ProtocolError("language server initialize result is malformed")
            connection.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            connection.send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": target_uri, "languageId": language_id, "version": 1, "text": text}}})
            if language_id != "python":
                # Push notifications may contain only syntax diagnostics (even
                # an empty list). Wait for explicit semantic-server responses.
                commands = ("syntacticDiagnosticsSync", "semanticDiagnosticsSync", "suggestionDiagnosticsSync") if action == "diagnostics" else ("semanticDiagnosticsSync",)
                collected = []
                clipped = False
                for command_name in commands:
                    response = request(connection, next_id, "workspace/executeCommand", {
                        "command": "typescript.tsserverRequest",
                        "arguments": [command_name, {"file": target_uri, "includeLinePosition": True}, {"executionTarget": 0, "expectsResult": True}],
                    }, diagnostics, target_uri)
                    next_id += 1
                    if not isinstance(response, dict) or response.get("success") is not True or not isinstance(response.get("body"), list):
                        raise ProtocolError("tsserver semantic query did not complete")
                    if action == "diagnostics":
                        for diagnostic in response["body"]:
                            if len(collected) < MAX_RESULTS:
                                collected.append(ts_diagnostic(diagnostic))
                            else:
                                clipped = True
                diagnostics = {"seen": True, "items": collected, "truncated": clipped}
            else:
                while not diagnostics["seen"]:
                    message = connection.read()
                    if message.get("method") is not None:
                        handle_message(connection, message, diagnostics, target_uri)
                    elif message.get("id") is not None:
                        handle_message(connection, message, diagnostics, target_uri)
            if action == "diagnostics":
                raw_results = diagnostics["items"]
                results = []
                omitted = 0
                cache = {"lines": {}, "bytes": 0}
                for diagnostic in raw_results:
                    item = diagnostic_result(root, target_uri, diagnostic, cache)
                    if item is None:
                        omitted += 1
                    elif len(results) < MAX_RESULTS:
                        results.append(item)
                    else:
                        omitted += 1
                complete = True
            else:
                method = "textDocument/definition" if action == "definition" else "textDocument/references"
                params = {"textDocument": {"uri": target_uri}, "position": position}
                if action == "references":
                    params["context"] = {"includeDeclaration": True}
                raw = request(connection, next_id, method, params, diagnostics, target_uri)
                next_id += 1
                if raw is None:
                    raw = []
                if isinstance(raw, dict) and action == "definition":
                    raw = [raw]
                if not isinstance(raw, list):
                    raise ProtocolError("language server navigation result is malformed")
                results = []
                omitted = max(0, len(raw) - MAX_RESULTS)
                cache = {"lines": {}, "bytes": 0}
                for location in raw[:MAX_RESULTS]:
                    item = location_result(root, location, cache)
                    if item is None:
                        omitted += 1
                    elif len(results) < MAX_RESULTS:
                        results.append(item)
                    else:
                        omitted += 1
                complete = True
            result = {"ok": True, "action": action, "path": path, "results": [], "complete": complete, "truncated": diagnostics["truncated"] if action == "diagnostics" else False, "omitted": omitted + len(results)}
            remaining = MAX_OUTPUT_BYTES - len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) - 64
            for item in results:
                size = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
                if size > remaining:
                    omitted += 1
                else:
                    result["results"].append(item)
                    remaining -= size
            result["truncated"] = result["truncated"] or omitted > 0
            if omitted:
                result["omitted"] = omitted
            else:
                del result["omitted"]
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            return 0
        finally:
            try:
                if process.poll() is None:
                    try:
                        request(connection, next_id, "shutdown", None, diagnostics, target_uri)
                    except Exception:
                        pass
                    try:
                        connection.send({"jsonrpc": "2.0", "method": "exit", "params": None})
                    except Exception:
                        pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
                    process.kill()
                    process.wait()
    except FileNotFoundError as exc:
        return fail("language server executable is unavailable: " + str(exc.filename))
    except json.JSONDecodeError as exc:
        return fail("helper payload is malformed JSON: " + str(exc))
    except (TimeoutError, ProtocolError, OSError, UnicodeError, ValueError) as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
'''
