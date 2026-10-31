"""SQLite persistence for conversations, messages, uploads, jobs and events."""

import json
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    attachments TEXT NOT NULL DEFAULT '[]',
    job_id TEXT,
    source TEXT NOT NULL DEFAULT 'api',
    connection_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_conv ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    user_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    phase TEXT,
    label TEXT,
    input TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    connection_id TEXT,
    sandbox_id TEXT,
    pending_input TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_conv ON jobs(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS sandboxes (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    conversation_id TEXT,
    provider TEXT NOT NULL,
    provider_id TEXT,
    status TEXT NOT NULL,
    live_url TEXT,
    reason TEXT,
    seconds REAL,
    est_cost_usd REAL,
    created_at TEXT NOT NULL,
    terminated_at TEXT
);
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT,
    client TEXT,
    user_agent TEXT,
    connected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    disconnected_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_connections_conv ON connections(conversation_id);
CREATE TABLE IF NOT EXISTS tutorials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
    job_id TEXT,
    recipe TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    spec TEXT NOT NULL,
    voice TEXT NOT NULL DEFAULT '{}',
    capture TEXT,
    assets TEXT NOT NULL DEFAULT '{}',
    graph TEXT,
    output TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tutorials_conv ON tutorials(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    conversation_id TEXT,
    job_id TEXT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_records (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT,
    conversation_id TEXT,
    job_id TEXT,
    category TEXT NOT NULL,
    kind TEXT NOT NULL,
    plan TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_user ON usage_records(user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_usage_session ON usage_records(session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_usage_job ON usage_records(job_id);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    levelno INTEGER NOT NULL,
    logger TEXT NOT NULL,
    message TEXT NOT NULL,
    user_id TEXT,
    session_id TEXT,
    conversation_id TEXT,
    job_id TEXT,
    fields TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_logs_user ON logs(user_id, id);
CREATE INDEX IF NOT EXISTS ix_logs_session ON logs(session_id, id);
CREATE INDEX IF NOT EXISTS ix_logs_job ON logs(job_id, id);
CREATE INDEX IF NOT EXISTS ix_logs_ts ON logs(ts);
"""

ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_input")
COUNTED_USAGE = ("reserved", "succeeded", "interrupted")  # failed/cancelled are refunded
USER_COLS = (
    "id, username, email, display_name, plan, role, auth_provider, created_at, ref_code, "
    "referred_by, bonus_videos, referral_credited"
)


def _where(filters: dict[str, Any], cols: Collection[str], ts: str, alias: str = "") -> tuple:
    parts, args = [], []
    for k in cols:
        if filters.get(k):
            parts.append(f"{alias}{k} = ?")
            args.append(filters[k])
    if filters.get("since"):
        parts.append(f"{alias}{ts} >= ?")
        args.append(filters["since"])
    if filters.get("until"):
        parts.append(f"{alias}{ts} < ?")
        args.append(filters["until"])
    return (" WHERE " + " AND ".join(parts)) if parts else "", args


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(jobs)")}
        if "pending_input" not in cols:
            self._conn.execute("ALTER TABLE jobs ADD COLUMN pending_input TEXT")
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(tutorials)")}
        if "active_job" not in cols:  # the job currently working on the tutorial
            self._conn.execute("ALTER TABLE tutorials ADD COLUMN active_job TEXT")
        self._add_columns("users", {
            "plan": "TEXT NOT NULL DEFAULT 'free'", "role": "TEXT NOT NULL DEFAULT 'user'",
            "email": "TEXT", "display_name": "TEXT",
            "auth_provider": "TEXT NOT NULL DEFAULT 'password'", "provider_uid": "TEXT",
        })  # fmt: skip
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_identity ON users(auth_provider, "
            "provider_uid) WHERE provider_uid IS NOT NULL"
        )
        self._add_columns("users", {
            "ref_code": "TEXT", "referred_by": "TEXT",
            "bonus_videos": "INTEGER NOT NULL DEFAULT 0",
            "referral_credited": "INTEGER NOT NULL DEFAULT 0",
        })  # fmt: skip
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_ref ON users(ref_code) "
            "WHERE ref_code IS NOT NULL"
        )
        self._add_columns("tutorials", {"share_slug": "TEXT", "shared_at": "TEXT"})
        self._add_columns("conversations", {"voice_id": "TEXT"})  # the user's narrator pick
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_tutorials_share ON tutorials(share_slug) "
            "WHERE share_slug IS NOT NULL"
        )
        self._add_columns("sessions", {"id": "TEXT"})
        self._conn.execute(
            "UPDATE sessions SET id = 'ses_' || lower(hex(randomblob(8))) WHERE id IS NULL"
        )
        self._add_columns("jobs", {"user_id": "TEXT", "session_id": "TEXT"})
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_jobs_user ON jobs(user_id, created_at)")
        self._lock = threading.RLock()

    def _add_columns(self, table: str, cols: dict[str, str]) -> None:
        have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def close(self) -> None:
        self._conn.close()

    def _all(self, sql: str, args: tuple | dict = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    def _exec(self, sql: str, args: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        cols = ", ".join(row)
        marks = ", ".join(f":{k}" for k in row)
        self._exec(f"INSERT INTO {table} ({cols}) VALUES ({marks})", row)

    # sandboxes
    def create_sandbox(self, job_id: str | None, cid: str | None, provider: str) -> dict[str, Any]:
        sid = new_id("sb")
        self._insert(
            "sandboxes",
            {"id": sid, "job_id": job_id, "conversation_id": cid, "provider": provider,
             "status": "starting", "created_at": now()},
        )  # fmt: skip
        return self.get_sandbox(sid)  # type: ignore[return-value]

    def get_sandbox(self, sid: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM sandboxes WHERE id = ?", (sid,))

    def update_sandbox(self, sid: str, **fields: Any) -> dict[str, Any]:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE sandboxes SET {cols} WHERE id = ?", (*fields.values(), sid))
        return self.get_sandbox(sid)  # type: ignore[return-value]

    def sandbox_usage(self, user_id: str | None, since: str) -> dict[str, float]:
        """Running sandboxes (overall and the user's) and the user's billed seconds since."""
        row = (
            self._one(
                "SELECT COUNT(*) AS active, COALESCE(SUM(CASE WHEN c.user_id = ? THEN 1 END), 0) "
                "AS mine FROM sandboxes s LEFT JOIN conversations c ON c.id = s.conversation_id "
                "WHERE s.status IN ('starting', 'running')",
                (user_id,),
            )
            or {}
        )
        used = (
            self._one(
                "SELECT COALESCE(SUM(s.seconds), 0) AS seconds FROM sandboxes s "
                "JOIN conversations c ON c.id = s.conversation_id "
                "WHERE c.user_id = ? AND s.created_at >= ?",
                (user_id, since),
            )
            or {}
        )
        return {"active": row.get("active", 0), "mine": row.get("mine", 0),
                "seconds": used.get("seconds", 0)}  # fmt: skip

    def list_sandboxes(self, active_only: bool = False, user_id: str | None = None) -> list[dict]:
        sql = "SELECT s.* FROM sandboxes s LEFT JOIN conversations c ON c.id = s.conversation_id"
        sql += " WHERE 1=1"
        args: list[Any] = []
        if active_only:
            sql += " AND s.status IN ('starting', 'running')"
        if user_id:
            sql += " AND c.user_id = ?"
            args.append(user_id)
        return self._all(sql + " ORDER BY s.created_at DESC", tuple(args))

    # users and auth sessions
    def upsert_user(
        self, username: str, password_hash: str, *, plan: str = "free", role: str = "user"
    ) -> dict[str, Any]:
        """Create or re-sync a password account (its password, plan and role)."""
        user = self.get_user_by_name(username)
        if user:
            self._exec(
                "UPDATE users SET password_hash = ?, plan = ?, role = ? WHERE id = ?",
                (password_hash, plan, role, user["id"]),
            )
        else:
            self._insert(
                "users",
                {"id": new_id("usr"), "username": username, "password_hash": password_hash,
                 "plan": plan, "role": role, "created_at": now()},
            )  # fmt: skip
        return self.get_user_by_name(username)  # type: ignore[return-value]

    def create_account(
        self, username: str, password_hash: str, *, plan: str, referred_by: str | None = None
    ) -> dict[str, Any]:
        """A new self-service password account (raises ValueError if the name is taken)."""
        with self._lock:
            if self.get_user_by_name(username):
                raise ValueError("That username is taken")
            uid = new_id("usr")
            self._insert(
                "users",
                {"id": uid, "username": username, "password_hash": password_hash,
                 "plan": plan, "role": "user", "referred_by": referred_by,
                 "created_at": now()},
            )  # fmt: skip
        return self.get_user_by_name(username)  # type: ignore[return-value]

    def ensure_ref_code(self, uid: str) -> str:
        with self._lock:
            row = self._one("SELECT ref_code FROM users WHERE id = ?", (uid,))
            if row and row["ref_code"]:
                return row["ref_code"]
            code = secrets.token_hex(4)
            self._exec("UPDATE users SET ref_code = ? WHERE id = ?", (code, uid))
            return code

    def user_by_ref(self, code: str) -> dict[str, Any] | None:
        return self._one(f"SELECT {USER_COLS} FROM users WHERE ref_code = ?", (code,))

    def credit_referral(self, uid: str, bonus: int) -> str | None:
        """Give `uid`'s referrer `bonus` videos, once; returns the referrer's id."""
        with self._lock:
            row = self._one("SELECT referred_by, referral_credited FROM users WHERE id = ?", (uid,))
            if not row or not row["referred_by"] or row["referral_credited"]:
                return None
            self._exec("UPDATE users SET referral_credited = 1 WHERE id = ?", (uid,))
            self._exec(
                "UPDATE users SET bonus_videos = bonus_videos + ? WHERE id = ?",
                (bonus, row["referred_by"]),
            )
            return row["referred_by"]

    def referral_counts(self, uid: str) -> dict[str, int]:
        row = self._one(
            "SELECT COUNT(*) AS invited, COALESCE(SUM(referral_credited), 0) AS credited "
            "FROM users WHERE referred_by = ?",
            (uid,),
        )
        return {"invited": row["invited"], "credited": row["credited"]} if row else {}

    def upsert_identity(
        self,
        provider: str,
        uid: str,
        *,
        email: str | None,
        name: str | None,
        plan: str,
        referred_by: str | None = None,
    ) -> dict[str, Any]:
        """The account for an external sign-in (e.g. Firebase Google); created on first use."""
        sql = "SELECT * FROM users WHERE auth_provider = ? AND provider_uid = ?"
        user = self._one(sql, (provider, uid))
        if user:
            self._exec(
                "UPDATE users SET email = ?, display_name = ? WHERE id = ?",
                (email, name, user["id"]),
            )
        else:
            base = email or f"{provider}:{uid}"
            username, n = base, 1
            while self.get_user_by_name(username):
                n += 1
                username = f"{base}#{n}"
            self._insert(
                "users",
                {"id": new_id("usr"), "username": username, "password_hash": "",
                 "email": email, "display_name": name, "plan": plan, "role": "user",
                 "referred_by": referred_by,
                 "auth_provider": provider, "provider_uid": uid, "created_at": now()},
            )  # fmt: skip
        return self._one(sql, (provider, uid))  # type: ignore[return-value]

    def get_user(self, uid: str) -> dict[str, Any] | None:
        return self._one(f"SELECT {USER_COLS} FROM users WHERE id = ?", (uid,))

    def list_users(self) -> list[dict[str, Any]]:
        return self._all(f"SELECT {USER_COLS} FROM users ORDER BY created_at")

    def update_user(self, uid: str, **fields: Any) -> dict[str, Any] | None:
        allowed = ("plan", "role", "email", "display_name", "bonus_videos")
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            self._exec(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), uid))
        return self.get_user(uid)

    def get_user_by_name(self, username: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM users WHERE username = ?", (username,))

    def create_session(self, token_hash: str, user_id: str) -> str:
        """Store a login; returns its public session id (logs and usage are tagged with it)."""
        sid, ts = new_id("ses"), now()
        self._insert(
            "sessions",
            {"id": sid, "token_hash": token_hash, "user_id": user_id, "created_at": ts,
             "last_seen_at": ts},
        )  # fmt: skip
        return sid

    def user_for_session(self, token_hash: str) -> dict[str, Any] | None:
        user = self._one(
            """SELECT u.id, u.username, u.email, u.display_name, u.plan, u.role,
                      u.bonus_videos, u.ref_code, s.id AS session_id
               FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?""",
            (token_hash,),
        )
        if user:
            self._exec(
                "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now(), token_hash)
            )
        return user

    def delete_session(self, token_hash: str) -> None:
        self._exec("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    # conversations
    def create_conversation(self, user_id: str, title: str = "New chat") -> dict[str, Any]:
        cid, ts = new_id("c"), now()
        self._insert(
            "conversations",
            {"id": cid, "user_id": user_id, "title": title, "created_at": ts, "updated_at": ts},
        )
        return self.get_conversation(cid)  # type: ignore[return-value]

    def get_conversation(self, cid: str, user_id: str | None = None) -> dict[str, Any] | None:
        conv = self._one("SELECT * FROM conversations WHERE id = ?", (cid,))
        if conv and user_id and conv["user_id"] != user_id:
            return None
        return conv

    def list_conversations(self, user_id: str) -> list[dict[str, Any]]:
        return self._all(
            """SELECT c.*, (SELECT COUNT(*) FROM jobs j WHERE j.conversation_id = c.id
                   AND j.status IN ('queued', 'running', 'waiting_input')) AS active_jobs
               FROM conversations c WHERE c.user_id = ? ORDER BY updated_at DESC""",
            (user_id,),
        )

    def update_conversation(
        self, cid: str, *, title: str | None = None, voice_id: str | None | bool = False
    ) -> None:
        sets, args = [], []
        if title is not None:
            sets.append("title = ?")
            args.append(title)
        if voice_id is not False:  # None clears the pick, a string sets it
            sets.append("voice_id = ?")
            args.append(voice_id)
        sets.append("updated_at = ?")
        args += [now(), cid]
        self._exec(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", tuple(args))

    def delete_conversation(self, cid: str) -> None:
        self._exec("DELETE FROM conversations WHERE id = ?", (cid,))

    # messages
    def add_message(
        self,
        cid: str,
        role: str,
        content: str,
        attachments: list[dict[str, Any]] | None = None,
        *,
        job_id: str | None = None,
        source: str = "api",
        connection_id: str | None = None,
    ) -> dict[str, Any]:
        mid = new_id("m")
        self._insert(
            "messages",
            {"id": mid, "conversation_id": cid, "role": role, "content": content,
             "attachments": json.dumps(attachments or []), "job_id": job_id, "source": source,
             "connection_id": connection_id, "created_at": now()},
        )  # fmt: skip
        self.update_conversation(cid)
        return self.get_message(mid)  # type: ignore[return-value]

    def get_message(self, mid: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM messages WHERE id = ?", (mid,))
        return _decode(row, "attachments")

    def list_messages(self, cid: str) -> list[dict[str, Any]]:
        rows = self._all(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid", (cid,)
        )
        return [_decode(r, "attachments") for r in rows]  # type: ignore[misc]

    # uploads
    def add_upload(self, upload: dict[str, Any]) -> dict[str, Any]:
        self._insert("uploads", upload)
        return upload

    def get_uploads(self, ids: list[str], user_id: str | None = None) -> list[dict[str, Any]]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self._all(f"SELECT * FROM uploads WHERE id IN ({marks})", tuple(ids))
        found = {r["id"]: r for r in rows if user_id is None or r["user_id"] == user_id}
        return [found[i] for i in dict.fromkeys(ids) if i in found]

    # tutorials: the editable state graph and asset URLs; media bytes live in storage
    _TUT_JSON = ("spec", "voice", "capture", "assets", "graph", "output")

    def create_tutorial(
        self, user_id: str, cid: str | None, job_id: str, recipe: str, title: str, spec: dict
    ) -> dict[str, Any]:
        tid, ts = new_id("t"), now()
        self._insert(
            "tutorials",
            {"id": tid, "user_id": user_id, "conversation_id": cid, "job_id": job_id,
             "recipe": recipe, "title": title, "status": "planned", "spec": json.dumps(spec),
             "created_at": ts, "updated_at": ts},
        )  # fmt: skip
        return self.get_tutorial(tid)  # type: ignore[return-value]

    def get_tutorial(self, tid: str, user_id: str | None = None) -> dict[str, Any] | None:
        sql, args = "SELECT * FROM tutorials WHERE id = ?", [tid]
        if user_id:
            sql += " AND user_id = ?"
            args.append(user_id)
        return _decode(self._one(sql, tuple(args)), *self._TUT_JSON)

    def list_tutorials(self, user_id: str, cid: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM tutorials WHERE user_id = ?", [user_id]
        if cid:
            sql += " AND conversation_id = ?"
            args.append(cid)
        rows = self._all(sql + " ORDER BY created_at DESC", tuple(args))
        return [_decode(r, *self._TUT_JSON) for r in rows]  # type: ignore[misc]

    def tutorials_to_resume(self, job_ids: Collection[str]) -> list[dict[str, Any]]:
        """Unfinished tutorials whose active job was among `job_ids` (interrupted)."""
        if not job_ids:
            return []
        marks = ", ".join("?" * len(job_ids))
        rows = self._all(
            "SELECT * FROM tutorials WHERE status IN ('planned', 'capturing', 'rendering') "
            f"AND active_job IN ({marks}) ORDER BY created_at",
            tuple(job_ids),
        )
        return [_decode(r, *self._TUT_JSON) for r in rows]  # type: ignore[misc]

    def update_tutorial(self, tid: str, **fields: Any) -> dict[str, Any]:
        for col in self._TUT_JSON:
            if col in fields and fields[col] is not None:
                fields[col] = json.dumps(fields[col])
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE tutorials SET {cols} WHERE id = ?", (*fields.values(), tid))
        return self.get_tutorial(tid)  # type: ignore[return-value]

    def share_tutorial(self, tid: str, public: bool = True) -> str | None:
        """Turn the public share page on (returns its slug) or off."""
        with self._lock:
            row = self._one("SELECT share_slug FROM tutorials WHERE id = ?", (tid,))
            if row is None:
                return None
            slug = row["share_slug"] or secrets.token_urlsafe(6).replace("_", "x").replace("-", "z")
            self._exec(
                "UPDATE tutorials SET share_slug = ?, shared_at = ? WHERE id = ?",
                (slug, now() if public else None, tid),
            )
            return slug

    def tutorial_by_slug(self, slug: str) -> dict[str, Any] | None:
        sql = "SELECT * FROM tutorials WHERE share_slug = ? AND shared_at IS NOT NULL"
        return _decode(self._one(sql, (slug,)), *self._TUT_JSON)

    def job_stats(self, since: str) -> dict[str, Any]:
        """Per job kind: counts by status and end-to-end seconds (avg, p95) of finished jobs."""
        rows = self._all(
            "SELECT kind, status, created_at, finished_at, error FROM jobs WHERE created_at >= ?",
            (since,),
        )
        out: dict[str, Any] = {}
        for r in rows:
            k = out.setdefault(r["kind"], {"statuses": {}, "_secs": [], "errors": {}})
            k["statuses"][r["status"]] = k["statuses"].get(r["status"], 0) + 1
            if r["finished_at"] and r["status"] == "succeeded":
                t0 = datetime.fromisoformat(r["created_at"])
                k["_secs"].append((datetime.fromisoformat(r["finished_at"]) - t0).total_seconds())
            if r["status"] == "failed" and r["error"]:
                e = r["error"][:120]
                k["errors"][e] = k["errors"].get(e, 0) + 1
        for k in out.values():
            secs = sorted(k.pop("_secs"))
            done = sum(k["statuses"].get(s, 0) for s in ("succeeded", "failed"))
            k["success_rate"] = round(k["statuses"].get("succeeded", 0) / done, 3) if done else None
            k["avg_seconds"] = round(sum(secs) / len(secs), 1) if secs else None
            k["p95_seconds"] = (
                round(secs[min(len(secs) - 1, int(0.95 * len(secs)))], 1) if secs else None
            )
            k["errors"] = sorted(k["errors"].items(), key=lambda x: -x[1])[:5]
        return out

    def delete_tutorial(self, tid: str) -> None:
        self._exec("DELETE FROM tutorials WHERE id = ?", (tid,))

    # jobs
    def create_job(
        self,
        cid: str | None,
        kind: str,
        payload: dict[str, Any],
        connection_id: str | None = None,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        jid = new_id("j")
        self._insert(
            "jobs",
            {"id": jid, "conversation_id": cid, "kind": kind, "status": "queued",
             "input": json.dumps(payload), "connection_id": connection_id, "created_at": now(),
             "user_id": user_id, "session_id": session_id},
        )  # fmt: skip
        return self.get_job(jid)  # type: ignore[return-value]

    def get_job(self, jid: str) -> dict[str, Any] | None:
        return _decode(
            self._one("SELECT * FROM jobs WHERE id = ?", (jid,)), "input", "result", "pending_input"
        )

    def list_jobs(
        self,
        cid: str | None = None,
        active_only: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT j.* FROM jobs j JOIN conversations c ON c.id = j.conversation_id", []
        sql += " WHERE 1=1"
        if cid:
            sql += " AND j.conversation_id = ?"
            args.append(cid)
        if user_id:
            sql += " AND c.user_id = ?"
            args.append(user_id)
        if active_only:
            sql += " AND j.status IN ('queued', 'running', 'waiting_input')"
        rows = self._all(sql + " ORDER BY j.created_at DESC", tuple(args))
        return [_decode(r, "input", "result", "pending_input") for r in rows]  # type: ignore[misc]

    def update_job(self, jid: str, **fields: Any) -> dict[str, Any]:
        for col in ("result", "pending_input"):
            if col in fields and fields[col] is not None:
                fields[col] = json.dumps(fields[col])
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), jid))
        return self.get_job(jid)  # type: ignore[return-value]

    def interrupt_stale_jobs(self) -> list[dict[str, Any]]:
        stale = self._all(
            "SELECT id FROM jobs WHERE status IN ('queued', 'running', 'waiting_input')"
        )
        for row in stale:
            self.finish_usage(row["id"], "interrupted")
            self.update_job(
                row["id"], status="interrupted", error="Server restarted", finished_at=now()
            )
        return [self.get_job(r["id"]) for r in stale]  # type: ignore[misc]

    # websocket connections
    def open_connection(self, user_id: str, client: str, user_agent: str | None) -> dict[str, Any]:
        ts = now()
        cid = new_id("ws")
        self._insert(
            "connections",
            {"id": cid, "user_id": user_id, "client": client, "user_agent": user_agent,
             "connected_at": ts, "last_seen_at": ts},
        )  # fmt: skip
        return self.get_connection(cid)  # type: ignore[return-value]

    def get_connection(self, conn_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM connections WHERE id = ?", (conn_id,))

    def touch_connection(self, conn_id: str, conversation_id: str | None | bool = False) -> None:
        """Update last_seen; pass a conversation id (or None to clear) to record what is open."""
        if conversation_id is False:
            self._exec("UPDATE connections SET last_seen_at = ? WHERE id = ?", (now(), conn_id))
        else:
            self._exec(
                "UPDATE connections SET last_seen_at = ?, conversation_id = ? WHERE id = ?",
                (now(), conversation_id, conn_id),
            )

    def close_connection(self, conn_id: str) -> None:
        self._exec(
            "UPDATE connections SET disconnected_at = ?, last_seen_at = ? WHERE id = ?",
            (now(), now(), conn_id),
        )

    def close_all_connections(self) -> None:
        self._exec(
            "UPDATE connections SET disconnected_at = ? WHERE disconnected_at IS NULL", (now(),)
        )

    def list_connections(
        self, user_id: str, conversation_id: str | None = None, active_only: bool = True
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM connections WHERE user_id = ?", [user_id]
        if conversation_id:
            sql += " AND conversation_id = ?"
            args.append(conversation_id)
        if active_only:
            sql += " AND disconnected_at IS NULL"
        return self._all(sql + " ORDER BY connected_at DESC LIMIT 200", tuple(args))

    # events
    def add_event(
        self, type_: str, payload: dict[str, Any], cid: str | None, jid: str | None
    ) -> dict[str, Any]:
        ts = now()
        owner = self._one("SELECT user_id FROM conversations WHERE id = ?", (cid,)) if cid else None
        user_id = owner["user_id"] if owner else payload.get("user_id")
        cur = self._exec(
            "INSERT INTO events (user_id, conversation_id, job_id, type, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, cid, jid, type_, json.dumps(payload), ts),
        )
        return {
            "seq": cur.lastrowid,
            "type": type_,
            "user_id": user_id,
            "conversation_id": cid,
            "job_id": jid,
            "payload": payload,
            "created_at": ts,
        }

    def events_after(
        self,
        seq: int,
        user_id: str,
        cid: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM events WHERE seq > ? AND user_id = ?", [seq, user_id]
        if cid:
            sql += " AND conversation_id = ?"
            args.append(cid)
        rows = self._all(sql + " ORDER BY seq LIMIT ?", (*args, limit))
        return [_decode(r, "payload") for r in rows]  # type: ignore[misc]

    def last_event_seq(self) -> int:
        row = self._one("SELECT COALESCE(MAX(seq), 0) AS seq FROM events")
        return int(row["seq"]) if row else 0

    # usage: one record per metered video job, counted per calendar month
    def usage_counts(self, user_id: str, since: str) -> dict[str, int]:
        marks = ", ".join("?" * len(COUNTED_USAGE))
        rows = self._all(
            f"SELECT category, COUNT(*) AS n FROM usage_records WHERE user_id = ? "
            f"AND created_at >= ? AND status IN ({marks}) GROUP BY category",
            (user_id, since, *COUNTED_USAGE),
        )
        return {r["category"]: r["n"] for r in rows}

    def reserve_usage(
        self, row: dict[str, Any], since: str, blocked: Any
    ) -> tuple[dict[str, int], str | None]:
        """Atomically: count the user's usage since `since`, ask `blocked(counts)` which
        limit (if any) one more would break, and insert `row` when none is."""
        with self._lock:
            if self._one(
                "SELECT id FROM usage_records WHERE job_id = ? AND category = ?",
                (row["job_id"], row["category"]),
            ):
                return {}, None
            counts = self.usage_counts(row["user_id"], since)
            reason = blocked(counts)
            if reason is None:
                self._insert("usage_records", row)
            return counts, reason

    def update_usage(self, job_id: str, **fields: Any) -> None:
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            self._exec(
                f"UPDATE usage_records SET {cols} WHERE job_id = ?", (*fields.values(), job_id)
            )

    def finish_usage(self, job_id: str, status: str, detail: dict[str, Any] | None = None) -> int:
        return self._exec(
            "UPDATE usage_records SET status = ?, finished_at = ?, detail = COALESCE(?, detail) "
            "WHERE job_id = ? AND status = 'reserved'",
            (status, now(), json.dumps(detail) if detail else None, job_id),
        ).rowcount

    def list_usage(self, filters: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
        cols = ("user_id", "session_id", "conversation_id", "job_id", "category", "kind",
                "status", "plan")  # fmt: skip
        where, args = _where(filters, cols, "created_at", "r.")
        rows = self._all(
            f"SELECT r.*, u.username FROM usage_records r LEFT JOIN users u ON u.id = r.user_id"
            f"{where} ORDER BY r.created_at DESC LIMIT ?",
            (*args, max(1, min(limit, 1000))),
        )
        return [_decode(r, "detail") for r in rows]  # type: ignore[misc]

    def usage_totals(self, filters: dict[str, Any]) -> dict[str, dict[str, int]]:
        cols = ("user_id", "session_id", "conversation_id", "job_id", "category", "kind",
                "status", "plan")  # fmt: skip
        where, args = _where(filters, cols, "created_at")
        rows = self._all(
            f"SELECT category, status, COUNT(*) AS n FROM usage_records{where} "
            "GROUP BY category, status",
            tuple(args),
        )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["category"], {})[r["status"]] = r["n"]
        return out

    # central logs
    def add_log(self, row: dict[str, Any]) -> None:
        self._insert("logs", row)

    def list_logs(self, filters: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
        where, args = _where(filters, ("user_id", "session_id", "conversation_id", "job_id"), "ts")
        extra = []
        if filters.get("level"):
            extra.append("levelno >= ?")
            args.append(_LEVELS.get(str(filters["level"]).upper(), 0))
        if filters.get("logger"):
            extra.append("logger LIKE ?")
            args.append(f"{filters['logger']}%")
        if filters.get("q"):
            extra.append("(message LIKE ? OR fields LIKE ?)")
            args += [f"%{filters['q']}%"] * 2
        if filters.get("before"):
            extra.append("id < ?")
            args.append(int(filters["before"]))
        if extra:
            where = (where + " AND " if where else " WHERE ") + " AND ".join(extra)
        rows = self._all(
            f"SELECT * FROM logs{where} ORDER BY id DESC LIMIT ?",
            (*args, max(1, min(limit, 1000))),
        )
        return [_decode(r, "fields") for r in rows]  # type: ignore[misc]

    def prune_logs(self, before: str) -> int:
        return self._exec("DELETE FROM logs WHERE ts < ?", (before,)).rowcount


def _decode(row: dict[str, Any] | None, *cols: str) -> dict[str, Any] | None:
    if row is None:
        return None
    for col in cols:
        if row.get(col) is not None:
            row[col] = json.loads(row[col])
    return row


_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
