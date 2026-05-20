from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from zedclaw_constants import get_zedclaw_home


def agent_home() -> Path:
    path = get_zedclaw_home() / "oss_pr_agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return agent_home() / "state.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            issue_url TEXT,
            issue_number INTEGER,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            workspace TEXT,
            branch TEXT,
            pr_url TEXT,
            pr_number INTEGER,
            fix_attempts INTEGER NOT NULL DEFAULT 0,
            max_fix_attempts INTEGER NOT NULL DEFAULT 5,
            score REAL NOT NULL DEFAULT 0,
            failure_reason TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS email_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_message_id TEXT UNIQUE,
            received_at TEXT,
            sender TEXT,
            subject TEXT NOT NULL,
            repo TEXT,
            pr_number INTEGER,
            github_url TEXT,
            model_intent TEXT,
            urgency TEXT,
            summary TEXT,
            raw_snippet TEXT,
            action_taken TEXT,
            processed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS successful_merged_pr (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo TEXT NOT NULL,
            pr_number INTEGER NOT NULL,
            pr_url TEXT,
            merged_at TEXT,
            task_id TEXT,
            source_email_id INTEGER,
            summary TEXT,
            created_at REAL NOT NULL,
            UNIQUE(repo, pr_number)
        );
        CREATE TABLE IF NOT EXISTS human_review_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT,
            repo TEXT,
            pr_number INTEGER,
            subject TEXT,
            reason TEXT NOT NULL,
            summary TEXT,
            raw_snippet TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at REAL NOT NULL,
            finished_at REAL,
            log_path TEXT,
            result_path TEXT,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS preferred_plans (
            id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            repo_url TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT,
            issue_url TEXT,
            issue_number INTEGER,
            score REAL NOT NULL DEFAULT 0,
            plan_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS repo_cooldowns (
            repo TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            task_id TEXT,
            pr_url TEXT,
            started_at REAL NOT NULL,
            until_at REAL NOT NULL
        );
        """
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


def upsert_task(conn: sqlite3.Connection, task: dict[str, Any]) -> None:
    now = time.time()
    existing = conn.execute("SELECT id, created_at FROM tasks WHERE id=?", (task["id"],)).fetchone()
    data = {
        "repo": task.get("repo", ""),
        "issue_url": task.get("issue_url"),
        "issue_number": task.get("issue_number"),
        "title": task.get("title", ""),
        "status": task.get("status", "PENDING"),
        "workspace": task.get("workspace"),
        "branch": task.get("branch"),
        "pr_url": task.get("pr_url"),
        "pr_number": task.get("pr_number"),
        "fix_attempts": int(task.get("fix_attempts", 0) or 0),
        "max_fix_attempts": int(task.get("max_fix_attempts", 5) or 5),
        "score": float(task.get("score", 0) or 0),
        "failure_reason": task.get("failure_reason"),
        "updated_at": now,
    }
    if existing:
        conn.execute(
            """
            UPDATE tasks SET repo=:repo, issue_url=:issue_url, issue_number=:issue_number,
                title=:title, status=:status, workspace=:workspace, branch=:branch,
                pr_url=:pr_url, pr_number=:pr_number, fix_attempts=:fix_attempts,
                max_fix_attempts=:max_fix_attempts, score=:score,
                failure_reason=:failure_reason, updated_at=:updated_at
            WHERE id=:id
            """,
            {"id": task["id"], **data},
        )
    else:
        conn.execute(
            """
            INSERT INTO tasks(id, repo, issue_url, issue_number, title, status,
                workspace, branch, pr_url, pr_number, fix_attempts, max_fix_attempts,
                score, failure_reason, created_at, updated_at)
            VALUES (:id, :repo, :issue_url, :issue_number, :title, :status,
                :workspace, :branch, :pr_url, :pr_number, :fix_attempts,
                :max_fix_attempts, :score, :failure_reason, :created_at, :updated_at)
            """,
            {"id": task["id"], "created_at": now, **data},
        )
    conn.commit()


def update_task(conn: sqlite3.Connection, task_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    assignments = ", ".join(f"{key}=:{key}" for key in fields)
    conn.execute(f"UPDATE tasks SET {assignments} WHERE id=:id", {"id": task_id, **fields})
    conn.commit()


def upsert_preferred_plan(conn: sqlite3.Connection, plan: dict[str, Any]) -> None:
    now = time.time()
    existing = conn.execute("SELECT id, created_at FROM preferred_plans WHERE id=?", (plan["id"],)).fetchone()
    data = {
        "repo": plan.get("repo", ""),
        "repo_url": plan.get("repo_url", ""),
        "status": plan.get("status", "pending_approval"),
        "title": plan.get("title"),
        "issue_url": plan.get("issue_url"),
        "issue_number": plan.get("issue_number"),
        "score": float(plan.get("score", 0) or 0),
        "plan_json": json.dumps(plan.get("plan_json") or {}, ensure_ascii=False),
        "updated_at": now,
    }
    if existing:
        conn.execute(
            """
            UPDATE preferred_plans SET repo=:repo, repo_url=:repo_url, status=:status,
                title=:title, issue_url=:issue_url, issue_number=:issue_number,
                score=:score, plan_json=:plan_json, updated_at=:updated_at
            WHERE id=:id
            """,
            {"id": plan["id"], **data},
        )
    else:
        conn.execute(
            """
            INSERT INTO preferred_plans(id, repo, repo_url, status, title, issue_url,
                issue_number, score, plan_json, created_at, updated_at)
            VALUES (:id, :repo, :repo_url, :status, :title, :issue_url,
                :issue_number, :score, :plan_json, :created_at, :updated_at)
            """,
            {"id": plan["id"], "created_at": now, **data},
        )
    conn.commit()


def update_preferred_plan(conn: sqlite3.Connection, plan_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    assignments = ", ".join(f"{key}=:{key}" for key in fields)
    conn.execute(f"UPDATE preferred_plans SET {assignments} WHERE id=:id", {"id": plan_id, **fields})
    conn.commit()


def list_preferred_plans(conn: sqlite3.Connection, statuses: Iterable[str]) -> list[dict[str, Any]]:
    vals = list(statuses)
    if not vals:
        return []
    marks = ",".join("?" for _ in vals)
    rows = conn.execute(
        f"SELECT * FROM preferred_plans WHERE status IN ({marks}) ORDER BY updated_at",
        vals,
    ).fetchall()
    plans: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["plan_json"] = json.loads(item.get("plan_json") or "{}")
        except Exception:
            item["plan_json"] = {}
        plans.append(item)
    return plans


def add_repo_cooldown(
    conn: sqlite3.Connection,
    *,
    repo: str,
    reason: str,
    until_at: float,
    task_id: str = "",
    pr_url: str = "",
) -> None:
    repo = str(repo or "").strip()
    if not repo:
        return
    conn.execute(
        """
        INSERT INTO repo_cooldowns(repo, reason, task_id, pr_url, started_at, until_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo) DO UPDATE SET
            reason=excluded.reason,
            task_id=excluded.task_id,
            pr_url=excluded.pr_url,
            started_at=excluded.started_at,
            until_at=MAX(repo_cooldowns.until_at, excluded.until_at)
        """,
        (repo, reason, task_id, pr_url, time.time(), float(until_at)),
    )
    conn.commit()


def repo_cooldown(conn: sqlite3.Connection, repo: str, *, now: float | None = None) -> dict[str, Any] | None:
    repo = str(repo or "").strip()
    if not repo:
        return None
    now_ts = time.time() if now is None else float(now)
    row = conn.execute("SELECT * FROM repo_cooldowns WHERE repo=?", (repo,)).fetchone()
    if not row:
        return None
    item = dict(row)
    if float(item.get("until_at") or 0) <= now_ts:
        conn.execute("DELETE FROM repo_cooldowns WHERE repo=?", (repo,))
        conn.commit()
        return None
    return item


def repo_in_cooldown(conn: sqlite3.Connection, repo: str, *, now: float | None = None) -> bool:
    return repo_cooldown(conn, repo, now=now) is not None


def list_tasks(conn: sqlite3.Connection, statuses: Iterable[str]) -> list[dict[str, Any]]:
    vals = list(statuses)
    if not vals:
        return []
    marks = ",".join("?" for _ in vals)
    rows = conn.execute(f"SELECT * FROM tasks WHERE status IN ({marks}) ORDER BY updated_at", vals).fetchall()
    return [dict(row) for row in rows]


def add_human_review_item(conn: sqlite3.Connection, **item: Any) -> None:
    source = item.get("source", "runtime")
    source_id = item.get("source_id")
    repo = item.get("repo")
    pr_number = item.get("pr_number")
    reason = item.get("reason", "")
    existing = conn.execute(
        """
        SELECT id FROM human_review_items
        WHERE status='open'
          AND source=?
          AND COALESCE(source_id, '')=COALESCE(?, '')
          AND COALESCE(repo, '')=COALESCE(?, '')
          AND COALESCE(pr_number, 0)=COALESCE(?, 0)
          AND reason=?
        LIMIT 1
        """,
        (source, source_id, repo, pr_number, reason),
    ).fetchone()
    if existing:
        return
    conn.execute(
        """
        INSERT INTO human_review_items(source, source_id, repo, pr_number, subject,
            reason, summary, raw_snippet, created_at)
        VALUES (:source, :source_id, :repo, :pr_number, :subject, :reason,
            :summary, :raw_snippet, :created_at)
        """,
        {
            "source": source,
            "source_id": source_id,
            "repo": repo,
            "pr_number": pr_number,
            "subject": item.get("subject"),
            "reason": reason,
            "summary": item.get("summary"),
            "raw_snippet": item.get("raw_snippet"),
            "created_at": time.time(),
        },
    )
    conn.commit()


def cleanup_human_review_items(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT * FROM human_review_items WHERE status='open'").fetchall()
    closed = 0
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        item = dict(row)
        reason = str(item.get("reason") or "")
        summary = str(item.get("summary") or "")
        raw = str(item.get("raw_snippet") or "")
        text = f"{reason}\n{summary}\n{raw}".lower()
        key = (
            item.get("source"),
            item.get("source_id") or "",
            item.get("repo") or "",
            item.get("pr_number") or 0,
            reason,
        )
        broad_key = (
            item.get("repo") or "",
            item.get("pr_number") or 0,
            reason,
        )
        close = key in seen
        seen.add(key)
        close = close or broad_key in seen
        seen.add(broad_key)

        task = None
        if item.get("source_id"):
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (item.get("source_id"),)).fetchone()
        if not task and item.get("repo") and item.get("pr_number"):
            task = conn.execute(
                "SELECT * FROM tasks WHERE repo=? AND pr_number=?",
                (item.get("repo"), item.get("pr_number")),
            ).fetchone()
        task_dict = dict(task) if task else None

        if "codex process disappeared before opening a pr" in reason.lower():
            close = True
        elif task_dict and task_dict.get("pr_url") and any(
            token in reason.lower()
            for token in (
                "codex finished without opening a detectable pr",
                "initial codex run failed",
                "codex process disappeared before opening a pr",
            )
        ):
            close = True
        elif task_dict and str(task_dict.get("status") or "").upper() == "MERGED":
            close = True
        elif any(token in text for token in ("approved this pull request", "approved pr", "looks good to merge", "fixed", "resolved")):
            close = True
        elif "could not be automatically matched to a fixable active task" in reason.lower() and not any(
            token in text for token in ("changes requested", "failing", "failed", "bug", "error", "please", "could you", "needs")
        ):
            close = True

        if close:
            conn.execute("UPDATE human_review_items SET status='closed' WHERE id=?", (item["id"],))
            closed += 1
    conn.commit()
    return closed


def mark_merged(conn: sqlite3.Connection, *, repo: str, pr_number: int, pr_url: str = "", task_id: str = "", source_email_id: int | None = None, summary: str = "", merged_at: str = "") -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO successful_merged_pr(repo, pr_number, pr_url, merged_at,
            task_id, source_email_id, summary, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (repo, int(pr_number), pr_url, merged_at, task_id, source_email_id, summary, time.time()),
    )
    conn.commit()


def merged_pr_count(conn: sqlite3.Connection, repo: str) -> int:
    row = conn.execute(
        "SELECT count(*) AS c FROM successful_merged_pr WHERE repo=?",
        (str(repo or "").strip(),),
    ).fetchone()
    return int(row["c"] if row else 0)


def write_human_review_markdown(conn: sqlite3.Connection) -> Path:
    path = agent_home() / "human_review.md"
    rows = conn.execute(
        "SELECT * FROM human_review_items WHERE status='open' ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    lines = ["# OSS PR Agent Human Review", ""]
    if not rows:
        lines.append("No open human review items.")
    for row in rows:
        item = dict(row)
        lines.extend([
            f"## {item.get('repo') or 'unknown'} #{item.get('pr_number') or ''}".strip(),
            f"- Source: {item.get('source')} {item.get('source_id') or ''}".strip(),
            f"- Subject: {item.get('subject') or ''}",
            f"- Reason: {item.get('reason') or ''}",
            f"- Summary: {item.get('summary') or ''}",
            "",
            item.get("raw_snippet") or "",
            "",
        ])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
