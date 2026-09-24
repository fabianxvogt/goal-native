"""Durable SQLite runtime for Goal Native.

The store is the trusted controller-side authority boundary.  The Python worker
supervisor is trusted, but worker/model/task code receives only narrow tool
handlers and untrusted records or proposals; it never receives Store or any
trusted verification, approval, authority, or commit API.  No arbitrary plugin
is permitted.  This is an integration boundary, not Python object-capability
security theater.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_EXPORT_FORMAT = "goal-native-export"
_EXPORT_VERSION = 1
_ALLOWED_CONTROLS = {None, "pause", "cancel", "draft", "resume", "allow_effects"}
_ALLOWED_FINISH_STATUSES = {"finished", "failed", "cancelled", "interrupted"}
_ALLOWED_RUN_STATUSES = {"running", "finished", "failed", "cancelled", "interrupted"}


class Store:
    """A single-workspace, transactionally durable domain store."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "store.sqlite3"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @contextmanager
    def _read_snapshot(self) -> Iterator[sqlite3.Connection]:
        """Keep every query in one SQLite read snapshot."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.rollback()

    def _initialize(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY,
                    outcome TEXT NOT NULL,
                    parent_id TEXT,
                    kind TEXT NOT NULL,
                    criteria TEXT NOT NULL,
                    constraints_text TEXT NOT NULL,
                    original_request_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    input_version INTEGER NOT NULL,
                    authority_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    effects_allowed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(parent_id) REFERENCES goals(id)
                );
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    text TEXT NOT NULL,
                    control TEXT,
                    source TEXT NOT NULL,
                    input_version INTEGER NOT NULL,
                    authority_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    revision INTEGER NOT NULL,
                    input_version INTEGER NOT NULL,
                    authority_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS invocations (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    assignment_id TEXT NOT NULL REFERENCES assignments(id),
                    input_version INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    authority_version INTEGER NOT NULL,
                    context_artifact_id TEXT,
                    status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS run_attempts (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    assignment_id TEXT NOT NULL REFERENCES assignments(id),
                    invocation_id TEXT REFERENCES invocations(id),
                    status TEXT NOT NULL,
                    stop_reason TEXT NOT NULL DEFAULT '',
                    diagnostic_json TEXT NOT NULL DEFAULT '{}',
                    assistant_text TEXT NOT NULL DEFAULT '',
                    limits_json TEXT NOT NULL DEFAULT '{}',
                    admission_json TEXT,
                    stage_name TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS run_stages (
                    goal_id TEXT PRIMARY KEY REFERENCES goals(id),
                    attempt_id TEXT NOT NULL REFERENCES run_attempts(id),
                    stage_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS local_stages (
                    goal_id TEXT PRIMARY KEY REFERENCES goals(id),
                    invocation_id TEXT NOT NULL REFERENCES invocations(id),
                    stage_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    invocation_id TEXT NOT NULL REFERENCES invocations(id),
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    input_version INTEGER NOT NULL,
                    authority_version INTEGER NOT NULL,
                    inputs_json TEXT NOT NULL,
                    limitations TEXT NOT NULL,
                    trust TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS goal_links (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    related_goal_id TEXT NOT NULL REFERENCES goals(id),
                    relationship TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(goal_id, related_goal_id, relationship)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT PRIMARY KEY,
                    invocation_id TEXT NOT NULL REFERENCES invocations(id),
                    tool TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_records (
                    invocation_id TEXT PRIMARY KEY REFERENCES invocations(id),
                    raw_json TEXT NOT NULL,
                    normalized_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    invocation_id TEXT NOT NULL REFERENCES invocations(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    check_name TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    details_json TEXT NOT NULL,
                    trusted INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS effects (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    invocation_id TEXT NOT NULL REFERENCES invocations(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    target TEXT NOT NULL,
                    expected_version INTEGER NOT NULL,
                    candidate_content TEXT NOT NULL,
                    evidence_id TEXT NOT NULL REFERENCES evidence(id),
                    operation_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    committed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS destinations (
                    target TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    operation_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS destination_history (
                    operation_id TEXT PRIMARY KEY,
                    target TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    effect_id TEXT NOT NULL REFERENCES effects(id),
                    committed_at TEXT NOT NULL,
                    UNIQUE(target, version)
                );
                CREATE TRIGGER IF NOT EXISTS destination_history_no_update
                BEFORE UPDATE ON destination_history
                BEGIN
                    SELECT RAISE(ABORT, 'destination history is immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS destination_history_no_delete
                BEFORE DELETE ON destination_history
                BEGIN
                    SELECT RAISE(ABORT, 'destination history is immutable');
                END;
                CREATE TABLE IF NOT EXISTS acceptances (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    evidence_id TEXT NOT NULL REFERENCES evidence(id),
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspace_snapshots (
                    content_hash TEXT PRIMARY KEY,
                    files_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS local_workspaces (
                    goal_id TEXT NOT NULL REFERENCES goals(id),
                    stage_name TEXT NOT NULL,
                    baseline_hash TEXT NOT NULL REFERENCES workspace_snapshots(content_hash),
                    selection_json TEXT NOT NULL,
                    PRIMARY KEY(goal_id, stage_name)
                );
                CREATE INDEX IF NOT EXISTS idx_requests_goal ON requests(goal_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_assignments_goal ON assignments(goal_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_invocations_goal ON invocations(goal_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_run_attempts_goal ON run_attempts(goal_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_run_stages_attempt ON run_stages(attempt_id);
                CREATE INDEX IF NOT EXISTS idx_artifacts_goal ON artifacts(goal_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_goal_links_goal ON goal_links(goal_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_receipts_invocation ON receipts(invocation_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_evidence_invocation ON evidence(invocation_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_effects_goal ON effects(goal_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_destination_history_target
                    ON destination_history(target, version);
                CREATE INDEX IF NOT EXISTS idx_acceptances_goal ON acceptances(goal_id, created_at, id);
                CREATE INDEX IF NOT EXISTS idx_events_goal ON events(goal_id, created_at, id);
                """
            )
            self._conn.execute(
                """INSERT OR IGNORE INTO destination_history(
                    operation_id, target, version, content, effect_id, committed_at
                )
                SELECT destinations.operation_id, destinations.target, destinations.version,
                    destinations.content, effects.id, COALESCE(effects.committed_at, destinations.updated_at)
                FROM destinations
                JOIN effects ON effects.operation_id = destinations.operation_id
                WHERE destinations.operation_id IS NOT NULL"""
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _text(value: Any, field: str, *, allow_empty: bool = True) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
        if not allow_empty and not value.strip():
            raise ValueError(f"{field} must not be empty")
        return value

    @staticmethod
    def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} must be an integer")
        if minimum is not None and value < minimum:
            raise ValueError(f"{field} must be at least {minimum}")
        return value

    @staticmethod
    def _json_dump(value: Any, field: str) -> str:
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be JSON-compatible") from exc

    @staticmethod
    def _json_load(value: str) -> Any:
        return json.loads(value)

    @staticmethod
    def _content_for_context(context: Any) -> str:
        if isinstance(context, str):
            return context
        return Store._json_dump(context, "context")

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _artifact_id(
        goal_id: str,
        invocation_id: str,
        kind: str,
        name: str,
        content: str,
        inputs: list[str],
        limitations: str,
        trust: str,
    ) -> str:
        envelope = {
            "goal_id": goal_id,
            "invocation_id": invocation_id,
            "kind": kind,
            "name": name,
            "content": content,
            "inputs": inputs,
            "limitations": limitations,
            "trust": trust,
        }
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _goal_row(self, goal_id: str) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown goal: {goal_id}")
        return row

    def _invocation_row(self, invocation_id: str) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM invocations WHERE id = ?", (invocation_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown invocation: {invocation_id}")
        return row

    def _effect_row(self, effect_id: str) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM effects WHERE id = ?", (effect_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown effect: {effect_id}")
        return row

    def _event(self, conn: sqlite3.Connection, goal_id: str, event_type: str, payload: Any) -> None:
        conn.execute(
            "INSERT INTO events(id, goal_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (self._id(), goal_id, event_type, self._json_dump(payload, "event payload"), self._now()),
        )

    @staticmethod
    def _goal_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "outcome": row["outcome"],
            "parent_id": row["parent_id"],
            "kind": row["kind"],
            "criteria": row["criteria"],
            "constraints": row["constraints_text"],
            "original_request": json.loads(row["original_request_json"]),
            "revision": row["revision"],
            "input_version": row["input_version"],
            "authority_version": row["authority_version"],
            "status": row["status"],
            "effects_allowed": bool(row["effects_allowed"]),
            "authority_mode": "effects" if row["effects_allowed"] else "draft",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _request_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "text": row["text"],
            "control": row["control"],
            "source": row["source"],
            "input_version": row["input_version"],
            "authority_version": row["authority_version"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _assignment_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "revision": row["revision"],
            "input_version": row["input_version"],
            "authority_version": row["authority_version"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def _usage_for(self, invocation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT raw_json, normalized_json, created_at FROM usage_records WHERE invocation_id = ?",
            (invocation_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "raw": self._json_load(row["raw_json"]),
            "normalized": self._json_load(row["normalized_json"]),
            "created_at": row["created_at"],
        }

    def _invocation_public(self, row: sqlite3.Row) -> dict[str, Any]:
        result = {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "assignment_id": row["assignment_id"],
            "input_version": row["input_version"],
            "revision": row["revision"],
            "authority_version": row["authority_version"],
            "context_artifact_id": row["context_artifact_id"],
            "status": row["status"],
            "result": row["result"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "usage": self._usage_for(row["id"]),
        }
        result["receipts"] = [
            self._receipt_public(receipt)
            for receipt in self._conn.execute(
                "SELECT * FROM receipts WHERE invocation_id = ? ORDER BY created_at, id",
                (row["id"],),
            ).fetchall()
        ]
        result["evidence"] = [
            self._evidence_public(evidence)
            for evidence in self._conn.execute(
                "SELECT * FROM evidence WHERE invocation_id = ? ORDER BY created_at, id",
                (row["id"],),
            ).fetchall()
        ]
        return result

    def run_attempts(self, goal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._goal_row(goal_id)
            rows = self._conn.execute(
                "SELECT * FROM run_attempts WHERE goal_id = ? ORDER BY created_at, id",
                (goal_id,),
            ).fetchall()
            return [self._run_attempt_public(row) for row in rows]
    def _run_attempt_public(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "run_id": row["id"],
            "goal_id": row["goal_id"],
            "assignment_id": row["assignment_id"],
            "invocation_id": row["invocation_id"],
            "status": row["status"],
            "stop_reason": row["stop_reason"] or None,
            "diagnostic": self._json_load(row["diagnostic_json"]),
            "assistant_text": row["assistant_text"],
            "result": row["assistant_text"],
            "limits": self._json_load(row["limits_json"]),
            "admission": (
                self._json_load(row["admission_json"])
                if row["admission_json"] is not None else None
            ),
            "stage_name": row["stage_name"],
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    def _artifact_public(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "invocation_id": row["invocation_id"],
            "kind": row["kind"],
            "name": row["name"],
            "content": row["content"],
            "content_hash": row["content_hash"],
            "revision": row["revision"],
            "input_version": row["input_version"],
            "authority_version": row["authority_version"],
            "applicability": {
                "revision": row["revision"],
                "input_version": row["input_version"],
                "authority_version": row["authority_version"],
            },
            "inputs": json.loads(row["inputs_json"]),
            "limitations": row["limitations"],
            "trust": row["trust"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _receipt_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "invocation_id": row["invocation_id"],
            "tool": row["tool"],
            "parameters": json.loads(row["parameters_json"]),
            "result": json.loads(row["result_json"]),
            "note": row["note"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _evidence_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "invocation_id": row["invocation_id"],
            "artifact_id": row["artifact_id"],
            "check": row["check_name"],
            "passed": bool(row["passed"]),
            "details": json.loads(row["details_json"]),
            "trusted": bool(row["trusted"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _effect_public(row: sqlite3.Row) -> dict[str, Any]:
        imported = row["state"] == "imported"
        return {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "invocation_id": row["invocation_id"],
            "artifact_id": row["artifact_id"],
            "target": row["target"],
            "expected_version": row["expected_version"],
            "candidate_content": row["candidate_content"],
            "evidence_id": row["evidence_id"],
            "operation_id": row["operation_id"],
            "state": row["state"],
            "created_at": row["created_at"],
            "approved_at": row["approved_at"],
            "gateway_state": {
                "prepared": "prepared",
                "approved": "authorized",
                "committed": "confirmed",
                "unresolved": "unresolved",
                "imported": "imported",
            }.get(row["state"], row["state"]),
            "delivery_verification": "unverified" if imported else (
                "confirmed" if row["state"] == "committed" else "not-delivered"
            ),
            "committed_at": row["committed_at"],
        }

    @staticmethod
    def _destination_history_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "operation_id": row["operation_id"],
            "target": row["target"],
            "version": row["version"],
            "content": row["content"],
            "effect_id": row["effect_id"],
            "committed_at": row["committed_at"],
        }

    @staticmethod
    def _acceptance_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "artifact_id": row["artifact_id"],
            "evidence_id": row["evidence_id"],
            "revision": row["revision"],
            "created_at": row["created_at"],
        }
    @staticmethod
    def _link_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "related_goal_id": row["related_goal_id"],
            "relationship": row["relationship"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _event_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "goal_id": row["goal_id"],
            "type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    def _insert_artifact(
        self,
        conn: sqlite3.Connection,
        *,
        goal_id: str,
        invocation_id: str,
        kind: str,
        content: str,
        name: str,
        inputs: list[str],
        limitations: str,
        trust: str,
        created_at: str | None = None,
    ) -> str:
        snapshot = conn.execute(
            """SELECT revision, input_version, authority_version
            FROM invocations WHERE id = ? AND goal_id = ?""",
            (invocation_id, goal_id),
        ).fetchone()
        if snapshot is None:
            raise KeyError(f"unknown invocation for goal: {invocation_id}")
        artifact_id = self._artifact_id(
            goal_id, invocation_id, kind, name, content, inputs, limitations, trust
        )
        content_hash = self._content_hash(content)
        existing = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        if existing is not None:
            expected = {
                "goal_id": goal_id,
                "invocation_id": invocation_id,
                "kind": kind,
                "name": name,
                "content": content,
                "content_hash": content_hash,
                "revision": snapshot["revision"],
                "input_version": snapshot["input_version"],
                "authority_version": snapshot["authority_version"],
                "inputs_json": self._json_dump(inputs, "inputs"),
                "limitations": limitations,
                "trust": trust,
            }
            if any(existing[key] != value for key, value in expected.items()):
                raise ValueError("artifact content-address collision")
            return artifact_id
        conn.execute(
            """INSERT INTO artifacts(
                id, goal_id, invocation_id, kind, name, content, content_hash,
                revision, input_version, authority_version, inputs_json,
                limitations, trust, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artifact_id,
                goal_id,
                invocation_id,
                kind,
                name,
                content,
                content_hash,
                snapshot["revision"],
                snapshot["input_version"],
                snapshot["authority_version"],
                self._json_dump(inputs, "inputs"),
                limitations,
                trust,
                created_at or self._now(),
            ),
        )
        return artifact_id

    def create_goal(
        self,
        outcome: str,
        parent_id: str | None = None,
        kind: str = "outcome",
        criteria: str = "",
        constraints: str = "",
    ) -> dict[str, Any]:
        outcome = self._text(outcome, "outcome", allow_empty=False)
        kind = self._text(kind, "kind", allow_empty=False)
        criteria = self._text(criteria, "criteria")
        constraints = self._text(constraints, "constraints")
        if parent_id is not None:
            parent_id = self._text(parent_id, "parent_id", allow_empty=False)
        now = self._now()
        goal_id = self._id()
        original = {
            "outcome": outcome,
            "parent_id": parent_id,
            "kind": kind,
            "criteria": criteria,
            "constraints": constraints,
        }
        with self._transaction() as conn:
            if parent_id is not None and conn.execute(
                "SELECT 1 FROM goals WHERE id = ?", (parent_id,)
            ).fetchone() is None:
                raise KeyError(f"unknown parent goal: {parent_id}")
            conn.execute(
                """INSERT INTO goals(
                    id, outcome, parent_id, kind, criteria, constraints_text,
                    original_request_json, revision, input_version, authority_version,
                    status, effects_allowed, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 1, 'draft', 0, ?, ?)""",
                (
                    goal_id,
                    outcome,
                    parent_id,
                    kind,
                    criteria,
                    constraints,
                    self._json_dump(original, "original request"),
                    now,
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO requests(
                    id, goal_id, text, control, source, input_version,
                    authority_version, created_at
                ) VALUES (?, ?, ?, NULL, 'create', 0, 1, ?)""",
                (self._id(), goal_id, outcome, now),
            )
            self._event(conn, goal_id, "goal_created", original)
            return self._goal_public(conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())
    def link_goal(
        self,
        goal_id: str,
        related_goal_id: str,
        relationship: str,
    ) -> dict[str, Any]:
        relationship = self._text(relationship, "relationship", allow_empty=False)
        if relationship not in {"prerequisite", "repair", "replacement"}:
            raise ValueError("relationship must be prerequisite, repair, or replacement")
        if goal_id == related_goal_id:
            raise ValueError("a goal cannot link to itself")
        with self._transaction() as conn:
            self._goal_row(goal_id)
            self._goal_row(related_goal_id)
            existing = conn.execute(
                """SELECT * FROM goal_links
                WHERE goal_id = ? AND related_goal_id = ? AND relationship = ?""",
                (goal_id, related_goal_id, relationship),
            ).fetchone()
            if existing is not None:
                return self._link_public(existing)
            link_id = self._id()
            now = self._now()
            conn.execute(
                """INSERT INTO goal_links(
                    id, goal_id, related_goal_id, relationship, created_at
                ) VALUES (?, ?, ?, ?, ?)""",
                (link_id, goal_id, related_goal_id, relationship, now),
            )
            self._event(
                conn,
                goal_id,
                "goal_linked",
                {
                    "link_id": link_id,
                    "related_goal_id": related_goal_id,
                    "relationship": relationship,
                },
            )
            return self._link_public(
                conn.execute("SELECT * FROM goal_links WHERE id = ?", (link_id,)).fetchone()
            )

    def links(self, goal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._goal_row(goal_id)
            return [
                self._link_public(link)
                for link in self._conn.execute(
                    """SELECT * FROM goal_links
                    WHERE goal_id = ? OR related_goal_id = ?
                    ORDER BY created_at, id""",
                    (goal_id, goal_id),
                ).fetchall()
            ]

    def move_goal(self, goal_id: str, parent_id: str | None) -> dict[str, Any]:
        if parent_id is not None:
            parent_id = self._text(parent_id, "parent_id", allow_empty=False)
        with self._transaction() as conn:
            self._goal_row(goal_id)
            if parent_id == goal_id:
                raise ValueError("a goal cannot parent itself")
            if parent_id is not None:
                if conn.execute("SELECT 1 FROM goals WHERE id = ?", (parent_id,)).fetchone() is None:
                    raise KeyError(f"unknown parent goal: {parent_id}")
                cursor = parent_id
                seen: set[str] = set()
                while cursor is not None:
                    if cursor in seen:
                        raise ValueError("goal hierarchy contains a cycle")
                    seen.add(cursor)
                    if cursor == goal_id:
                        raise ValueError("moving the goal would create a hierarchy cycle")
                    row = conn.execute("SELECT parent_id FROM goals WHERE id = ?", (cursor,)).fetchone()
                    cursor = row["parent_id"] if row is not None else None
            now = self._now()
            conn.execute(
                "UPDATE goals SET parent_id = ?, updated_at = ? WHERE id = ?",
                (parent_id, now, goal_id),
            )
            self._event(conn, goal_id, "goal_moved", {"parent_id": parent_id})
            return self._goal_public(conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())


    def list_goals(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM goals ORDER BY created_at, id").fetchall()
            return [self._goal_public(row) for row in rows]

    def goal(self, goal_id: str) -> dict[str, Any]:
        with self._read_snapshot():
            row = self._goal_row(goal_id)
            result = self._goal_public(row)
            result["links"] = [
                self._link_public(link)
                for link in self._conn.execute(
                    """SELECT * FROM goal_links
                    WHERE goal_id = ? OR related_goal_id = ?
                    ORDER BY created_at, id""",
                    (goal_id, goal_id),
                ).fetchall()
            ]
            result["requests"] = [
                self._request_public(request)
                for request in self._conn.execute(
                    "SELECT * FROM requests WHERE goal_id = ? ORDER BY created_at, id", (goal_id,)
                ).fetchall()
            ]
            result["artifacts"] = self.artifacts(goal_id)
            result["invocations"] = [
                self._invocation_public(invocation)
                for invocation in self._conn.execute(
                    "SELECT * FROM invocations WHERE goal_id = ? ORDER BY created_at, id", (goal_id,)
                ).fetchall()
            ]
            result["runs"] = [
                self._run_attempt_public(attempt)
                for attempt in self._conn.execute(
                    "SELECT * FROM run_attempts WHERE goal_id = ? ORDER BY created_at, id",
                    (goal_id,),
                ).fetchall()
            ]
            result["effects"] = self.effects(goal_id)
            result["events"] = [
                self._event_public(event)
                for event in self._conn.execute(
                    "SELECT * FROM events WHERE goal_id = ? ORDER BY created_at, id", (goal_id,)
                ).fetchall()
            ]
            result["acceptances"] = [
                self._acceptance_public(acceptance)
                for acceptance in self._conn.execute(
                    "SELECT * FROM acceptances WHERE goal_id = ? ORDER BY created_at, id", (goal_id,)
                ).fetchall()
            ]
            return result

    def request(self, goal_id: str, text: str, control: str | None = None) -> dict[str, Any]:
        text = self._text(text, "text")
        if control not in _ALLOWED_CONTROLS:
            raise ValueError(f"unsupported control: {control}")
        with self._transaction() as conn:
            goal = self._goal_row(goal_id)
            if goal["status"] == "cancelled":
                raise PermissionError("cancelled goals cannot receive requests")
            input_version = goal["input_version"] + 1
            authority_version = goal["authority_version"]
            status = goal["status"]
            effects_allowed = bool(goal["effects_allowed"])
            if control == "pause":
                status = "paused"
                effects_allowed = False
                authority_version += 1
            elif control == "cancel":
                status = "cancelled"
                effects_allowed = False
                authority_version += 1
            elif control == "draft":
                status = "draft"
                effects_allowed = False
                authority_version += 1
            elif control == "resume":
                status = "active"
                authority_version += 1
            elif control == "allow_effects":
                effects_allowed = True
                authority_version += 1
            now = self._now()
            conn.execute(
                """UPDATE goals SET input_version = ?, authority_version = ?, status = ?,
                    effects_allowed = ?, updated_at = ? WHERE id = ?""",
                (input_version, authority_version, status, int(effects_allowed), now, goal_id),
            )
            conn.execute(
                """INSERT INTO requests(
                    id, goal_id, text, control, source, input_version,
                    authority_version, created_at
                ) VALUES (?, ?, ?, ?, 'human', ?, ?, ?)""",
                (self._id(), goal_id, text, control, input_version, authority_version, now),
            )
            self._event(
                conn,
                goal_id,
                "request",
                {"text": text, "control": control, "input_version": input_version},
            )
            return self._goal_public(conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())

    def pause_for_interruption(self, goal_id: str, reason: str = "run interrupted") -> dict[str, Any]:
        reason = self._text(reason, "reason", allow_empty=False)
        with self._transaction() as conn:
            goal = self._goal_row(goal_id)
            if goal["status"] == "cancelled":
                return self._goal_public(goal)
            authority_version = goal["authority_version"] + 1
            now = self._now()
            conn.execute(
                """UPDATE goals SET status = 'paused', effects_allowed = 0,
                    authority_version = ?, updated_at = ? WHERE id = ?""",
                (authority_version, now, goal_id),
            )
            self._event(
                conn,
                goal_id,
                "run_paused",
                {"reason": reason, "authority_version": authority_version},
            )
            return self._goal_public(conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())

    def reopen_for_run(self, goal_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            goal = self._goal_row(goal_id)
            if goal["status"] == "cancelled":
                raise PermissionError("cancelled goals cannot be reopened")
            status = "active" if goal["status"] == "paused" else goal["status"]
            authority_version = goal["authority_version"]
            if status != goal["status"] or goal["effects_allowed"]:
                authority_version += 1
            now = self._now()
            conn.execute(
                """UPDATE goals SET status = ?, effects_allowed = 0,
                    authority_version = ?, updated_at = ? WHERE id = ?""",
                (status, authority_version, now, goal_id),
            )
            self._event(
                conn,
                goal_id,
                "run_reopened",
                {"authority_version": authority_version},
            )
            return self._goal_public(conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())

    def revise(
        self,
        goal_id: str,
        expected_revision: int,
        outcome: str,
        criteria: str,
        constraints: str,
    ) -> dict[str, Any]:
        expected_revision = self._integer(expected_revision, "expected_revision", minimum=1)
        outcome = self._text(outcome, "outcome", allow_empty=False)
        criteria = self._text(criteria, "criteria")
        constraints = self._text(constraints, "constraints")
        with self._transaction() as conn:
            goal = self._goal_row(goal_id)
            if goal["status"] == "cancelled":
                raise PermissionError("cancelled goals cannot be revised")
            if goal["revision"] != expected_revision:
                raise PermissionError("revision fence rejected the revision")
            revision = expected_revision + 1
            input_version = goal["input_version"] + 1
            now = self._now()
            conn.execute(
                """UPDATE goals SET outcome = ?, criteria = ?, constraints_text = ?,
                    revision = ?, input_version = ?, updated_at = ? WHERE id = ?""",
                (outcome, criteria, constraints, revision, input_version, now, goal_id),
            )
            self._event(
                conn,
                goal_id,
                "revised",
                {"revision": revision, "input_version": input_version},
            )
            return self._goal_public(conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone())

    def local_stage(self, goal_id: str) -> str | None:
        """Controller-only recovery metadata; never imported or exported."""
        with self._lock:
            self._goal_row(goal_id)
            row = self._conn.execute(
                """SELECT stage_name FROM run_stages
                WHERE goal_id = ?
                ORDER BY rowid DESC LIMIT 1""",
                (goal_id,),
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    "SELECT stage_name FROM local_stages WHERE goal_id = ?", (goal_id,)
                ).fetchone()
            return row["stage_name"] if row is not None else None

    def remember_local_stage(self, assignment_id: str, stage_name: str) -> bool:
        """Retain stopped work without letting a replaced run move recovery back."""
        if not isinstance(stage_name, str) or not re.fullmatch(r"run-[A-Za-z0-9_-]{1,96}", stage_name):
            raise ValueError("local stage must be a run directory name, not a path")
        with self._transaction() as conn:
            assignment = conn.execute(
                "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
            ).fetchone()
            if assignment is None:
                raise KeyError(f"unknown assignment: {assignment_id}")
            if assignment["status"] != "active":
                return False
            if conn.execute(
                "SELECT 1 FROM invocations WHERE assignment_id = ? AND status = 'running' LIMIT 1",
                (assignment_id,),
            ).fetchone():
                raise PermissionError("cannot remember files from a running invocation")
            attempt = conn.execute(
                """SELECT * FROM run_attempts
                WHERE assignment_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1""",
                (assignment_id,),
            ).fetchone()
            if attempt is not None:
                if attempt["status"] == "running":
                    raise PermissionError("cannot remember files from a running attempt")
                conn.execute(
                    """INSERT INTO run_stages(goal_id, attempt_id, stage_name)
                    VALUES (?, ?, ?) ON CONFLICT(goal_id) DO UPDATE SET
                        attempt_id = excluded.attempt_id, stage_name = excluded.stage_name""",
                    (attempt["goal_id"], attempt["id"], stage_name),
                )
                conn.execute(
                    "UPDATE run_attempts SET stage_name = ? WHERE id = ?",
                    (stage_name, attempt["id"]),
                )
                return True
            invocation = conn.execute(
                "SELECT * FROM invocations WHERE assignment_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (assignment_id,),
            ).fetchone()
            if invocation is None:
                return False
            conn.execute(
                """INSERT INTO local_stages(goal_id, invocation_id, stage_name)
                VALUES (?, ?, ?) ON CONFLICT(goal_id) DO UPDATE SET
                    invocation_id = excluded.invocation_id, stage_name = excluded.stage_name""",
                (invocation["goal_id"], invocation["id"], stage_name),
            )
            return True

    def assign(self, goal_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            goal = self._goal_row(goal_id)
            if goal["status"] in {"paused", "cancelled"}:
                raise PermissionError(f"cannot assign a {goal['status']} goal")
            conn.execute(
                "UPDATE assignments SET status = 'fenced' WHERE goal_id = ? AND status = 'active'",
                (goal_id,),
            )
            assignment_id = self._id()
            now = self._now()
            conn.execute(
                """INSERT INTO assignments(
                    id, goal_id, revision, input_version, authority_version, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                (
                    assignment_id,
                    goal_id,
                    goal["revision"],
                    goal["input_version"],
                    goal["authority_version"],
                    now,
                ),
            )
            self._event(conn, goal_id, "assignment_created", {"assignment_id": assignment_id})
            return self._assignment_public(
                conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
            )

    def start_run(
        self,
        goal_id: str,
        assignment_id: str,
        limits: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        limits = {} if limits is None else limits
        if not isinstance(limits, dict):
            raise ValueError("run limits must be an object")
        limits_json = self._json_dump(limits, "run limits")
        with self._transaction() as conn:
            goal = self._goal_row(goal_id)
            assignment = conn.execute(
                "SELECT * FROM assignments WHERE id = ? AND goal_id = ?",
                (assignment_id, goal_id),
            ).fetchone()
            if assignment is None:
                raise KeyError(f"unknown assignment for goal: {assignment_id}")
            if assignment["status"] != "active":
                raise PermissionError("assignment is fenced")
            if any(
                assignment[key] != goal[key]
                for key in ("revision", "input_version", "authority_version")
            ):
                raise PermissionError("assignment snapshot is stale")
            run_id = self._id()
            now = self._now()
            conn.execute(
                """INSERT INTO run_attempts(
                    id, goal_id, assignment_id, status, stop_reason,
                    diagnostic_json, assistant_text, limits_json, created_at
                ) VALUES (?, ?, ?, 'running', '', '{}', '', ?, ?)""",
                (run_id, goal_id, assignment_id, limits_json, now),
            )
            self._event(conn, goal_id, "run_started", {"run_id": run_id, "assignment_id": assignment_id})
            return self._run_attempt_public(
                conn.execute("SELECT * FROM run_attempts WHERE id = ?", (run_id,)).fetchone()
            )

    def record_run_admission(self, run_id: str, admission: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(admission, dict):
            raise ValueError("run admission must be an object")
        admission_json = self._json_dump(admission, "run admission")
        with self._transaction() as conn:
            run = conn.execute("SELECT * FROM run_attempts WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run: {run_id}")
            if run["status"] != "running":
                raise PermissionError("finished run is immutable")
            conn.execute(
                "UPDATE run_attempts SET admission_json = ? WHERE id = ?",
                (admission_json, run_id),
            )
            return self._run_attempt_public(
                conn.execute("SELECT * FROM run_attempts WHERE id = ?", (run_id,)).fetchone()
            )

    def bind_run_invocation(self, run_id: str, invocation_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            run = conn.execute("SELECT * FROM run_attempts WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run: {run_id}")
            invocation = self._invocation_row(invocation_id)
            if invocation["goal_id"] != run["goal_id"] or invocation["assignment_id"] != run["assignment_id"]:
                raise PermissionError("run invocation fence rejected")
            if run["status"] != "running":
                raise PermissionError("finished run is immutable")
            conn.execute(
                "UPDATE run_attempts SET invocation_id = ? WHERE id = ?",
                (invocation_id, run_id),
            )
            return self._run_attempt_public(
                conn.execute("SELECT * FROM run_attempts WHERE id = ?", (run_id,)).fetchone()
            )

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        stop_reason: str | None = None,
        diagnostic: dict[str, Any] | None = None,
        assistant_text: str = "",
        admission: dict[str, Any] | None = None,
        invocation_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in _ALLOWED_RUN_STATUSES - {"running"}:
            raise ValueError(f"unsupported run status: {status}")
        assistant_text = self._text(assistant_text, "assistant_text")
        reason = "" if stop_reason is None else self._text(stop_reason, "stop_reason")
        diagnostic = {} if diagnostic is None else diagnostic
        if not isinstance(diagnostic, dict):
            raise ValueError("run diagnostic must be an object")
        admission_json = None
        if admission is not None:
            if not isinstance(admission, dict):
                raise ValueError("run admission must be an object")
            admission_json = self._json_dump(admission, "run admission")
        diagnostic_json = self._json_dump(diagnostic, "run diagnostic")
        with self._transaction() as conn:
            run = conn.execute("SELECT * FROM run_attempts WHERE id = ?", (run_id,)).fetchone()
            if run is None:
                raise KeyError(f"unknown run: {run_id}")
            if run["status"] != "running":
                return self._run_attempt_public(run)
            if invocation_id is not None:
                invocation = self._invocation_row(invocation_id)
                if invocation["goal_id"] != run["goal_id"] or invocation["assignment_id"] != run["assignment_id"]:
                    raise PermissionError("run invocation fence rejected")
            finished_at = self._now()
            conn.execute(
                """UPDATE run_attempts SET status = ?, stop_reason = ?,
                    diagnostic_json = ?, assistant_text = ?,
                    admission_json = COALESCE(?, admission_json),
                    invocation_id = COALESCE(?, invocation_id), finished_at = ?
                    WHERE id = ?""",
                (
                    status, reason, diagnostic_json, assistant_text,
                    admission_json, invocation_id, finished_at, run_id,
                ),
            )
            self._event(
                conn,
                run["goal_id"],
                "run_finished",
                {"run_id": run_id, "status": status, "stop_reason": reason or None},
            )
            return self._run_attempt_public(
                conn.execute("SELECT * FROM run_attempts WHERE id = ?", (run_id,)).fetchone()
            )
    def invoke(
        self,
        goal_id: str,
        assignment_id: str,
        context: Any,
        expected_input_version: int | None = None,
    ) -> dict[str, Any]:
        if expected_input_version is not None:
            expected_input_version = self._integer(
                expected_input_version, "expected_input_version", minimum=0
            )
        context_content = self._content_for_context(context)
        with self._transaction() as conn:
            goal = self._goal_row(goal_id)
            if goal["status"] in {"paused", "cancelled"}:
                raise PermissionError(f"cannot invoke a {goal['status']} goal")
            assignment = conn.execute(
                "SELECT * FROM assignments WHERE id = ? AND goal_id = ?",
                (assignment_id, goal_id),
            ).fetchone()
            if assignment is None:
                raise KeyError(f"unknown assignment for goal: {assignment_id}")
            if assignment["status"] != "active":
                raise PermissionError("assignment is fenced")
            if expected_input_version is not None and goal["input_version"] != expected_input_version:
                raise PermissionError("input fence rejected invocation")
            if any(
                assignment[key] != goal[key]
                for key in ("revision", "input_version", "authority_version")

            ):
                raise PermissionError("assignment snapshot is stale")
            invocation_id = self._id()
            now = self._now()
            conn.execute(
                """INSERT INTO invocations(
                    id, goal_id, assignment_id, input_version, revision,
                    authority_version, context_artifact_id, status, result, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'running', '', ?)""",
                (
                    invocation_id,
                    goal_id,
                    assignment_id,
                    goal["input_version"],
                    goal["revision"],
                    goal["authority_version"],
                    now,
                ),
            )
            context_id = self._insert_artifact(
                conn,
                goal_id=goal_id,
                invocation_id=invocation_id,
                kind="context",
                name="invocation-context",
                content=context_content,
                inputs=[],
                limitations="",
                trust="controller",
                created_at=now,
            )
            conn.execute(
                "UPDATE invocations SET context_artifact_id = ? WHERE id = ?",
                (context_id, invocation_id),
            )
            self._event(
                conn,
                goal_id,
                "invocation_started",
                {"invocation_id": invocation_id, "assignment_id": assignment_id},
            )
            return self._invocation_public(
                conn.execute("SELECT * FROM invocations WHERE id = ?", (invocation_id,)).fetchone()
            )

    def artifact(
        self,
        goal_id: str,
        invocation_id: str,
        kind: str,
        content: str,
        name: str = "",
        inputs: list[str] | None = None,
        limitations: str = "",
        trust: str = "worker",
    ) -> dict[str, Any]:
        kind = self._text(kind, "kind", allow_empty=False)
        content = self._text(content, "content")
        name = self._text(name, "name")
        limitations = self._text(limitations, "limitations")
        trust = self._text(trust, "trust", allow_empty=False)
        if inputs is None:
            inputs = []
        if not isinstance(inputs, list) or any(not isinstance(item, str) for item in inputs):
            raise ValueError("inputs must be a list of artifact IDs")
        with self._transaction() as conn:
            self._goal_row(goal_id)
            invocation = conn.execute(
                "SELECT * FROM invocations WHERE id = ? AND goal_id = ?",
                (invocation_id, goal_id),
            ).fetchone()
            if invocation is None:
                raise KeyError(f"unknown invocation for goal: {invocation_id}")
            for input_id in inputs:
                if conn.execute("SELECT 1 FROM artifacts WHERE id = ?", (input_id,)).fetchone() is None:
                    raise KeyError(f"unknown input artifact: {input_id}")
            artifact_id = self._insert_artifact(
                conn,
                goal_id=goal_id,
                invocation_id=invocation_id,
                kind=kind,
                content=content,
                name=name,
                inputs=inputs,
                limitations=limitations,
                trust=trust,
            )
            return self._artifact_public(conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone())

    def artifacts(self, goal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._goal_row(goal_id)
            rows = self._conn.execute(
                "SELECT * FROM artifacts WHERE goal_id = ? ORDER BY created_at, rowid",
                (goal_id,),
            ).fetchall()
            return [self._artifact_public(row) for row in rows]

    def receipt(
        self,
        invocation_id: str,
        tool: str,
        parameters: Any,
        result: Any,
        note: str = "",
    ) -> dict[str, Any]:
        tool = self._text(tool, "tool", allow_empty=False)
        note = self._text(note, "note")
        parameters_json = self._json_dump(parameters, "parameters")
        result_json = self._json_dump(result, "result")
        with self._transaction() as conn:
            self._invocation_row(invocation_id)
            receipt_id = self._id()
            now = self._now()
            conn.execute(
                """INSERT INTO receipts(
                    id, invocation_id, tool, parameters_json, result_json, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (receipt_id, invocation_id, tool, parameters_json, result_json, note, now),
            )
            return self._receipt_public(conn.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone())

    @staticmethod
    def _normalized_usage(usage: dict[str, Any]) -> dict[str, int | None]:
        def first_int(*keys: str) -> int | None:
            for key in keys:
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return value
            return None

        input_tokens = first_int("input_tokens", "prompt_tokens", "inputTokens")
        output_tokens = first_int("output_tokens", "completion_tokens", "outputTokens")
        total_tokens = first_int("total_tokens", "totalTokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cached_tokens": first_int(
                "cached_tokens", "cache_read_input_tokens", "cached_input_tokens"
            ),
            "reasoning_tokens": first_int("reasoning_tokens", "reasoning_output_tokens"),
        }

    def usage(self, invocation_id: str, usage_dict: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(usage_dict, dict):
            raise ValueError("usage_dict must be a dictionary")
        raw_json = self._json_dump(usage_dict, "usage_dict")
        normalized_json = self._json_dump(self._normalized_usage(usage_dict), "normalized usage")
        with self._transaction() as conn:
            self._invocation_row(invocation_id)
            now = self._now()
            conn.execute(
                """INSERT INTO usage_records(invocation_id, raw_json, normalized_json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(invocation_id) DO UPDATE SET
                    raw_json = excluded.raw_json,
                    normalized_json = excluded.normalized_json,
                    created_at = excluded.created_at""",
                (invocation_id, raw_json, normalized_json, now),
            )
            return self._usage_for(invocation_id) or {}

    def finish(self, invocation_id: str, status: str, result: str = "") -> dict[str, Any]:
        if status not in _ALLOWED_FINISH_STATUSES:
            raise ValueError(f"unsupported invocation status: {status}")
        result = self._text(result, "result")
        with self._transaction() as conn:
            invocation = self._invocation_row(invocation_id)
            if invocation["status"] in _ALLOWED_FINISH_STATUSES:
                if invocation["status"] != status or invocation["result"] != result:
                    raise PermissionError("finished invocation is immutable")
                return self._invocation_public(invocation)
            finished_at = self._now()
            conn.execute(
                "UPDATE invocations SET status = ?, result = ?, finished_at = ? WHERE id = ?",
                (status, result, finished_at, invocation_id),
            )
            self._event(
                conn,
                invocation["goal_id"],
                "invocation_finished",
                {"invocation_id": invocation_id, "status": status},
            )
            return self._invocation_public(
                conn.execute("SELECT * FROM invocations WHERE id = ?", (invocation_id,)).fetchone()
            )

    def verify(
        self,
        invocation_id: str,
        artifact_id: str,
        check: str,
        passed: bool,
        details: Any,
        trusted: bool = False,
    ) -> dict[str, Any]:
        check = self._text(check, "check", allow_empty=False)
        if not isinstance(passed, bool) or not isinstance(trusted, bool):
            raise ValueError("passed and trusted must be booleans")
        details_json = self._json_dump(details, "details")
        with self._transaction() as conn:
            invocation = self._invocation_row(invocation_id)
            artifact = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if artifact is None:
                raise KeyError(f"unknown artifact: {artifact_id}")
            if artifact["invocation_id"] != invocation_id or artifact["goal_id"] != invocation["goal_id"]:
                raise PermissionError("evidence artifact is not bound to the invocation")
            evidence_id = self._id()
            now = self._now()
            conn.execute(
                """INSERT INTO evidence(
                    id, invocation_id, artifact_id, check_name, passed,
                    details_json, trusted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence_id,
                    invocation_id,
                    artifact_id,
                    check,
                    int(passed),
                    details_json,
                    int(trusted),
                    now,
                ),
            )
            return self._evidence_public(conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone())

    def _assert_invocation_fresh(
        self,
        conn: sqlite3.Connection,
        invocation: sqlite3.Row,
        goal: sqlite3.Row,
    ) -> None:
        if goal["status"] in {"paused", "cancelled"}:
            raise PermissionError(f"goal is {goal['status']}")
        if any(invocation[key] != goal[key] for key in ("revision", "input_version", "authority_version")):
            raise PermissionError("invocation snapshot is stale")
        assignment = conn.execute(
            "SELECT status FROM assignments WHERE id = ? AND goal_id = ?",
            (invocation["assignment_id"], invocation["goal_id"]),
        ).fetchone()
        if assignment is None or assignment["status"] != "active":
            raise PermissionError("assignment is fenced")

    def assert_invocation_current(self, invocation_id: str) -> None:
        """Fence a controlled tool dispatch; in-flight code still needs isolated resources."""
        with self._read_snapshot():
            invocation = self._invocation_row(invocation_id)
            goal = self._goal_row(invocation["goal_id"])
            self._assert_invocation_fresh(self._conn, invocation, goal)

    def _effect_context(
        self,
        conn: sqlite3.Connection,
        effect: sqlite3.Row,
        *,
        require_permission: bool = True,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        goal = self._goal_row(effect["goal_id"])
        invocation = conn.execute(
            "SELECT * FROM invocations WHERE id = ? AND goal_id = ?",
            (effect["invocation_id"], effect["goal_id"]),
        ).fetchone()
        artifact = conn.execute(
            "SELECT * FROM artifacts WHERE id = ? AND goal_id = ?",
            (effect["artifact_id"], effect["goal_id"]),
        ).fetchone()
        evidence = conn.execute(
            "SELECT * FROM evidence WHERE id = ? AND invocation_id = ? AND artifact_id = ?",
            (effect["evidence_id"], effect["invocation_id"], effect["artifact_id"]),
        ).fetchone()
        destination = conn.execute(
            "SELECT * FROM destinations WHERE target = ?", (effect["target"],)
        ).fetchone()
        if invocation is None or artifact is None or evidence is None:
            raise PermissionError("effect provenance is incomplete")
        if artifact["invocation_id"] != invocation["id"] or artifact["content"] != effect["candidate_content"]:
            raise PermissionError("effect candidate changed")
        if not evidence["passed"] or not evidence["trusted"]:
            raise PermissionError("effect requires trusted passing evidence")
        self._assert_invocation_fresh(conn, invocation, goal)
        if require_permission:
            if not goal["effects_allowed"]:
                raise PermissionError("mock effects require explicit user authority")
        if destination is None:
            self._ensure_destination(conn, effect["target"])
            destination = conn.execute(
                "SELECT * FROM destinations WHERE target = ?", (effect["target"],)
            ).fetchone()
        if destination["version"] != effect["expected_version"]:
            raise PermissionError("destination version fence rejected the effect")
        return goal, invocation, artifact, evidence, destination

    @staticmethod
    def _ensure_destination(conn: sqlite3.Connection, target: str) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO destinations(
                target, version, content, operation_id, updated_at
            ) VALUES (?, 0, '', NULL, ?)""",
            (target, Store._now()),
        )

    def prepare_effect(
        self,
        invocation_id: str,
        artifact_id: str,
        target: str,
        expected_version: int,
        evidence_id: str,
    ) -> dict[str, Any]:
        target = self._text(target, "target", allow_empty=False)
        expected_version = self._integer(expected_version, "expected_version", minimum=0)
        with self._transaction() as conn:
            invocation = self._invocation_row(invocation_id)
            artifact = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if artifact is None:
                raise KeyError(f"unknown artifact: {artifact_id}")
            evidence = conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
            if evidence is None:
                raise KeyError(f"unknown evidence: {evidence_id}")
            if (
                artifact["goal_id"] != invocation["goal_id"]
                or artifact["invocation_id"] != invocation_id
                or evidence["invocation_id"] != invocation_id
                or evidence["artifact_id"] != artifact_id
            ):
                raise PermissionError("effect provenance is not invocation-bound")
            if not evidence["passed"] or not evidence["trusted"]:
                raise PermissionError("effect requires trusted passing evidence")
            goal = self._goal_row(invocation["goal_id"])
            self._assert_invocation_fresh(conn, invocation, goal)
            self._ensure_destination(conn, target)
            destination = conn.execute(
                "SELECT version FROM destinations WHERE target = ?", (target,)
            ).fetchone()
            if destination["version"] != expected_version:
                raise PermissionError("destination version fence rejected preparation")
            effect_id = self._id()
            operation_id = self._id()
            now = self._now()
            conn.execute(
                """INSERT INTO effects(
                    id, goal_id, invocation_id, artifact_id, target, expected_version,
                    candidate_content, evidence_id, operation_id, state, created_at,
                    approved_at, committed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?, NULL, NULL)""",
                (
                    effect_id,
                    goal["id"],
                    invocation_id,
                    artifact_id,
                    target,
                    expected_version,
                    artifact["content"],
                    evidence_id,
                    operation_id,
                    now,
                ),
            )
            self._event(
                conn,
                goal["id"],
                "effect_prepared",
                {"effect_id": effect_id, "target": target, "expected_version": expected_version},
            )
            return self._effect_public(conn.execute("SELECT * FROM effects WHERE id = ?", (effect_id,)).fetchone())

    def approve(self, effect_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            effect = self._effect_row(effect_id)
            if effect["state"] in {"committed", "unresolved"}:
                return self._effect_public(effect)
            if effect["state"] == "approved":
                self._effect_context(conn, effect)
                return self._effect_public(effect)
            if effect["state"] != "prepared":
                raise PermissionError("effect is not approvable")
            goal, _invocation, _artifact, _evidence, _destination = self._effect_context(conn, effect)
            now = self._now()
            conn.execute(
                "UPDATE effects SET state = 'approved', approved_at = ? WHERE id = ?",
                (now, effect_id),
            )
            self._event(conn, goal["id"], "effect_approved", {"effect_id": effect_id})
            return self._effect_public(conn.execute("SELECT * FROM effects WHERE id = ?", (effect_id,)).fetchone())

    def commit(self, effect_id: str, lose_response: bool = False) -> dict[str, Any]:
        if not isinstance(lose_response, bool):
            raise ValueError("lose_response must be a boolean")
        with self._transaction() as conn:
            effect = self._effect_row(effect_id)
            if effect["state"] in {"committed", "unresolved"}:
                return self._effect_public(effect)
            if effect["state"] != "approved":
                raise PermissionError("effect must be approved before commit")
            goal, _invocation, _artifact, _evidence, destination = self._effect_context(conn, effect)
            changed = conn.execute(
                """UPDATE destinations SET version = ?, content = ?, operation_id = ?, updated_at = ?
                WHERE target = ? AND version = ?""",
                (
                    effect["expected_version"] + 1,
                    effect["candidate_content"],
                    effect["operation_id"],
                    self._now(),
                    effect["target"],
                    effect["expected_version"],
                ),
            ).rowcount
            if changed != 1:
                raise PermissionError("destination compare-and-set rejected the effect")
            committed_at = self._now()
            conn.execute(
                """INSERT INTO destination_history(
                    operation_id, target, version, content, effect_id, committed_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    effect["operation_id"],
                    effect["target"],
                    destination["version"] + 1,
                    effect["candidate_content"],
                    effect_id,
                    committed_at,
                ),
            )
            state = "unresolved" if lose_response else "committed"
            conn.execute(
                "UPDATE effects SET state = ?, committed_at = ? WHERE id = ?",
                (state, committed_at, effect_id),
            )
            self._event(
                conn,
                goal["id"],
                "effect_committed" if not lose_response else "effect_response_lost",
                {"effect_id": effect_id, "operation_id": effect["operation_id"], "state": state},
            )
            return self._effect_public(conn.execute("SELECT * FROM effects WHERE id = ?", (effect_id,)).fetchone())

    def reconcile(self, effect_id: str) -> dict[str, Any]:
        with self._transaction() as conn:
            effect = self._effect_row(effect_id)
            if effect["state"] in {"committed", "imported"}:
                return self._effect_public(effect)
            history = conn.execute(
                "SELECT * FROM destination_history WHERE operation_id = ?", (effect["operation_id"],)
            ).fetchone()
            if history is not None:
                if (
                    history["target"] != effect["target"]
                    or history["version"] != effect["expected_version"] + 1
                    or history["content"] != effect["candidate_content"]
                    or history["effect_id"] != effect_id
                ):
                    raise PermissionError("destination operation history is inconsistent")
                conn.execute(
                    "UPDATE effects SET state = 'committed', committed_at = COALESCE(committed_at, ?) WHERE id = ?",
                    (history["committed_at"], effect_id),
                )
                self._event(
                    conn,
                    effect["goal_id"],
                    "effect_reconciled",
                    {"effect_id": effect_id, "operation_id": effect["operation_id"]},
                )
            else:
                destination = conn.execute(
                    "SELECT * FROM destinations WHERE target = ?", (effect["target"],)
                ).fetchone()
                if (
                    destination is not None
                    and destination["operation_id"] == effect["operation_id"]
                    and destination["version"] == effect["expected_version"] + 1
                    and destination["content"] == effect["candidate_content"]
                ):
                    committed_at = destination["updated_at"]
                    conn.execute(
                        """INSERT INTO destination_history(
                            operation_id, target, version, content, effect_id, committed_at
                        ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            effect["operation_id"],
                            effect["target"],
                            destination["version"],
                            destination["content"],
                            effect_id,
                            committed_at,
                        ),
                    )
                    conn.execute(
                        "UPDATE effects SET state = 'committed', committed_at = COALESCE(committed_at, ?) WHERE id = ?",
                        (committed_at, effect_id),
                    )
                    self._event(
                        conn,
                        effect["goal_id"],
                        "effect_reconciled",
                        {"effect_id": effect_id, "operation_id": effect["operation_id"]},
                    )
            return self._effect_public(conn.execute("SELECT * FROM effects WHERE id = ?", (effect_id,)).fetchone())


    def effects(self, goal_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._goal_row(goal_id)
            return [
                self._effect_public(row)
                for row in self._conn.execute(
                    "SELECT * FROM effects WHERE goal_id = ? ORDER BY created_at, id", (goal_id,)
                ).fetchall()
            ]

    def destination(self, target: str) -> dict[str, Any]:
        target = self._text(target, "target", allow_empty=False)
        with self._transaction() as conn:
            self._ensure_destination(conn, target)
            row = conn.execute("SELECT * FROM destinations WHERE target = ?", (target,)).fetchone()
            return {
                "target": row["target"],
                "version": row["version"],
                "content": row["content"],
                "operation_id": row["operation_id"],
            }

    def accept(
        self,
        goal_id: str,
        artifact_id: str,
        evidence_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        expected_revision = self._integer(expected_revision, "expected_revision", minimum=1)
        with self._transaction() as conn:
            goal = self._goal_row(goal_id)
            if goal["revision"] != expected_revision:
                raise PermissionError("acceptance revision fence rejected the candidate")
            artifact = conn.execute(
                "SELECT * FROM artifacts WHERE id = ? AND goal_id = ?", (artifact_id, goal_id)
            ).fetchone()
            if artifact is None:
                raise KeyError(f"unknown artifact for goal: {artifact_id}")
            evidence = conn.execute(
                "SELECT * FROM evidence WHERE id = ? AND artifact_id = ?", (evidence_id, artifact_id)
            ).fetchone()
            if evidence is None:
                raise KeyError(f"unknown evidence for artifact: {evidence_id}")
            invocation = conn.execute(
                "SELECT * FROM invocations WHERE id = ? AND goal_id = ?",
                (evidence["invocation_id"], goal_id),
            ).fetchone()
            if invocation is None or invocation["revision"] != goal["revision"] or invocation["input_version"] != goal["input_version"]:
                raise PermissionError("acceptance candidate is stale")
            self._assert_invocation_fresh(conn, invocation, goal)
            if not evidence["passed"] or not evidence["trusted"]:
                raise PermissionError("acceptance requires trusted passing evidence")
            existing = conn.execute(
                """SELECT * FROM acceptances WHERE goal_id = ? AND artifact_id = ?
                AND evidence_id = ? AND revision = ?""",
                (goal_id, artifact_id, evidence_id, expected_revision),
            ).fetchone()
            if existing is not None:
                return self._acceptance_public(existing)
            acceptance_id = self._id()
            now = self._now()
            conn.execute(
                """INSERT INTO acceptances(
                    id, goal_id, artifact_id, evidence_id, revision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (acceptance_id, goal_id, artifact_id, evidence_id, expected_revision, now),
            )
            self._event(
                conn,
                goal_id,
                "artifact_accepted",
                {"artifact_id": artifact_id, "evidence_id": evidence_id, "revision": expected_revision},
            )
            return self._acceptance_public(
                conn.execute("SELECT * FROM acceptances WHERE id = ?", (acceptance_id,)).fetchone()
            )

    def remember_workspace_baseline(
        self, goal_id: str, stage_name: str, files: dict[str, Any], selection: dict[str, Any]
    ) -> None:
        """Bind an immutable selected baseline to one local stage, never an import."""
        if not isinstance(stage_name, str) or not re.fullmatch(r"run-[A-Za-z0-9_-]{1,96}", stage_name):
            raise ValueError("workspace stage must be a run directory name")
        if not isinstance(files, dict) or not isinstance(selection, dict):
            raise ValueError("workspace baseline and selection must be objects")
        serialized = self._json_dump(files, "workspace files")
        selected = self._json_dump(selection, "source selection")
        digest = self._content_hash(serialized)
        with self._transaction() as conn:
            self._goal_row(goal_id)
            existing = conn.execute(
                "SELECT baseline_hash, selection_json FROM local_workspaces WHERE goal_id = ? AND stage_name = ?",
                (goal_id, stage_name),
            ).fetchone()
            if existing is not None:
                if existing["baseline_hash"] != digest or existing["selection_json"] != selected:
                    raise ValueError("workspace baseline is immutable")
                return
            conn.execute(
                "INSERT OR IGNORE INTO workspace_snapshots(content_hash, files_json) VALUES (?, ?)",
                (digest, serialized),
            )
            conn.execute(
                "INSERT INTO local_workspaces(goal_id, stage_name, baseline_hash, selection_json) VALUES (?, ?, ?, ?)",
                (goal_id, stage_name, digest, selected),
            )

    def workspace_baseline(self, goal_id: str, stage_name: str) -> dict[str, Any] | None:
        with self._lock:
            self._goal_row(goal_id)
            row = self._conn.execute(
                """SELECT snapshots.files_json, local.selection_json
                FROM local_workspaces AS local JOIN workspace_snapshots AS snapshots
                    ON snapshots.content_hash = local.baseline_hash
                WHERE local.goal_id = ? AND local.stage_name = ?""",
                (goal_id, stage_name),
            ).fetchone()
            if row is None:
                return None
            return {"files": self._json_load(row["files_json"]), "selection": self._json_load(row["selection_json"])}

    @staticmethod
    def _export_rows(store: "Store") -> dict[str, list[dict[str, Any]]]:
        conn = store._conn
        goals = [
            store._goal_public(row)
            for row in conn.execute("SELECT * FROM goals ORDER BY created_at, id").fetchall()
        ]
        requests = [
            store._request_public(row)
            for row in conn.execute("SELECT * FROM requests ORDER BY created_at, id").fetchall()
        ]
        links = [
            store._link_public(row)
            for row in conn.execute("SELECT * FROM goal_links ORDER BY created_at, id").fetchall()
        ]
        assignments = [
            store._assignment_public(row)
            for row in conn.execute("SELECT * FROM assignments ORDER BY created_at, id").fetchall()
        ]
        invocations = [
            store._invocation_public(row)
            for row in conn.execute("SELECT * FROM invocations ORDER BY created_at, id").fetchall()
        ]
        artifacts = [
            store._artifact_public(row)
            for row in conn.execute("SELECT * FROM artifacts ORDER BY created_at, rowid").fetchall()
        ]
        receipts = [
            store._receipt_public(row)
            for row in conn.execute("SELECT * FROM receipts ORDER BY created_at, id").fetchall()
        ]
        usage = [
            {
                "invocation_id": row["invocation_id"],
                "raw": json.loads(row["raw_json"]),
                "normalized": json.loads(row["normalized_json"]),
                "created_at": row["created_at"],
            }
            for row in conn.execute("SELECT * FROM usage_records ORDER BY created_at, invocation_id").fetchall()
        ]
        evidence = [
            store._evidence_public(row)
            for row in conn.execute("SELECT * FROM evidence ORDER BY created_at, id").fetchall()
        ]
        effects = [
            store._effect_public(row)
            for row in conn.execute("SELECT * FROM effects ORDER BY created_at, id").fetchall()
        ]
        destinations = [
            {
                "target": row["target"],
                "version": row["version"],
                "content": row["content"],
                "operation_id": row["operation_id"],
                "updated_at": row["updated_at"],
            }
            for row in conn.execute("SELECT * FROM destinations ORDER BY target").fetchall()
        ]
        destination_history = [
            store._destination_history_public(row)
            for row in conn.execute(
                "SELECT * FROM destination_history ORDER BY target, version"
            ).fetchall()
        ]
        acceptances = [
            store._acceptance_public(row)
            for row in conn.execute("SELECT * FROM acceptances ORDER BY created_at, id").fetchall()
        ]
        events = [
            store._event_public(row)
            for row in conn.execute("SELECT * FROM events ORDER BY created_at, id").fetchall()
        ]
        return {
            "links": links,
            "goals": goals,
            "requests": requests,
            "assignments": assignments,
            "invocations": invocations,
            "artifacts": artifacts,
            "receipts": receipts,
            "usage": usage,
            "evidence": evidence,
            "effects": effects,
            "destinations": destinations,
            "destination_history": destination_history,
            "acceptances": acceptances,
            "events": events,
        }

    def export(self) -> dict[str, Any]:
        with self._read_snapshot():
            rows = self._export_rows(self)
            return {
                "format": _EXPORT_FORMAT,
                "version": _EXPORT_VERSION,
                "exported_at": self._now(),
                "records": rows,
            }

    @staticmethod
    def _record_list(records: dict[str, Any], name: str) -> list[dict[str, Any]]:
        value = records.get(name, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"export records.{name} must be a list of objects")
        return value

    @classmethod
    def import_data(cls, root: str | os.PathLike[str], data: dict[str, Any]) -> "Store":
        if not isinstance(data, dict):
            raise ValueError("import data must be an object")
        if data.get("format") != _EXPORT_FORMAT or data.get("version") != _EXPORT_VERSION:
            raise ValueError("unsupported export format")
        records = data.get("records")
        if not isinstance(records, dict):
            raise ValueError("export records must be an object")
        names = (
            "goals",
            "requests",
            "links",
            "assignments",
            "invocations",
            "artifacts",
            "receipts",
            "usage",
            "evidence",
            "effects",
            "destinations",
            "destination_history",
            "acceptances",
            "events",
        )
        lists = {name: cls._record_list(records, name) for name in names}
        store = cls(root)
        try:
            with store._transaction() as conn:
                total = sum(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "goals",
                        "goal_links",
                        "requests",
                        "assignments",
                        "invocations",
                        "artifacts",
                        "receipts",
                        "usage_records",
                        "evidence",
                        "effects",
                        "destinations",
                        "destination_history",
                        "acceptances",
                        "events",
                    )
                )
                if total:
                    raise ValueError("import destination must be empty")

                for goal in lists["goals"]:
                    goal_id = cls._text(goal.get("id"), "goal id", allow_empty=False)
                    original = goal.get(
                        "original_request",
                        {
                            "outcome": goal.get("outcome"),
                            "parent_id": goal.get("parent_id"),
                            "kind": goal.get("kind"),
                            "criteria": goal.get("criteria", ""),
                            "constraints": goal.get("constraints", ""),
                        },
                    )
                    original_json = cls._json_dump(original, "original request")
                    parent_id = goal.get("parent_id")
                    if parent_id is not None:
                        cls._text(parent_id, "parent_id", allow_empty=False)
                    for field in ("outcome", "kind", "criteria", "constraints"):
                        cls._text(goal.get(field, ""), field, allow_empty=field not in {"outcome", "kind"})
                    revision = cls._integer(goal.get("revision"), "revision", minimum=1)
                    input_version = cls._integer(goal.get("input_version"), "input_version", minimum=0)
                    authority_version = cls._integer(
                        goal.get("authority_version"), "authority_version", minimum=1
                    )
                    status = cls._text(goal.get("status"), "status", allow_empty=False)
                    created_at = cls._text(goal.get("created_at"), "created_at", allow_empty=False)
                    updated_at = cls._text(goal.get("updated_at"), "updated_at", allow_empty=False)
                    conn.execute(
                        """INSERT INTO goals(
                            id, outcome, parent_id, kind, criteria, constraints_text,
                            original_request_json, revision, input_version, authority_version,
                            status, effects_allowed, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                        (
                            goal_id,
                            goal["outcome"],
                            None,
                            goal["kind"],
                            goal.get("criteria", ""),
                            goal.get("constraints", ""),
                            original_json,
                            revision,
                            input_version,
                            authority_version,
                            status,
                            created_at,
                            updated_at,
                        ),
                    )
                for goal in lists["goals"]:
                    parent_id = goal.get("parent_id")
                    if parent_id is not None:
                        if conn.execute("SELECT 1 FROM goals WHERE id = ?", (parent_id,)).fetchone() is None:
                            raise KeyError(f"unknown imported parent goal: {parent_id}")
                        conn.execute(
                            "UPDATE goals SET parent_id = ? WHERE id = ?",
                            (parent_id, goal["id"]),
                        )
                for goal in lists["goals"]:
                    cursor = goal["id"]
                    seen: set[str] = set()
                    while cursor is not None:
                        if cursor in seen:
                            raise ValueError("imported goal hierarchy contains a cycle")
                        seen.add(cursor)
                        row = conn.execute(
                            "SELECT parent_id FROM goals WHERE id = ?", (cursor,)
                        ).fetchone()
                        cursor = row["parent_id"] if row is not None else None


                for link in lists["links"]:
                    link_id = cls._text(link.get("id"), "link id", allow_empty=False)
                    link_goal_id = cls._text(link.get("goal_id"), "link goal_id", allow_empty=False)
                    related_goal_id = cls._text(
                        link.get("related_goal_id"), "link related_goal_id", allow_empty=False
                    )
                    relationship = cls._text(link.get("relationship"), "link relationship", allow_empty=False)
                    if link_goal_id == related_goal_id or relationship not in {
                        "prerequisite",
                        "repair",
                        "replacement",
                    }:
                        raise ValueError("invalid imported goal link")
                    if conn.execute("SELECT 1 FROM goals WHERE id = ?", (link_goal_id,)).fetchone() is None:
                        raise KeyError(f"unknown imported link goal: {link_goal_id}")
                    if conn.execute("SELECT 1 FROM goals WHERE id = ?", (related_goal_id,)).fetchone() is None:
                        raise KeyError(f"unknown imported related goal: {related_goal_id}")
                    conn.execute(
                        """INSERT INTO goal_links(
                            id, goal_id, related_goal_id, relationship, created_at
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            link_id,
                            link_goal_id,
                            related_goal_id,
                            relationship,
                            cls._text(link.get("created_at"), "link created_at", allow_empty=False),
                        ),
                    )

                for request in lists["requests"]:
                    request_id = cls._text(request.get("id"), "request id", allow_empty=False)
                    goal_id = cls._text(request.get("goal_id"), "request goal_id", allow_empty=False)
                    goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
                    if goal is None:
                        raise KeyError(f"unknown imported request goal: {goal_id}")
                    control = request.get("control")
                    if control not in _ALLOWED_CONTROLS:
                        raise ValueError("invalid imported request control")
                    input_version = cls._integer(request.get("input_version"), "request input_version", minimum=0)
                    authority_version = cls._integer(
                        request.get("authority_version"), "request authority_version", minimum=1
                    )
                    if input_version > goal["input_version"] or authority_version > goal["authority_version"]:
                        raise ValueError("imported request snapshot exceeds its goal")
                    conn.execute(
                        """INSERT INTO requests(
                            id, goal_id, text, control, source, input_version,
                            authority_version, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            request_id,
                            goal_id,
                            cls._text(request.get("text"), "request text"),
                            control,
                            cls._text(request.get("source"), "request source", allow_empty=False),
                            input_version,
                            authority_version,
                            cls._text(request.get("created_at"), "request created_at", allow_empty=False),
                        ),
                    )

                for assignment in lists["assignments"]:
                    assignment_id = cls._text(assignment.get("id"), "assignment id", allow_empty=False)
                    goal_id = cls._text(assignment.get("goal_id"), "assignment goal_id", allow_empty=False)
                    goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
                    if goal is None:
                        raise KeyError(f"unknown imported assignment goal: {goal_id}")
                    revision = cls._integer(assignment.get("revision"), "assignment revision", minimum=1)
                    input_version = cls._integer(assignment.get("input_version"), "assignment input_version", minimum=0)
                    authority_version = cls._integer(
                        assignment.get("authority_version"), "assignment authority_version", minimum=1
                    )
                    status = cls._text(assignment.get("status"), "assignment status", allow_empty=False)
                    if status not in {"active", "fenced"}:
                        raise ValueError("invalid imported assignment status")
                    if (
                        revision > goal["revision"]
                        or input_version > goal["input_version"]
                        or authority_version > goal["authority_version"]
                    ):
                        raise ValueError("imported assignment snapshot exceeds its goal")
                    conn.execute(
                        """INSERT INTO assignments(
                            id, goal_id, revision, input_version, authority_version, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            assignment_id,
                            goal_id,
                            revision,
                            input_version,
                            authority_version,
                            "fenced",
                            cls._text(assignment.get("created_at"), "assignment created_at", allow_empty=False),
                        ),
                    )

                for invocation in lists["invocations"]:
                    invocation_id = cls._text(invocation.get("id"), "invocation id", allow_empty=False)
                    goal_id = cls._text(invocation.get("goal_id"), "invocation goal_id", allow_empty=False)
                    assignment_id = cls._text(
                        invocation.get("assignment_id"), "invocation assignment_id", allow_empty=False
                    )
                    assignment = conn.execute(
                        "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
                    ).fetchone()
                    if assignment is None or assignment["goal_id"] != goal_id:
                        raise ValueError("imported invocation assignment is not bound to its goal")
                    input_version = cls._integer(
                        invocation.get("input_version"), "invocation input_version", minimum=0
                    )
                    revision = cls._integer(invocation.get("revision"), "invocation revision", minimum=1)
                    authority_version = cls._integer(
                        invocation.get("authority_version"), "invocation authority_version", minimum=1
                    )
                    if (
                        input_version != assignment["input_version"]
                        or revision != assignment["revision"]
                        or authority_version != assignment["authority_version"]
                    ):
                        raise ValueError("imported invocation snapshot does not match its assignment")
                    status = cls._text(invocation.get("status"), "invocation status", allow_empty=False)
                    if status != "running" and status not in _ALLOWED_FINISH_STATUSES:
                        raise ValueError("invalid imported invocation status")
                    context_id = invocation.get("context_artifact_id")
                    if context_id is not None:
                        cls._text(context_id, "invocation context_artifact_id", allow_empty=False)
                    conn.execute(
                        """INSERT INTO invocations(
                            id, goal_id, assignment_id, input_version, revision,
                            authority_version, context_artifact_id, status, result,
                            created_at, finished_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            invocation_id,
                            goal_id,
                            assignment_id,
                            input_version,
                            revision,
                            authority_version,
                            context_id,
                            status,
                            cls._text(invocation.get("result"), "invocation result"),
                            cls._text(invocation.get("created_at"), "invocation created_at", allow_empty=False),
                            invocation.get("finished_at"),
                        ),
                    )

                imported_artifact_ids: set[str] = set()
                for artifact in lists["artifacts"]:
                    artifact_id = cls._text(artifact.get("id"), "artifact id", allow_empty=False)
                    goal_id = cls._text(artifact.get("goal_id"), "artifact goal_id", allow_empty=False)
                    invocation_id = cls._text(artifact.get("invocation_id"), "artifact invocation_id", allow_empty=False)
                    kind = cls._text(artifact.get("kind"), "artifact kind", allow_empty=False)
                    name = cls._text(artifact.get("name"), "artifact name")
                    content = cls._text(artifact.get("content"), "artifact content")
                    inputs = artifact.get("inputs", [])
                    if not isinstance(inputs, list) or any(not isinstance(item, str) for item in inputs):
                        raise ValueError("artifact inputs must be a list of strings")
                    limitations = cls._text(artifact.get("limitations"), "artifact limitations")
                    trust = cls._text(artifact.get("trust"), "artifact trust", allow_empty=False)
                    snapshot = conn.execute(
                        """SELECT revision, input_version, authority_version
                        FROM invocations WHERE id = ? AND goal_id = ?""",
                        (invocation_id, goal_id),
                    ).fetchone()
                    if snapshot is None:
                        raise KeyError(f"unknown artifact invocation: {invocation_id}")
                    revision = cls._integer(
                        artifact.get("revision", snapshot["revision"]), "artifact revision", minimum=1
                    )
                    input_version = cls._integer(
                        artifact.get("input_version", snapshot["input_version"]),
                        "artifact input_version",
                        minimum=0,
                    )
                    authority_version = cls._integer(
                        artifact.get("authority_version", snapshot["authority_version"]),
                        "artifact authority_version",
                        minimum=1,
                    )
                    if (
                        revision != snapshot["revision"]
                        or input_version != snapshot["input_version"]
                        or authority_version != snapshot["authority_version"]
                    ):
                        raise ValueError("artifact applicability does not match its invocation")
                    expected_id = cls._artifact_id(
                        goal_id, invocation_id, kind, name, content, inputs, limitations, trust
                    )
                    if artifact_id != expected_id:
                        raise ValueError(f"artifact hash mismatch: {artifact_id}")
                    expected_content_hash = cls._content_hash(content)
                    if artifact.get("content_hash", expected_content_hash) != expected_content_hash:
                        raise ValueError(f"artifact content hash mismatch: {artifact_id}")
                    conn.execute(
                        """INSERT INTO artifacts(
                            id, goal_id, invocation_id, kind, name, content, content_hash,
                            revision, input_version, authority_version, inputs_json,
                            limitations, trust, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            artifact_id,
                            goal_id,
                            invocation_id,
                            kind,
                            name,
                            content,
                            expected_content_hash,
                            revision,
                            input_version,
                            authority_version,
                            cls._json_dump(inputs, "artifact inputs"),
                            limitations,
                            trust,
                            cls._text(artifact.get("created_at"), "artifact created_at", allow_empty=False),
                        ),
                    )
                    imported_artifact_ids.add(artifact_id)
                for artifact in lists["artifacts"]:
                    for input_id in artifact.get("inputs", []):
                        if input_id not in imported_artifact_ids:
                            raise KeyError(f"unknown imported input artifact: {input_id}")

                for invocation in lists["invocations"]:
                    context_id = invocation.get("context_artifact_id")
                    if context_id is not None:
                        context = conn.execute(
                            "SELECT goal_id, invocation_id FROM artifacts WHERE id = ?", (context_id,)
                        ).fetchone()
                        if context is None or context["goal_id"] != invocation["goal_id"] or context["invocation_id"] != invocation["id"]:
                            raise ValueError("invalid invocation context artifact")
                    conn.execute(
                        "UPDATE invocations SET context_artifact_id = ? WHERE id = ?",
                        (context_id, invocation["id"]),
                    )

                for receipt in lists["receipts"]:
                    receipt_id = cls._text(receipt.get("id"), "receipt id", allow_empty=False)
                    invocation_id = cls._text(receipt.get("invocation_id"), "receipt invocation_id", allow_empty=False)
                    if conn.execute("SELECT 1 FROM invocations WHERE id = ?", (invocation_id,)).fetchone() is None:
                        raise KeyError(f"unknown imported receipt invocation: {invocation_id}")
                    conn.execute(
                        """INSERT INTO receipts(
                            id, invocation_id, tool, parameters_json, result_json, note, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            receipt_id,
                            invocation_id,
                            cls._text(receipt.get("tool"), "receipt tool", allow_empty=False),
                            cls._json_dump(receipt.get("parameters"), "receipt parameters"),
                            cls._json_dump(receipt.get("result"), "receipt result"),
                            cls._text(receipt.get("note"), "receipt note"),
                            cls._text(receipt.get("created_at"), "receipt created_at", allow_empty=False),
                        ),
                    )

                for usage in lists["usage"]:
                    invocation_id = cls._text(usage.get("invocation_id"), "usage invocation_id", allow_empty=False)
                    if conn.execute("SELECT 1 FROM invocations WHERE id = ?", (invocation_id,)).fetchone() is None:
                        raise KeyError(f"unknown imported usage invocation: {invocation_id}")
                    raw = usage.get("raw")
                    normalized = usage.get("normalized")
                    if not isinstance(normalized, dict):
                        raise ValueError("usage normalized value must be an object")
                    conn.execute(
                        """INSERT INTO usage_records(
                            invocation_id, raw_json, normalized_json, created_at
                        ) VALUES (?, ?, ?, ?)""",
                        (
                            invocation_id,
                            cls._json_dump(raw, "usage raw"),
                            cls._json_dump(normalized, "usage normalized"),
                            cls._text(usage.get("created_at"), "usage created_at", allow_empty=False),
                        ),
                    )

                for evidence in lists["evidence"]:
                    evidence_id = cls._text(evidence.get("id"), "evidence id", allow_empty=False)
                    invocation_id = cls._text(evidence.get("invocation_id"), "evidence invocation_id", allow_empty=False)
                    artifact_id = cls._text(evidence.get("artifact_id"), "evidence artifact_id", allow_empty=False)
                    invocation = conn.execute(
                        "SELECT goal_id FROM invocations WHERE id = ?", (invocation_id,)
                    ).fetchone()
                    artifact = conn.execute(
                        "SELECT goal_id, invocation_id FROM artifacts WHERE id = ?", (artifact_id,)
                    ).fetchone()
                    if (
                        invocation is None
                        or artifact is None
                        or artifact["goal_id"] != invocation["goal_id"]
                        or artifact["invocation_id"] != invocation_id
                    ):
                        raise ValueError("imported evidence is not bound to its invocation artifact")
                    passed = evidence.get("passed")
                    trusted = evidence.get("trusted")
                    if not isinstance(passed, bool) or not isinstance(trusted, bool):
                        raise ValueError("imported evidence booleans are invalid")
                    conn.execute(
                        """INSERT INTO evidence(
                            id, invocation_id, artifact_id, check_name, passed,
                            details_json, trusted, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            evidence_id,
                            invocation_id,
                            artifact_id,
                            cls._text(evidence.get("check"), "evidence check", allow_empty=False),
                            int(passed),
                            cls._json_dump(evidence.get("details"), "evidence details"),
                            int(trusted),
                            cls._text(evidence.get("created_at"), "evidence created_at", allow_empty=False),
                        ),
                    )

                destination_rows: dict[str, dict[str, Any]] = {}
                for destination in lists["destinations"]:
                    target = cls._text(destination.get("target"), "destination target", allow_empty=False)
                    version = cls._integer(destination.get("version"), "destination version", minimum=0)
                    content = cls._text(destination.get("content"), "destination content")
                    operation_id = destination.get("operation_id")
                    if operation_id is not None:
                        operation_id = cls._text(operation_id, "destination operation_id", allow_empty=False)
                    if version == 0 and (operation_id is not None or content != ""):
                        raise ValueError("empty imported destination has an operation or content")
                    if version > 0 and operation_id is None:
                        raise ValueError("versioned imported destination has no operation")
                    row = {
                        "target": target,
                        "version": version,
                        "content": content,
                        "operation_id": operation_id,
                        "updated_at": cls._text(
                            destination.get("updated_at"), "destination updated_at", allow_empty=False
                        ),
                    }
                    if target in destination_rows:
                        raise ValueError(f"duplicate imported destination: {target}")
                    destination_rows[target] = row
                    conn.execute(
                        """INSERT INTO destinations(
                            target, version, content, operation_id, updated_at
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (target, version, content, operation_id, row["updated_at"]),
                    )

                history_rows: dict[str, dict[str, Any]] = {}
                history_versions: set[tuple[str, int]] = set()
                for history in lists["destination_history"]:
                    operation_id = cls._text(
                        history.get("operation_id"), "destination history operation_id", allow_empty=False
                    )
                    target = cls._text(history.get("target"), "destination history target", allow_empty=False)
                    version = cls._integer(
                        history.get("version"), "destination history version", minimum=1
                    )
                    content = cls._text(history.get("content"), "destination history content")
                    effect_id = cls._text(
                        history.get("effect_id"), "destination history effect_id", allow_empty=False
                    )
                    committed_at = cls._text(
                        history.get("committed_at"), "destination history committed_at", allow_empty=False
                    )
                    if operation_id in history_rows or (target, version) in history_versions:
                        raise ValueError("duplicate imported destination operation history")
                    history_rows[operation_id] = {
                        "operation_id": operation_id,
                        "target": target,
                        "version": version,
                        "content": content,
                        "effect_id": effect_id,
                        "committed_at": committed_at,
                    }
                    history_versions.add((target, version))
                for row in destination_rows.values():
                    history = history_rows.get(row["operation_id"])
                    if (
                        row["version"] > 0
                        and (
                            history is None
                            or history["target"] != row["target"]
                            or history["version"] != row["version"]
                            or history["content"] != row["content"]
                        )
                    ):
                        raise ValueError("imported destination does not match its operation history")

                claimed_effect_states: dict[str, str] = {}
                for effect in lists["effects"]:
                    effect_id = cls._text(effect.get("id"), "effect id", allow_empty=False)
                    goal_id = cls._text(effect.get("goal_id"), "effect goal_id", allow_empty=False)
                    invocation_id = cls._text(effect.get("invocation_id"), "effect invocation_id", allow_empty=False)
                    artifact_id = cls._text(effect.get("artifact_id"), "effect artifact_id", allow_empty=False)
                    target = cls._text(effect.get("target"), "effect target", allow_empty=False)
                    expected_version = cls._integer(
                        effect.get("expected_version"), "effect expected_version", minimum=0
                    )
                    evidence_id = cls._text(effect.get("evidence_id"), "effect evidence_id", allow_empty=False)
                    operation_id = cls._text(effect.get("operation_id"), "effect operation_id", allow_empty=False)
                    candidate_content = cls._text(effect.get("candidate_content"), "effect candidate_content")
                    artifact_row = conn.execute(
                        "SELECT content, invocation_id, goal_id FROM artifacts WHERE id = ?",
                        (artifact_id,),
                    ).fetchone()
                    invocation_row = conn.execute(
                        "SELECT goal_id FROM invocations WHERE id = ?", (invocation_id,)
                    ).fetchone()
                    goal_row = conn.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone()
                    if (
                        artifact_row is None
                        or invocation_row is None
                        or goal_row is None
                        or artifact_row["content"] != candidate_content
                        or artifact_row["invocation_id"] != invocation_id
                        or artifact_row["goal_id"] != goal_id
                        or invocation_row["goal_id"] != goal_id
                        or target not in destination_rows
                        or destination_rows[target]["version"] < expected_version
                    ):
                        raise ValueError("imported effect provenance is not goal-bound")
                    evidence_row = conn.execute(
                        "SELECT invocation_id, artifact_id FROM evidence WHERE id = ?", (evidence_id,)
                    ).fetchone()
                    if (
                        evidence_row is None
                        or evidence_row["invocation_id"] != invocation_id
                        or evidence_row["artifact_id"] != artifact_id
                    ):
                        raise ValueError("imported effect evidence is not bound to its candidate")
                    state = cls._text(effect.get("state"), "effect state", allow_empty=False)
                    if state not in {"prepared", "approved", "committed", "unresolved", "imported"}:
                        raise ValueError("invalid imported effect state")
                    claimed_effect_states[effect_id] = state
                    conn.execute(
                        """INSERT INTO effects(
                            id, goal_id, invocation_id, artifact_id, target, expected_version,
                            candidate_content, evidence_id, operation_id, state, created_at,
                            approved_at, committed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'imported', ?, ?, NULL)""",
                        (
                            effect_id,
                            goal_id,
                            invocation_id,
                            artifact_id,
                            target,
                            expected_version,
                            candidate_content,
                            evidence_id,
                            operation_id,
                            cls._text(effect.get("created_at"), "effect created_at", allow_empty=False),
                            effect.get("approved_at"),
                        ),
                    )
                for history in history_rows.values():
                    effect = conn.execute(
                        """SELECT id, target, expected_version, candidate_content, operation_id
                        FROM effects WHERE id = ?""",
                        (history["effect_id"],),
                    ).fetchone()
                    destination = destination_rows.get(history["target"])
                    if (
                        effect is None
                        or claimed_effect_states.get(history["effect_id"])
                        not in {"committed", "unresolved", "imported"}
                        or effect["target"] != history["target"]
                        or effect["expected_version"] + 1 != history["version"]
                        or effect["candidate_content"] != history["content"]
                        or effect["operation_id"] != history["operation_id"]
                        or destination is None
                    ):
                        raise ValueError("imported destination history is not bound to its effect")
                    conn.execute(
                        """INSERT INTO destination_history(
                            operation_id, target, version, content, effect_id, committed_at
                        ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            history["operation_id"],
                            history["target"],
                            history["version"],
                            history["content"],
                            history["effect_id"],
                            history["committed_at"],
                        ),
                    )

                for acceptance in lists["acceptances"]:
                    acceptance_id = cls._text(acceptance.get("id"), "acceptance id", allow_empty=False)
                    goal_id = cls._text(acceptance.get("goal_id"), "acceptance goal_id", allow_empty=False)
                    artifact_id = cls._text(acceptance.get("artifact_id"), "acceptance artifact_id", allow_empty=False)
                    evidence_id = cls._text(acceptance.get("evidence_id"), "acceptance evidence_id", allow_empty=False)
                    revision = cls._integer(acceptance.get("revision"), "acceptance revision", minimum=1)
                    goal = conn.execute("SELECT revision FROM goals WHERE id = ?", (goal_id,)).fetchone()
                    artifact = conn.execute(
                        "SELECT goal_id, invocation_id, revision FROM artifacts WHERE id = ?", (artifact_id,)
                    ).fetchone()
                    evidence = conn.execute(
                        "SELECT invocation_id, artifact_id, passed, trusted FROM evidence WHERE id = ?",
                        (evidence_id,),
                    ).fetchone()
                    invocation = (
                        conn.execute("SELECT goal_id, revision FROM invocations WHERE id = ?", (evidence["invocation_id"],)).fetchone()
                        if evidence is not None
                        else None
                    )
                    if (
                        goal is None
                        or artifact is None
                        or evidence is None
                        or invocation is None
                        or goal_id != artifact["goal_id"]
                        or evidence["artifact_id"] != artifact_id
                        or evidence["invocation_id"] != artifact["invocation_id"]
                        or invocation["goal_id"] != goal_id
                        or artifact["revision"] != revision
                        or invocation["revision"] != revision
                        or revision > goal["revision"]
                        or not evidence["passed"]
                        or not evidence["trusted"]
                    ):
                        raise ValueError("imported acceptance is not an exact candidate chain")
                    conn.execute(
                        """INSERT INTO acceptances(
                            id, goal_id, artifact_id, evidence_id, revision, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            acceptance_id,
                            goal_id,
                            artifact_id,
                            evidence_id,
                            revision,
                            cls._text(acceptance.get("created_at"), "acceptance created_at", allow_empty=False),
                        ),
                    )

                for event in lists["events"]:
                    event_id = cls._text(event.get("id"), "event id", allow_empty=False)
                    goal_id = cls._text(event.get("goal_id"), "event goal_id", allow_empty=False)
                    if conn.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone() is None:
                        raise KeyError(f"unknown imported event goal: {goal_id}")
                    conn.execute(
                        """INSERT INTO events(
                            id, goal_id, event_type, payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            event_id,
                            goal_id,
                            cls._text(event.get("type"), "event type", allow_empty=False),
                            cls._json_dump(event.get("payload"), "event payload"),
                            cls._text(event.get("created_at"), "event created_at", allow_empty=False),
                        ),
                    )
            return store
        except BaseException:
            store.close()
            raise
