"""Python durability and security supervisor for the upstream pi agent runtime."""

from __future__ import annotations

import json
import math
import os
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .context import CompiledContext, ContextBudget, ContextBudgetError, compile_context
from .sandbox import Sandbox, SandboxError
from .store import Store


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "staged_read",
        "description": "Read UTF-8 text from the staged workspace only.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "staged_write",
        "description": "Write UTF-8 text to the staged workspace only.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 1},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "staged_search",
        "description": "Search staged UTF-8 files for literal text; returns bounded scope and snapshot receipts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_artifact",
        "description": "Read a bounded projection of a durable artifact with provenance and qualifications.",
        "parameters": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 1},
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "staged_run",
        "description": "Run one staged Python script inside enforced OS isolation.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_artifact",
        "description": "Persist a worker-produced candidate with explicit qualifications.",
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "content": {"type": "string"},
                "name": {"type": "string"},
                "inputs": {"type": "array", "items": {"type": "string"}},
                "limitations": {"type": "string"},
            },
            "required": ["kind", "content", "limitations"],
            "additionalProperties": False,
        },
    },
    {
        "name": "finding",
        "description": "Record a worker-reported finding as untrusted evidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "limitations": {"type": "string"},
                "inputs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content", "limitations"],
            "additionalProperties": False,
        },
    },
]


class BridgeError(RuntimeError):
    """The pi bridge could not complete its controlled IPC contract."""


class BridgeRPCError(BridgeError):
    def __init__(self, message: str, status: str = "failed") -> None:
        super().__init__(message)
        self.status = status


class PiBridge:
    """Newline JSON supervisor for the built upstream pi agent package."""

    def __init__(
        self,
        *,
        node: str | None = None,
        bridge_path: str | os.PathLike[str] | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        selected_node = node or shutil.which("node")
        if not selected_node:
            raise BridgeError("Node.js >=22.19 is required for the pi bridge")
        self.node = selected_node
        self.bridge_path = Path(bridge_path) if bridge_path else Path(__file__).parents[1] / "bridge" / "agent.mjs"
        if not self.bridge_path.is_file():
            raise BridgeError(f"pi bridge entrypoint is missing: {self.bridge_path}")
        self.api_key = api_key
        self.timeout_seconds = float(timeout_seconds)
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("bridge timeout must be finite and positive")
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._cancel_requested = threading.Event()
        self._run_gate = threading.Lock()

    def run(
        self,
        request: dict[str, Any],
        *,
        rpc_handler: Callable[[str, dict[str, Any]], dict[str, Any]],
        event_handler: Callable[[dict[str, Any]], None],
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        if not self._run_gate.acquire(blocking=False):
            raise BridgeError("pi bridge already owns an active run")
        try:
            return self._run_once(request, rpc_handler=rpc_handler,
                                  event_handler=event_handler, cancel_event=cancel_event)
        finally:
            self._run_gate.release()

    def _run_once(
        self,
        request: dict[str, Any],
        *,
        rpc_handler: Callable[[str, dict[str, Any]], dict[str, Any]],
        event_handler: Callable[[dict[str, Any]], None],
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        self._cancel_requested.clear()
        requested_timeout = request.get("timeout_ms")
        limit = self.timeout_seconds
        if isinstance(requested_timeout, int) and not isinstance(requested_timeout, bool):
            limit = min(limit, requested_timeout / 1000)
        if limit <= 0:
            raise TimeoutError("pi bridge timeout expired before startup")
        environment = self._controller_environment(request.get("provider", "openai"))
        try:
            process = subprocess.Popen(
                [self.node, str(self.bridge_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                bufsize=0,
            )
        except OSError as exc:
            raise BridgeError(f"could not start pi bridge: {exc}") from exc
        with self._lock:
            self._process = process
        deadline = time.monotonic() + limit
        cancel_sent = False
        stdout_buffer = bytearray()
        stderr_tail = bytearray()
        stdout_open = stderr_open = True
        try:
            self._send(process, request)
            while True:
                if (cancel_event.is_set() or self._cancel_requested.is_set()) and not cancel_sent:
                    self._send(process, {"type": "cancel", "request_id": request["request_id"]})
                    cancel_sent = True
                    deadline = min(deadline, time.monotonic() + 2.0)
                if time.monotonic() >= deadline:
                    self.cancel()
                    raise TimeoutError("pi bridge whole-run timeout expired")
                if process.stdout is None or process.stderr is None:
                    raise BridgeError("pi bridge output pipes are unavailable")
                newline = stdout_buffer.find(b"\n")
                if newline < 0:
                    streams = ([process.stdout] if stdout_open else []) + ([process.stderr] if stderr_open else [])
                    if not streams:
                        break
                    readable, _, _ = select.select(streams, [], [], 0.1)
                    for stream in readable:
                        chunk = os.read(stream.fileno(), 65536)
                        if stream is process.stdout:
                            stdout_open = bool(chunk)
                            stdout_buffer.extend(chunk)
                        else:
                            stderr_open = bool(chunk)
                            stderr_tail.extend(chunk)
                            del stderr_tail[:-2000]
                    if len(stdout_buffer) > 4 * 1024 * 1024:
                        raise BridgeError("pi bridge message exceeds IPC limit")
                    newline = stdout_buffer.find(b"\n")
                    if newline < 0:
                        if not stdout_open:
                            break
                        continue
                line = bytes(stdout_buffer[:newline])
                del stdout_buffer[:newline + 1]
                message = self._decode_line(line)
                message_type = message.get("type")
                if message_type == "rpc":
                    method = message.get("method")
                    payload = message.get("payload")
                    rpc_id = message.get("id")
                    if not isinstance(method, str) or not isinstance(payload, dict) or not isinstance(rpc_id, str):
                        raise BridgeError("malformed pi bridge RPC request")
                    try:
                        result = rpc_handler(method, payload)
                        self._send(process, {"type": "rpc_result", "id": rpc_id, "ok": True, "result": result})
                    except ContextBudgetError as exc:
                        self._send(
                            process,
                            {
                                "type": "rpc_result",
                                "id": rpc_id,
                                "ok": False,
                                "status": "interrupted",
                                "error": f"context budget exhausted: {exc}",
                            },
                        )
                    except (BridgeError, PermissionError, ValueError, KeyError, OSError) as exc:
                        self._send(
                            process,
                            {
                                "type": "rpc_result",
                                "id": rpc_id,
                                "ok": False,
                                "status": "failed",
                                "error": str(exc),
                            },
                        )
                elif message_type == "event":
                    payload = message.get("event")
                    if not isinstance(payload, dict):
                        raise BridgeError("malformed pi bridge event")
                    event_handler(payload)
                elif message_type == "result":
                    if message.get("request_id") != request["request_id"]:
                        raise BridgeError("pi bridge result request id mismatch")
                    if message.get("ok") is not True:
                        status = message.get("status", "failed")
                        raise BridgeRPCError(str(message.get("error", "pi agent failed")), str(status))
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise BridgeError("pi bridge returned a non-object result")
                    return result
                elif message_type == "error":
                    raise BridgeError(str(message.get("error", "pi bridge error")))
                else:
                    raise BridgeError(f"unknown pi bridge message type: {message_type!r}")
            stderr = stderr_tail.decode("utf-8", "replace")
            raise BridgeError(f"pi bridge exited before result: {stderr}")
        finally:
            with self._lock:
                self._process = None
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            finally:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()

    def cancel(self) -> None:
        # The supervisor thread is the sole writer; cancellation cannot interleave JSON frames.
        self._cancel_requested.set()

    def _controller_environment(self, provider: str) -> dict[str, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "NODE_NO_WARNINGS": "1",
        }
        if provider == "openai":
            key = self.api_key if self.api_key is not None else os.environ.get("OPENAI_API_KEY")
            if key:
                environment["OPENAI_API_KEY"] = key
            base_url = os.environ.get("OPENAI_BASE_URL")
            if base_url:
                environment["OPENAI_BASE_URL"] = base_url
        return environment

    @staticmethod
    def _send(process: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise BridgeError("pi bridge stdin is unavailable")
        remaining = memoryview((json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        while remaining:
            written = process.stdin.write(remaining)
            if not written:
                raise BridgeError("pi bridge input pipe closed during write")
            remaining = remaining[written:]
        process.stdin.flush()

    @staticmethod
    def _decode_line(line: str | bytes) -> dict[str, Any]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BridgeError("pi bridge emitted invalid JSON") from exc
        if not isinstance(value, dict):
            raise BridgeError("pi bridge emitted a non-object")
        return value


class Worker:
    """Thin Python supervisor: durable Store, context fences, and controlled tools."""

    def __init__(
        self,
        store: Store,
        model: str,
        api_key: str | None = None,
        max_rounds: int = 12,
        sandbox: Sandbox | None = None,
        *,
        bridge: Any | None = None,
        context_budget: ContextBudget | None = None,
        max_total_seconds: float = 300.0,
        provider: str = "openai-codex",
        auth_file: str | Path | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("an explicit model is required")
        if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds <= 0:
            raise ValueError("max_rounds must be positive")
        if not math.isfinite(max_total_seconds) or max_total_seconds <= 0:
            raise ValueError("worker timeout must be finite and positive")
        if provider not in {"openai", "openai-codex"}:
            raise ValueError("unsupported provider")
        if provider == "openai-codex" and (not auth_file or api_key is not None):
            raise ValueError("Codex requires an OAuth auth file, not an API key")
        self.provider = provider
        self.auth_file = str(Path(auth_file).expanduser().absolute()) if auth_file else None
        self.store = store
        self.model = model
        self.api_key = api_key
        self.max_rounds = max_rounds
        self.sandbox = sandbox
        self.context_budget = context_budget or ContextBudget()
        self.max_total_seconds = float(max_total_seconds)
        self.bridge = bridge or PiBridge(api_key=api_key, timeout_seconds=self.max_total_seconds)
        self.on_event = on_event
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._active_bridge: Any | None = None
        self._goal_id: str | None = None
        self._assignment_id: str | None = None
        self._last_assignment_id: str | None = None
        self._run_id: str | None = None
        self._run_attempt_id: str | None = None
        self._run_fence: dict[str, Any] | None = None
        self._invocation_id: str | None = None
        self._last_invocation_id: str | None = None
        self._invocation: Mapping[str, Any] | None = None
        self._finished_invocations: set[str] = set()
        self._pending_events: list[dict[str, Any]] = []
        self._run_gate = threading.Lock()
        self._started_at = 0.0
        self._provider_usage: dict[str, dict[str, Any]] = {}
        self._admission: dict[str, Any] | None = None

    @property
    def last_assignment_id(self) -> str | None:
        """Recovery identity survives cleanup; it is not execution authority."""
        return self._last_assignment_id

    def cancel(self) -> None:
        self._cancel_event.set()
        if self.sandbox is not None:
            self.sandbox.cancel()
        with self._lock:
            bridge = self._active_bridge
        cancel = getattr(bridge, "cancel", None)
        if callable(cancel):
            cancel()

    def run(self, goal_id: str) -> dict[str, Any]:
        if not self._run_gate.acquire(blocking=False):
            raise BridgeError("worker already owns an active run")
        try:
            return self._run_once(goal_id)
        finally:
            self._run_gate.release()

    def _run_once(self, goal_id: str) -> dict[str, Any]:
        self._cancel_event.clear()
        started = time.monotonic()
        self._started_at = started
        self._provider_usage = {}
        self._admission = None
        self._goal_id = goal_id
        self._run_id = None
        self._run_attempt_id = None
        self._assignment_id = None
        self._last_assignment_id = None
        self._run_fence = None
        self._invocation_id = None
        self._last_invocation_id = None
        self._invocation = None
        self._finished_invocations = set()
        self._pending_events = []
        try:
            assignment = self.store.assign(goal_id)
            assignment_id = self._id(assignment, "assignment")
            self._last_assignment_id = assignment_id
            limits = {
                "model": self.model,
                "provider": self.provider,
                "context_budget": self.context_budget.max_context_tokens,
                "output_reserve": self.context_budget.max_output_tokens,
                "max_rounds": self.max_rounds,
                "max_time": self.max_total_seconds,
            }
            attempt = self.store.start_run(goal_id, assignment_id, limits)
            run_id = self._id(attempt, "run")
            self._run_id = run_id
            self._run_attempt_id = run_id
            goal = self.store.goal(goal_id)
            self._assignment_id = assignment_id
            self._run_fence = {
                key: goal.get(key)
                for key in ("input_version", "revision", "authority_version")
                if key in goal
            }
            compiled = self._compile(goal)
            self._set_admission(asdict(compiled.usage))
            self._check_cancelled(started)
            remaining_seconds = self.max_total_seconds - (time.monotonic() - started)
            if remaining_seconds <= 0:
                raise TimeoutError("worker whole-run timeout expired")
            with self._lock:
                self._active_bridge = self.bridge
            result = self.bridge.run(
                {
                    "type": "run",
                    "request_id": run_id,
                    "run_id": run_id,
                    "invocation_id": None,
                    "model": self.model,
                    "provider": self.provider,
                    "auth_file": self.auth_file,
                    "messages": compiled.messages,
                    "tools": TOOL_SCHEMAS,
                    "max_rounds": self.max_rounds,
                    "max_output_tokens": self.context_budget.max_output_tokens if self.provider == "openai" else None,
                    "timeout_ms": int(remaining_seconds * 1000),
                },
                rpc_handler=self._rpc,
                event_handler=self._event,
                cancel_event=self._cancel_event,
            )
            status = str(result.get("status", "finished"))
            if self._cancel_event.is_set() or status == "cancelled":
                status = "cancelled"
            if status not in {"finished", "failed", "cancelled", "interrupted"}:
                raise BridgeError(f"invalid pi bridge status: {status}")
            text = result.get("result", "")
            if not isinstance(text, str):
                raise BridgeError("pi bridge result text is not a string")
            diagnostic = self._diagnostic(result.get("error"), "provider") if result.get("error") else {}
            stop_reason = result.get("stop_reason") or self._default_stop_reason(status)
            invocation_id = self._last_invocation_id
            if invocation_id is not None and invocation_id not in self._finished_invocations:
                self._finish_current(status, text)
            return self._complete_run(
                goal_id, status, text, stop_reason, diagnostic,
                result.get("rounds", 0), invocation_id,
            )
        except ContextBudgetError as exc:
            status = "cancelled" if self._cancel_event.is_set() else "interrupted"
            admission = asdict(exc.usage) if exc.usage is not None else self._admission
            if admission is not None:
                self._set_admission(admission)
            return self._stop_run(goal_id, status, "context_budget", str(exc))
        except TimeoutError as exc:
            status = "cancelled" if self._cancel_event.is_set() else "interrupted"
            return self._stop_run(goal_id, status, "time_limit", str(exc))
        except BridgeRPCError as exc:
            status = "cancelled" if self._cancel_event.is_set() else exc.status
            if status not in {"failed", "cancelled", "interrupted", "finished"}:
                status = "failed"
            reason = "context_budget" if "context budget" in str(exc).casefold() else "provider_error"
            return self._stop_run(goal_id, status, reason, str(exc))
        except KeyboardInterrupt:
            self._stop_run(goal_id, "cancelled", "ctrl_c", "CLI interrupted by Ctrl-C")
            raise
        except (BridgeError, SandboxError, PermissionError, ValueError, KeyError, OSError) as exc:
            status = "cancelled" if self._cancel_event.is_set() else "failed"
            reason = "assignment_fence" if isinstance(exc, PermissionError) else "worker_error"
            return self._stop_run(goal_id, status, reason, str(exc))
        finally:
            with self._lock:
                self._active_bridge = None
            self._goal_id = None
            self._assignment_id = None
            self._run_id = None
            self._run_attempt_id = None
            self._run_fence = None
            self._invocation_id = None
            self._last_invocation_id = None
            self._invocation = None

    def _compile(self, goal: Mapping[str, Any]) -> CompiledContext:
        return compile_context(
            goal,
            history=goal.get("invocations", []),
            artifacts=goal.get("artifacts", []),
            budget=self.context_budget,
            tools=TOOL_SCHEMAS,
        )

    def _set_admission(self, admission: dict[str, Any]) -> None:
        self._admission = dict(admission)
        if self._run_attempt_id is not None:
            self.store.record_run_admission(self._run_attempt_id, self._admission)

    @staticmethod
    def _diagnostic(message: Any, kind: str) -> dict[str, Any]:
        return {"kind": kind, "message": str(message)}

    @staticmethod
    def _default_stop_reason(status: str) -> str:
        return {
            "finished": "completed",
            "cancelled": "cancelled",
            "interrupted": "interrupted",
            "failed": "provider_error",
        }.get(status, "worker_error")

    def _complete_run(
        self,
        goal_id: str,
        status: str,
        assistant_text: str,
        stop_reason: str,
        diagnostic: dict[str, Any],
        rounds: Any,
        invocation_id: str | None,
    ) -> dict[str, Any]:
        run_id = self._run_attempt_id
        if run_id is not None:
            self.store.finish_run(
                run_id,
                status,
                stop_reason=stop_reason,
                diagnostic=diagnostic,
                assistant_text=assistant_text,
                admission=self._admission,
                invocation_id=invocation_id,
            )
        return {
            "status": status,
            "goal_id": goal_id,
            "run_id": run_id,
            "invocation_id": invocation_id,
            "rounds": rounds,
            "result": assistant_text,
            "assistant_text": assistant_text,
            "stop_reason": stop_reason,
            "diagnostic": diagnostic,
            "admission": self._admission,
        }

    def _stop_run(self, goal_id: str, status: str, stop_reason: str, message: str) -> dict[str, Any]:
        invocation_id = self._last_invocation_id
        assistant_text = ""
        if isinstance(self._invocation, Mapping) and isinstance(self._invocation.get("result"), str):
            assistant_text = self._invocation["result"]
        if invocation_id is not None and invocation_id not in self._finished_invocations:
            self._finish_current(status, assistant_text)
        return self._complete_run(
            goal_id,
            status,
            assistant_text,
            stop_reason,
            self._diagnostic(message, stop_reason),
            0,
            invocation_id,
        )

    def _rpc(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_cancelled(self._started_at)
        if payload.get("run_id") not in {None, self._run_id}:
            raise PermissionError("pi RPC run fence mismatch")
        if method == "prepare_request":
            return self._prepare_request(payload)
        if method == "tool_call":
            return self._tool_call(payload)
        if method == "provider_payload":
            return self._provider_payload(payload)
        raise BridgeError(f"unsupported pi RPC method: {method}")

    def _provider_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("invocation_id") != self._invocation_id:
            raise PermissionError("provider payload invocation fence mismatch")
        self._fenced_goal()
        body = payload.get("payload")
        if not isinstance(body, dict):
            raise ValueError("provider payload must be an object")
        admitted = self.context_budget.admit([body])
        self.store.receipt(
            self._invocation_id, "provider.pi.payload",
            {"model": payload.get("model"), "run_id": self._run_id},
            {"payload": body, "admitted": asdict(admitted),
             "provider_output_limit_supported": self.provider == "openai"},
            note="Final serialized provider payload captured before HTTP submission; no credentials.",
        )
        return {"ok": True}

    def _prepare_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        prior_invocation_id = payload.get("invocation_id")
        if prior_invocation_id != self._last_invocation_id:
            raise PermissionError("provider request invocation fence mismatch")
        context = payload.get("context")
        if not isinstance(context, dict):
            raise ValueError("provider request context must be an object")
        messages = context.get("messages")
        tools = context.get("tools", TOOL_SCHEMAS)
        if not isinstance(messages, list) or not isinstance(tools, list):
            raise ValueError("provider request context messages/tools must be lists")
        usage = self.context_budget.admit(messages, tools)
        self._set_admission(asdict(usage))
        model_info = payload.get("model")
        if isinstance(model_info, Mapping):
            context_window = model_info.get("contextWindow")
            if isinstance(context_window, int) and not isinstance(context_window, bool):
                if usage.total_tokens > context_window:
                    raise ContextBudgetError(
                        f"model context window is smaller than admitted request: "
                        f"{context_window} < {usage.total_tokens}"
                    )
            max_tokens = model_info.get("maxTokens")
            if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
                if self.context_budget.max_output_tokens > max_tokens:
                    raise ContextBudgetError(
                        f"model output limit is smaller than configured reserve: "
                        f"{max_tokens} < {self.context_budget.max_output_tokens}"
                    )
        goal = self._fenced_goal()
        assignment_id = self._assignment_id
        if assignment_id is None:
            raise PermissionError("provider request assignment is unavailable")
        invocation = self.store.invoke(
            self._goal_id_required(),
            assignment_id,
            {"messages": messages, "tools": tools, "budget": asdict(usage)},
            expected_input_version=goal.get("input_version"),
        )
        invocation_id = self._id(invocation, "invocation")
        snapshot = dict(invocation)
        for key in ("input_version", "revision", "authority_version"):
            if key not in snapshot:
                snapshot[key] = goal.get(key)
        self._invocation_id = invocation_id
        self._last_invocation_id = invocation_id
        if self._run_attempt_id is not None:
            self.store.bind_run_invocation(self._run_attempt_id, invocation_id)
        self._invocation = snapshot
        pending, self._pending_events = self._pending_events, []
        for event in pending:
            self._event(event)
        fence = {
            key: goal.get(key)
            for key in ("input_version", "revision", "authority_version")
            if key in goal
        }
        self.store.receipt(
            invocation_id,
            "provider.pi.prepare_request",
            {
                "model": payload.get("model"),
                "context": context,
                "fence": fence,
            },
            {"admitted": asdict(usage)},
            note="Persisted immediately before this upstream pi provider request.",
        )
        return {"ok": True, "invocation_id": invocation_id, "admitted": asdict(usage)}

    def _tool_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        invocation_id = payload.get("invocation_id")
        if invocation_id != self._invocation_id:
            raise PermissionError("tool invocation fence mismatch")
        self._fenced_goal()
        name = payload.get("name")
        arguments = payload.get("arguments")
        call_id = payload.get("tool_call_id")
        if not isinstance(name, str) or not isinstance(arguments, dict) or not isinstance(call_id, str):
            raise ValueError("malformed pi tool call")
        try:
            result = self._run_tool(name, arguments, invocation_id)
            if not isinstance(result, dict) or "ok" not in result:
                result = {"ok": True, "value": result}
        except (SandboxError, ValueError, KeyError, OSError, PermissionError) as exc:
            result = {"ok": False, "error": str(exc)}
        self.store.receipt(
            invocation_id,
            f"tool.{name}",
            {"tool_call_id": call_id, "arguments": arguments},
            result,
            note="Pi tool dispatch; output is untrusted and cannot authorize policy.",
        )
        return result

    def _run_tool(self, name: str, arguments: dict[str, Any], invocation_id: str) -> dict[str, Any]:
        if name == "read_artifact":
            return self._read_artifact(arguments)
        if name == "staged_read":
            return self._require_sandbox().read(arguments)
        if name == "staged_write":
            return self._require_sandbox().write(arguments)
        if name == "staged_search":
            return self._require_sandbox().search(arguments)
        if name == "staged_run":
            sandbox = self._require_sandbox()
            self._check_cancelled(self._started_at)
            remaining = self.max_total_seconds - (time.monotonic() - self._started_at)
            requested = arguments.get("timeout_seconds", sandbox.timeout)
            if isinstance(requested, bool) or not isinstance(requested, (int, float)):
                raise ValueError("timeout_seconds must be numeric")
            return sandbox.run({**arguments, "timeout_seconds": min(requested, remaining, sandbox.timeout)})
        if name == "save_artifact":
            return self._save_artifact(invocation_id, arguments, "worker")
        if name == "finding":
            return self._save_artifact(invocation_id, arguments, "finding")
        raise ValueError(f"unsupported worker tool: {name}")

    def _read_artifact(self, arguments: dict[str, Any]) -> dict[str, Any]:
        artifact_id = arguments.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("artifact_id is required")
        requested = arguments.get("max_chars", 12_000)
        if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
            raise ValueError("max_chars must be a positive integer")
        limit = min(requested, 64 * 1024)
        artifacts = self.store.artifacts(self._goal_id_required())
        match = next(
            (artifact for artifact in artifacts if isinstance(artifact, Mapping) and artifact.get("id") == artifact_id),
            None,
        )
        if match is None:
            raise KeyError(f"artifact not found: {artifact_id}")
        content = match.get("content")
        if not isinstance(content, str):
            raise ValueError("artifact content is not text")
        projected = content[:limit]
        result = dict(match)
        result["content"] = projected
        result["content_chars"] = len(content)
        result["content_truncated"] = len(projected) < len(content)
        result["content_limit_chars"] = limit
        result["projection_note"] = (
            (
                "content is truncated at the hard 65536-character cap"
                if requested > limit
                else "content is a bounded projection; request read_artifact again with a larger max_chars value"
            )
            if result["content_truncated"]
            else "content is complete under the requested bound"
        )
        return {"ok": True, "artifact": result}


    def _save_artifact(self, invocation_id: str, arguments: dict[str, Any], default_kind: str) -> dict[str, Any]:
        content = arguments.get("content")
        limitations = arguments.get("limitations")
        if not isinstance(content, str) or not isinstance(limitations, str):
            raise ValueError("artifact content and limitations are required text")
        kind = arguments.get("kind", default_kind)
        name = arguments.get("name", "")
        inputs = arguments.get("inputs", [])
        if not isinstance(kind, str) or not isinstance(name, str):
            raise ValueError("artifact kind and name must be text")
        if not isinstance(inputs, list) or any(not isinstance(item, str) for item in inputs):
            raise ValueError("artifact inputs must be string IDs")
        artifact = self.store.artifact(
            self._goal_id_required(),
            invocation_id,
            kind,
            content,
            name=name,
            inputs=inputs,
            limitations=limitations,
            trust="worker",
        )
        return {
            "ok": True,
            "artifact_id": self._id(artifact, "artifact"),
            "trust": "worker",
            "limitations": limitations,
        }

    def _event(self, event: dict[str, Any]) -> None:
        invocation_id = self._invocation_id
        envelope_type = event.get("type", "unknown")
        observed = event.get("event") if envelope_type == "agent_event" else event
        if not isinstance(observed, dict):
            raise BridgeError("pi event payload is not an object")
        event_type = observed.get("type", envelope_type)
        if invocation_id is None:
            message = observed.get("message")
            input_event = (
                event_type in {"message_start", "message_end"}
                and isinstance(message, dict)
                and message.get("role") in {"system", "user"}
            )
            if envelope_type == "agent_config" or event_type in {"agent_start", "turn_start"} or input_event:
                if len(self._pending_events) >= 32:
                    raise BridgeError("too many lifecycle events before request admission")
                self._pending_events.append(event)
                return
            raise BridgeError("pi model/tool event arrived before request admission")
        self.store.receipt(
            invocation_id,
            f"pi.event.{event_type}",
            {"run_id": self._run_id, "envelope_type": envelope_type},
            observed,
            note="Exact upstream pi event; model/tool data remains untrusted.",
        )
        if envelope_type == "provider_stream_event":
            data = event.get("data")
            response = data.get("response") if isinstance(data, dict) else None
            reported = response.get("usage") if isinstance(response, dict) else None
            if isinstance(reported, dict):
                self._provider_usage[invocation_id] = reported
        if event_type == "message_end":
            message = observed.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                normalized: dict[str, Any] = {}
                reported = self._provider_usage.get(invocation_id)
                if isinstance(reported, dict):
                    for key in ("input_tokens", "output_tokens", "total_tokens"):
                        value = reported.get(key)
                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                            normalized[key] = value
                    for group, source, target in (
                        ("input_tokens_details", "cached_tokens", "cached_input_tokens"),
                        ("output_tokens_details", "reasoning_tokens", "reasoning_tokens"),
                    ):
                        details = reported.get(group)
                        value = details.get(source) if isinstance(details, dict) else None
                        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                            normalized[target] = value
                self.store.usage(
                    invocation_id,
                    {
                        **normalized,
                        "provider": "pi-ai",
                        "raw": reported,
                        "provider_reported": reported,
                        "normalized": normalized or None,
                    },
                )
        elif event_type == "turn_end":
            message = observed.get("message")
            stop_reason = message.get("stopReason") if isinstance(message, dict) else None
            status = "failed" if stop_reason == "error" else "cancelled" if stop_reason == "aborted" else "finished"
            result = self._assistant_text(message)
            if not result and isinstance(message, dict):
                result = str(message.get("errorMessage", ""))
            self._finish_current(status, result)
        if self.on_event is not None and envelope_type == "agent_event":
            self.on_event(invocation_id, observed)

    def _finish_current(self, status: str, result: str) -> None:
        invocation_id = self._invocation_id
        if invocation_id is None or invocation_id in self._finished_invocations:
            return
        self.store.finish(invocation_id, status, result=result)
        self._finished_invocations.add(invocation_id)

    @staticmethod
    def _assistant_text(message: Any) -> str:
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            return ""
        content = message.get("content", [])
        if not isinstance(content, list):
            return ""
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, Mapping) and part.get("type") == "text" and isinstance(part.get("text"), str)
        )

    def _fenced_goal(self) -> Mapping[str, Any]:
        goal_id = self._goal_id_required()
        goal = self.store.goal(goal_id)
        if not isinstance(goal, Mapping):
            raise PermissionError("current goal is unavailable")
        run_fence = self._run_fence or {}
        for key, expected in run_fence.items():
            if goal.get(key) != expected:
                raise PermissionError(f"run {key} fence changed")
        invocation = self._invocation or {}
        for key in ("input_version", "revision", "authority_version"):
            expected = invocation.get(key)
            if expected is not None and goal.get(key) != expected:
                raise PermissionError(f"invocation {key} fence changed")
        if self._invocation_id is not None:
            self.store.assert_invocation_current(self._invocation_id)
        return goal

    def _goal_id_required(self) -> str:
        if not isinstance(self._goal_id, str):
            raise KeyError("current goal is unavailable")
        return self._goal_id

    def _require_sandbox(self) -> Sandbox:
        if self.sandbox is None:
            raise SandboxError("staged sandbox is not configured")
        return self.sandbox

    def _check_cancelled(self, started: float) -> None:
        if self._cancel_event.is_set():
            raise TimeoutError("worker cancelled before invocation")
        if time.monotonic() - started >= self.max_total_seconds:
            raise TimeoutError("worker whole-run timeout expired")

    @staticmethod
    def _id(value: Any, kind: str) -> str:
        if isinstance(value, Mapping) and isinstance(value.get("id"), str):
            return value["id"]
        raise KeyError(f"{kind} did not return an id")



__all__ = ["PiBridge", "TOOL_SCHEMAS", "Worker"]
