"""Localhost HTTP API for the canonical Goal Native Store and Worker.

The controller owns HTTP policy and presentation only. Durable state comes from
``goal_native.store.Store`` and model execution comes from ``goal_native.worker.Worker``.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .store import Store
from .worker import Worker


MAX_BODY = 2_000_000
MAX_TEXT = 250_000
MAX_SMALL_TEXT = 20_000
MAX_QUERY = 200


def now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def bounded_text(value: Any, field: str, limit: int = MAX_SMALL_TEXT, required: bool = False) -> str:
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    if len(value) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return value


def bounded_query(value: Any) -> str:
    return bounded_text(value, "q", MAX_QUERY, True).strip()


def search_store(store: Store, query: str) -> dict[str, list[dict[str, Any]]]:
    """Search only canonical Store projections; never opens a second schema."""
    query = bounded_query(query).casefold()
    goals: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for summary in store.list_goals():
        haystack = " ".join(
            str(summary.get(key, ""))
            for key in ("outcome", "criteria", "constraints", "kind", "status")
        ).casefold()
        if query in haystack:
            goals.append(summary)
        detail = store.goal(summary["id"])
        for artifact in detail.get("artifacts", []):
            artifact_haystack = " ".join(
                str(artifact.get(key, ""))
                for key in ("name", "kind", "content", "limitations", "trust")
            ).casefold()
            if query in artifact_haystack:
                artifacts.append(artifact)
        if any(query in str(request.get("text", "")).casefold() for request in detail.get("requests", [])):
            if summary not in goals:
                goals.append(summary)
    return {"goals": goals, "artifacts": artifacts}


def manual_draft(
    store: Store,
    goal_id: str,
    content: str,
    name: str,
    kind: str,
    limitations: str,
) -> dict[str, Any]:
    """Record a human draft through the canonical assignment/invocation API."""
    assignment = store.assign(goal_id)
    invocation = store.invoke(goal_id, assignment["id"], {"producer": "human", "mode": "manual-draft"})
    artifact = store.artifact(
        goal_id,
        invocation["id"],
        kind,
        content,
        name,
        [],
        limitations,
        "human",
    )
    store.finish(invocation["id"], "finished", "Human-authored local draft")
    return artifact


class GoalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], state_root: str | Path, model: str | None = None):
        self.state_root = str(Path(state_root).expanduser())
        self.model = model or ""
        self.thread_local = threading.local()
        self.runner: LocalRunner | None = None
        super().__init__(server_address, GoalRequestHandler)
        self.runner = LocalRunner(self)

    def store(self) -> Store:
        store = getattr(self.thread_local, "store", None)
        if store is None:
            store = Store(self.state_root)
            self.thread_local.store = store
        return store


class LocalRunner:
    """Run canonical Worker instances off the HTTP handler thread."""

    def __init__(self, server: GoalHTTPServer):
        self.server = server
        self.lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}

    def start(self, goal_id: str, context: str = "") -> dict[str, Any]:
        if not self.server.model:
            raise ValueError("model is required; no provider was selected")
        bounded_text(context, "context", MAX_SMALL_TEXT)
        with self.lock:
            if any(
                job["goal_id"] == goal_id and job["status"] in {"running", "cancelling"}
                for job in self.jobs.values()
            ):
                raise PermissionError("a run is already active for this goal")
            run_id = new_id("run")
            job: dict[str, Any] = {
                "id": run_id,
                "goal_id": goal_id,
                "status": "running",
                "error": None,
                "result": None,
                "worker": None,
                "cancel_requested": False,
                "started_at": now(),
            }
            self.jobs[run_id] = job

        def execute() -> None:
            try:
                # Each background execution owns both the canonical Store
                # connection and the canonical Worker instance.
                with Store(self.server.state_root) as store:
                    worker = Worker(store, self.server.model, provider="openai")
                    with self.lock:
                        job["worker"] = worker
                        cancel_requested = job["cancel_requested"]
                    if cancel_requested:
                        worker.cancel()
                    result = worker.run(goal_id)
                with self.lock:
                    job["result"] = result
                    job["status"] = str(result.get("status", "finished"))
            except Exception as exc:
                with self.lock:
                    job["status"] = "cancelled" if job["cancel_requested"] else "failed"
                    job["error"] = str(exc)
            finally:
                with self.lock:
                    job["worker"] = None
                    job["finished_at"] = now()

        thread = threading.Thread(target=execute, name=f"goal-worker-{run_id}", daemon=True)
        with self.lock:
            job["thread"] = thread
        thread.start()
        return {"run_id": run_id, "goal_id": goal_id, "status": "running"}

    def cancel(self, goal_id: str) -> dict[str, Any]:
        with self.lock:
            jobs = [
                job
                for job in self.jobs.values()
                if job["goal_id"] == goal_id and job["status"] == "running"
            ]
            for job in jobs:
                job["cancel_requested"] = True
                job["status"] = "cancelling"
                worker = job.get("worker")
                if worker is not None:
                    worker.cancel()
            return {
                "goal_id": goal_id,
                "cancelled": len(jobs),
                "runs": [{"run_id": job["id"], "status": job["status"]} for job in jobs],
            }

    def status(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(run_id)
            if job is None:
                raise KeyError(f"run {run_id} not found")
            return {
                key: value
                for key, value in job.items()
                if key not in {"worker", "thread", "cancel_requested"}
            }


class HTTPProblem(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class GoalRequestHandler(BaseHTTPRequestHandler):
    server: GoalHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host", "")
        if not host or any(char in host for char in "\r\n/"):
            return False
        try:
            parsed = urllib.parse.urlsplit(f"http://{host}")
            hostname = (parsed.hostname or "").lower()
            port = parsed.port or 80
        except ValueError:
            return False
        return hostname in {"localhost", "127.0.0.1", "::1"} and port == self.server.server_address[1]

    def _allowed_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        if origin == "null":
            return False
        try:
            parsed = urllib.parse.urlsplit(origin)
            hostname = (parsed.hostname or "").lower()
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and hostname in {"localhost", "127.0.0.1", "::1"}
            and port == self.server.server_address[1]
        )

    def _guard(self, mutation: bool = False) -> None:
        if not self._allowed_host():
            raise HTTPProblem(403, "invalid localhost Host header")
        if mutation and not self._allowed_origin():
            raise HTTPProblem(403, "Origin does not match this localhost server")

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "-1")
        except ValueError as exc:
            raise HTTPProblem(400, "invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY:
            raise HTTPProblem(413, "request body is missing or too large")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPProblem(400, "request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise HTTPProblem(400, "request body must be an object")
        return value

    def _path(self) -> tuple[str, list[str], dict[str, list[str]]]:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.scheme or parsed.netloc:
            raise HTTPProblem(400, "absolute URLs are not accepted")
        path = urllib.parse.unquote(parsed.path)
        if ".." in Path(path).parts:
            raise HTTPProblem(400, "path traversal is not accepted")
        return path, [part for part in path.split("/") if part], urllib.parse.parse_qs(parsed.query)

    def _reply_error(self, problem: Exception) -> None:
        if isinstance(problem, HTTPProblem):
            status, message = problem.status, str(problem)
        elif isinstance(problem, KeyError):
            status, message = 404, str(problem).strip("'")
        elif isinstance(problem, PermissionError):
            status, message = 409, str(problem)
        elif isinstance(problem, (ValueError, TypeError)):
            status, message = 400, str(problem)
        elif isinstance(problem, OSError):
            status, message = 503, str(problem)
        else:
            status, message = 500, "internal server error"
        self._send_json(status, {"error": message, "type": problem.__class__.__name__})

    def do_GET(self) -> None:
        try:
            self._guard()
            path, parts, query = self._path()
            if path == "/":
                self._static("index.html", "text/html; charset=utf-8")
            elif path in {"/app.js", "/style.css"}:
                name = path.lstrip("/")
                content_type = "text/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
                self._static(name, content_type)
            elif parts[:1] == ["api"]:
                self._api_get(parts[1:], query)
            else:
                raise HTTPProblem(404, "not found")
        except Exception as exc:
            self._reply_error(exc)

    def do_POST(self) -> None:
        try:
            self._guard(True)
            _path, parts, _query = self._path()
            body = self._body()
            if parts[:1] != ["api"]:
                raise HTTPProblem(404, "not found")
            self._api_post(parts[1:], body)
        except Exception as exc:
            self._reply_error(exc)

    def _static(self, name: str, content_type: str) -> None:
        if name not in {"index.html", "app.js", "style.css"}:
            raise HTTPProblem(404, "not found")
        source_path = Path(__file__).resolve().parent.parent / "web" / name
        installed_path = Path(sys.prefix) / "share" / "goal-native" / "web" / name
        file_path = source_path if source_path.is_file() else installed_path
        try:
            body = file_path.read_bytes()
        except OSError as exc:
            raise HTTPProblem(500, "web asset unavailable") from exc
        self._send_bytes(200, body, content_type)

    def _api_get(self, parts: list[str], query: dict[str, list[str]]) -> None:
        store = self.server.store()
        if parts == ["config"]:
            self._send_json(
                200,
                {
                    "model": self.server.model or None,
                    "provider": "pi-agent-core/pi-ai",
                    "provider_ready": bool(self.server.model and os.environ.get("OPENAI_API_KEY")),
                    "state": self.server.state_root,
                },
            )
        elif parts == ["goals"]:
            q = query.get("q", [""])[0]
            self._send_json(200, search_store(store, q) if q else {"goals": store.list_goals(), "artifacts": []})
        elif len(parts) == 2 and parts[0] == "goals":
            self._send_json(200, store.goal(parts[1]))
        elif parts == ["export"]:
            self._send_json(200, store.export())
        elif parts == ["search"]:
            self._send_json(200, search_store(store, query.get("q", [""])[0]))
        elif len(parts) == 2 and parts[0] == "runs":
            if self.server.runner is None:
                raise HTTPProblem(503, "run manager unavailable")
            self._send_json(200, self.server.runner.status(parts[1]))
        elif len(parts) >= 2 and parts[0] == "destinations":
            self._send_json(200, store.destination("/".join(parts[1:])))
        else:
            raise HTTPProblem(404, "not found")

    def _api_post(self, parts: list[str], body: dict[str, Any]) -> None:
        store = self.server.store()
        if parts == ["goals"]:
            self._send_json(
                201,
                store.create_goal(
                    body.get("outcome"),
                    body.get("parent_id"),
                    body.get("kind", "outcome"),
                    body.get("criteria", ""),
                    body.get("constraints", ""),
                ),
            )
            return
        if parts == ["import"]:
            data = body.get("data", body)
            store.close()
            self.server.thread_local.store = None
            imported = Store.import_data(self.server.state_root, data)
            imported.close()
            self.server.thread_local.store = Store(self.server.state_root)
            self._send_json(200, {"imported": True, "goals": self.server.thread_local.store.list_goals()})
            return
        if len(parts) == 3 and parts[0] == "effects":
            effect_id, action = parts[1], parts[2]
            if action == "approve":
                self._send_json(200, store.approve(effect_id))
            elif action == "commit":
                self._send_json(200, store.commit(effect_id, bool(body.get("lose_response", False))))
            elif action == "reconcile":
                self._send_json(200, store.reconcile(effect_id))
            else:
                raise HTTPProblem(404, "not found")
            return
        if len(parts) != 3 or parts[0] != "goals":
            raise HTTPProblem(404, "not found")
        goal_id, action = parts[1], parts[2]
        if action == "request":
            self._send_json(200, store.request(goal_id, body.get("text"), body.get("control")))
        elif action == "revise":
            self._send_json(
                200,
                store.revise(
                    goal_id,
                    body.get("expected_revision"),
                    body.get("outcome"),
                    body.get("criteria", ""),
                    body.get("constraints", ""),
                ),
            )
        elif action == "artifacts":
            self._send_json(
                201,
                manual_draft(
                    store,
                    goal_id,
                    bounded_text(body.get("content"), "content", MAX_TEXT, True),
                    bounded_text(body.get("name", "Manual draft"), "name", 200),
                    bounded_text(body.get("kind", "draft"), "kind", 80, True),
                    bounded_text(body.get("limitations", ""), "limitations"),
                ),
            )
        elif action == "assess":
            artifact_id = body.get("artifact_id")
            artifact = next((item for item in store.artifacts(goal_id) if item["id"] == artifact_id), None)
            if artifact is None:
                raise KeyError(f"unknown artifact for goal: {artifact_id}")
            self._send_json(
                201,
                store.verify(
                    body.get("invocation_id", artifact["invocation_id"]),
                    artifact_id,
                    body.get("check"),
                    body.get("passed"),
                    body.get("details", ""),
                    trusted=True,
                ),
            )
        elif action == "prepare-effect":
            self._send_json(
                201,
                store.prepare_effect(
                    body.get("invocation_id"),
                    body.get("artifact_id"),
                    body.get("target"),
                    body.get("expected_version"),
                    body.get("evidence_id"),
                ),
            )
        elif action == "accept":
            self._send_json(
                201,
                store.accept(
                    goal_id,
                    body.get("artifact_id"),
                    body.get("evidence_id"),
                    body.get("expected_revision"),
                ),
            )
        elif action == "run":
            if self.server.runner is None:
                raise HTTPProblem(503, "run manager unavailable")
            self._send_json(202, self.server.runner.start(goal_id, body.get("context", "")))
        elif action == "cancel":
            if self.server.runner is None:
                raise HTTPProblem(503, "run manager unavailable")
            self._send_json(200, self.server.runner.cancel(goal_id))
        else:
            raise HTTPProblem(404, "not found")


def serve(state: str, port: int, model: str | None) -> None:
    server = GoalHTTPServer(("127.0.0.1", port), state, model)
    print(f"Goal Native listening on http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


