from __future__ import annotations

import fcntl
import json
import logging
import subprocess
import time
import uuid
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from zedclaw_cli.config import load_config

from . import codex_executor, daily_review, email_intake, github_ops, llm, notifier, state

logger = logging.getLogger(__name__)


ACTIVE_STATUSES = ("RUNNING_CODEX", "OPENING_PR", "WAITING_CI", "NEEDS_FIX", "FIXING_PR")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cfg() -> dict[str, Any]:
    cfg = load_config()
    return cfg if isinstance(cfg, dict) else {}


def _agent_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}


def _enabled(cfg: dict[str, Any]) -> bool:
    val = _agent_cfg(cfg).get("enabled", True)
    if isinstance(val, str):
        return val.strip().lower() not in {"0", "false", "no", "off"}
    return bool(val)


def _wake_bounds(cfg: dict[str, Any]) -> tuple[float, float]:
    agent_cfg = _agent_cfg(cfg)
    min_delay = float(agent_cfg.get("min_wake_delay_minutes", 5) or 5) * 60
    max_delay = float(agent_cfg.get("max_wake_delay_hours", 24) or 24) * 3600
    return max(60.0, min_delay), max(600.0, max_delay)


def _agent_timezone(cfg: dict[str, Any]) -> ZoneInfo:
    tz_name = str(_agent_cfg(cfg).get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("invalid oss_pr_agent timezone %r; falling back to Asia/Shanghai", tz_name)
        return ZoneInfo("Asia/Shanghai")


def _parse_hhmm(value: Any, fallback: str) -> dt_time:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return dt_time(hour, minute)
    except Exception:
        pass
    hour_text, minute_text = fallback.split(":", 1)
    return dt_time(int(hour_text), int(minute_text))


def _sleep_end_timestamp(cfg: dict[str, Any], at_ts: float | None = None) -> float | None:
    agent_cfg = _agent_cfg(cfg)
    if not agent_cfg.get("sleep_enabled", True):
        return None
    tz = _agent_timezone(cfg)
    local_now = datetime.fromtimestamp(at_ts or time.time(), tz=timezone.utc).astimezone(tz)
    start = _parse_hhmm(agent_cfg.get("sleep_start"), "21:00")
    end = _parse_hhmm(agent_cfg.get("sleep_end"), "09:00")
    today_start = datetime.combine(local_now.date(), start, tzinfo=tz)
    today_end = datetime.combine(local_now.date(), end, tzinfo=tz)

    if start == end:
        return None
    if start < end:
        if today_start <= local_now < today_end:
            return today_end.timestamp()
        return None

    if local_now >= today_start:
        return (today_end + timedelta(days=1)).timestamp()
    if local_now < today_end:
        return today_end.timestamp()
    return None


def _apply_sleep_window(cfg: dict[str, Any], target_ts: float) -> float:
    sleep_end = _sleep_end_timestamp(cfg, target_ts)
    return max(target_ts, sleep_end) if sleep_end else target_ts


def _set_next_wake(conn, cfg: dict[str, Any], delay_seconds: float, reason: str) -> None:
    min_delay, max_delay = _wake_bounds(cfg)
    delay = min(max(delay_seconds, min_delay), max_delay)
    state.set_meta(conn, "next_wake_at", _apply_sleep_window(cfg, time.time() + delay))
    state.set_meta(conn, "next_wake_reason", reason)


def _budget_aggression(budget: dict[str, Any]) -> tuple[str, float]:
    state_text = _budget_state(budget)
    if state_text == "exhausted":
        return "low", 0.75
    if state_text == "unknown":
        return "normal", 0.55
    if state_text == "available":
        return "high", 0.42
    return "max", 0.35


def _budget_state(budget: dict[str, Any]) -> str:
    """Classify budget telemetry without treating missing data as exhaustion."""
    if not budget or str(budget.get("status") or "").lower() == "unknown":
        return "unknown"
    text = json.dumps(budget, ensure_ascii=False).lower()
    if any(token in text for token in ("exhausted", "quota_exceeded", "rate_limit_exceeded", "insufficient_quota")):
        return "exhausted"

    def walk(value: Any, parent: str = "") -> str | None:
        if isinstance(value, dict):
            for key, child in value.items():
                found = walk(child, str(key).lower())
                if found:
                    return found
            return None
        if isinstance(value, list):
            for child in value:
                found = walk(child, parent)
                if found:
                    return found
            return None
        key = parent.lower()
        if isinstance(value, (int, float)):
            if any(name in key for name in ("remaining", "available", "left", "balance")):
                return "available" if float(value) > 0 else "exhausted"
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"unknown", "unavailable", "n/a", "none"}:
                return "unknown"
            if lowered in {"exhausted", "depleted", "quota_exceeded", "rate_limit_exceeded"}:
                return "exhausted"
        return None

    return walk(budget) or ("available" if any(x in text for x in ("reset", "remaining", "quota", "limit")) else "unknown")


def _planner_decision(cfg: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    agent_cfg = _agent_cfg(cfg)
    model = str(agent_cfg.get("planner_model") or cfg.get("model", {}).get("default") or "gpt-5.5")
    result = llm.call_json(
        model=model,
        system=(
            "You control an autonomous OSS PR runtime. Return strict JSON with "
            "action, reason, next_wake_delay_minutes, candidate_index. Optimize for "
            "opening and improving more PRs when budget is available, while prioritizing "
            "existing PR fixes before new tasks. Treat unknown or 404 budget telemetry "
            "as unknown, not exhausted; when active PR slots are available and candidates "
            "exist, prefer starting another task unless a PR needs fixing now."
        ),
        user=json.dumps(context, ensure_ascii=False),
        max_tokens=700,
        temperature=0.2,
    )
    return result or {}


def _task_id(repo: str, number: int | None) -> str:
    safe_repo = repo.replace("/", "__").replace(".", "_")
    suffix = str(number or uuid.uuid4().hex[:8])
    return f"{safe_repo}__{suffix}"


def _workspace_for(cfg: dict[str, Any], task_id: str) -> Path:
    raw = str(_agent_cfg(cfg).get("workspaces_dir") or "")
    base = Path(raw).expanduser() if raw else state.agent_home() / "workspaces"
    return base / task_id


def _handle_email_items(conn, cfg: dict[str, Any], items: list[dict[str, Any]]) -> None:
    for item in items:
        intent = str(item.get("intent") or "")
        repo = item.get("repo") or ""
        pr_number = item.get("pr_number")
        if intent == "pr_merged" and repo and pr_number:
            pr_url = item.get("github_url") or f"https://github.com/{repo}/pull/{pr_number}"
            status = github_ops.pr_status(pr_url)
            if not (status.get("mergedAt") or str(status.get("state") or "").upper() == "MERGED"):
                conn.execute("UPDATE email_list SET action_taken='merge_unverified' WHERE id=?", (item.get("id"),))
                conn.commit()
                continue
            state.mark_merged(
                conn,
                repo=repo,
                pr_number=int(pr_number),
                pr_url=pr_url,
                source_email_id=item.get("id"),
                summary=item.get("summary") or "",
                merged_at=status.get("mergedAt") or _now_iso(),
            )
            conn.execute("UPDATE email_list SET action_taken='marked_merged' WHERE id=?", (item.get("id"),))
            conn.commit()
            continue
        if intent in {"ci_failed", "review_requested_changes", "new_bug", "review_comment"} and repo and pr_number:
            row = conn.execute(
                "SELECT id, fix_attempts, max_fix_attempts FROM tasks WHERE repo=? AND pr_number=?",
                (repo, int(pr_number)),
            ).fetchone()
            if row and int(row["fix_attempts"] or 0) < int(row["max_fix_attempts"] or 5):
                state.update_task(conn, row["id"], status="NEEDS_FIX")
                conn.execute("UPDATE email_list SET action_taken='scheduled_fix' WHERE id=?", (item.get("id"),))
                conn.commit()
            else:
                task = {"id": row["id"] if row else "", "repo": repo, "pr_number": pr_number, "pr_url": item.get("github_url")}
                state.add_human_review_item(
                    conn,
                    source="email",
                    source_id=str(item.get("id")),
                    repo=repo,
                    pr_number=int(pr_number),
                    subject=item.get("subject"),
                    reason="PR-related email could not be automatically matched to a fixable active task.",
                    summary=item.get("summary"),
                    raw_snippet=(item.get("body") or "")[:2000],
                )
                notifier.notify_failed_human(cfg, task, "PR-related email/GitHub event could not be automatically matched to a fixable active task.")
        elif intent in {"maintainer_question", "other_actionable", "other_unhandled"}:
            task = {"id": "", "repo": repo, "pr_number": pr_number, "pr_url": item.get("github_url")}
            state.add_human_review_item(
                conn,
                source="email",
                source_id=str(item.get("id")),
                repo=repo,
                pr_number=pr_number,
                subject=item.get("subject"),
                reason=f"Email intent requires review or unsupported handling: {intent}",
                summary=item.get("summary"),
                raw_snippet=(item.get("body") or "")[:2000],
            )
            notifier.notify_failed_human(cfg, task, f"Email/GitHub event intent requires review or unsupported handling: {intent}")


def _codex_process_alive(task_id: str) -> bool:
    try:
        proc = subprocess.run(
            ["pgrep", "-af", "codex exec"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return True
    if proc.returncode not in {0, 1}:
        return True
    return task_id in (proc.stdout or "")


def _recover_stale_blocking_tasks(conn, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    stale_after = float(_agent_cfg(cfg).get("stale_codex_recovery_seconds", 180) or 180)
    now = time.time()
    recovered: list[dict[str, Any]] = []
    for task in state.list_tasks(conn, ["RUNNING_CODEX", "FIXING_PR", "OPENING_PR"]):
        if now - float(task.get("updated_at") or 0) < stale_after:
            continue
        if _codex_process_alive(task["id"]):
            continue
        old_status = str(task.get("status") or "")
        if task.get("pr_url"):
            state.update_task(
                conn,
                task["id"],
                status="NEEDS_FIX",
                failure_reason="Codex process disappeared before completing; scheduled another fix attempt.",
            )
            task["status"] = "NEEDS_FIX"
            notifier.notify_status_changed(cfg, task, old_status, "NEEDS_FIX", "Codex 进程异常结束，已重新排队修复。")
        else:
            reason = "Codex process disappeared before opening a PR."
            state.update_task(conn, task["id"], status="FAILED_NEEDS_HUMAN", failure_reason=reason)
            task["status"] = "FAILED_NEEDS_HUMAN"
            notifier.notify_status_changed(cfg, task, old_status, "FAILED_NEEDS_HUMAN", "Codex 进程异常结束，未生成 PR。")
        recovered.append(task)
    if recovered:
        state.write_human_review_markdown(conn)
    return recovered


def _github_activity_intent(kind: str, event: dict[str, Any], text: str) -> str | None:
    state_text = str(event.get("state") or "").lower()
    lowered = text.lower()
    if kind == "review":
        if state_text == "approved":
            return None
        if state_text == "changes_requested":
            return "review_requested_changes"
        if state_text == "commented":
            return "review_comment"
    if any(token in lowered for token in ("fail", "error", "bug", "broken", "regression", "does not", "doesn't", "schema", "invalid")):
        return "new_bug"
    if any(token in lowered for token in ("please", "could you", "can you", "should", "needs", "missing", "change")):
        return "review_comment"
    return None


def _fetch_latest_github_pr_events(conn, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    limit = int(_agent_cfg(cfg).get("github_pr_event_limit", 10) or 10)
    tasks = state.list_tasks(conn, ["WAITING_CI", "PR_READY", "NEEDS_FIX"])
    out: list[dict[str, Any]] = []
    for task in tasks[:5]:
        repo = task.get("repo") or ""
        pr_number = int(task.get("pr_number") or github_ops.parse_pr_number(task.get("pr_url") or "") or 0)
        if not repo or not pr_number:
            continue
        specs = [
            ("review", f"repos/{repo}/pulls/{pr_number}/reviews"),
            ("comment", f"repos/{repo}/issues/{pr_number}/comments"),
        ]
        for kind, endpoint in specs:
            proc = github_ops.run(["gh", "api", endpoint], cwd=task.get("workspace"), timeout=90)
            if proc.returncode != 0:
                logger.info("github PR activity fetch failed for %s #%s %s: %s", repo, pr_number, kind, proc.stderr.strip())
                continue
            try:
                events = json.loads(proc.stdout or "[]")
            except Exception:
                events = []
            for event in events[-limit:]:
                event_id = event.get("id") or event.get("node_id")
                if not event_id:
                    continue
                dedupe_id = f"github:{kind}:{repo}:{pr_number}:{event_id}"
                if conn.execute("SELECT 1 FROM email_list WHERE gmail_message_id=?", (dedupe_id,)).fetchone():
                    continue
                author = ((event.get("user") or {}).get("login") or "").strip()
                if author == "ZardLi1115":
                    continue
                body = str(event.get("body") or "")
                text = f"{kind} by {author}\nstate: {event.get('state') or ''}\n{body}"
                intent = _github_activity_intent(kind, event, text)
                if intent is None:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO email_list(gmail_message_id, received_at, sender,
                            subject, repo, pr_number, github_url, model_intent, urgency, summary,
                            raw_snippet, action_taken, processed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            dedupe_id,
                            event.get("submitted_at") or event.get("created_at") or _now_iso(),
                            author,
                            f"GitHub {kind} on {repo} PR #{pr_number}",
                            repo,
                            pr_number,
                            event.get("html_url") or task.get("pr_url"),
                            "ignored",
                            "normal",
                            f"Ignored non-actionable GitHub {kind}.",
                            text[:2000],
                            "ignored",
                            time.time(),
                        ),
                    )
                    conn.commit()
                    continue
                item = {
                    "gmail_message_id": dedupe_id,
                    "received_at": event.get("submitted_at") or event.get("created_at") or _now_iso(),
                    "sender": author,
                    "subject": f"GitHub {kind} on {repo} PR #{pr_number}",
                    "repo": repo,
                    "pr_number": pr_number,
                    "github_url": event.get("html_url") or task.get("pr_url"),
                    "intent": intent,
                    "urgency": "soon",
                    "summary": text[:1000],
                    "body": text,
                }
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO email_list(gmail_message_id, received_at, sender,
                        subject, repo, pr_number, github_url, model_intent, urgency, summary,
                        raw_snippet, processed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dedupe_id,
                        item["received_at"],
                        author,
                        item["subject"],
                        repo,
                        pr_number,
                        item["github_url"],
                        intent,
                        item["urgency"],
                        item["summary"],
                        text[:2000],
                        time.time(),
                    ),
                )
                conn.commit()
                if cur.rowcount:
                    item["id"] = conn.execute("SELECT id FROM email_list WHERE gmail_message_id=?", (dedupe_id,)).fetchone()["id"]
                    out.append(item)
    return out


def _monitor_active_prs(conn, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for task in state.list_tasks(conn, ACTIVE_STATUSES):
        if not task.get("pr_url"):
            continue
        status = github_ops.pr_status(task["pr_url"], cwd=task.get("workspace"))
        if status.get("mergedAt") or str(status.get("state") or "").upper() == "MERGED":
            old_status = task.get("status")
            state.update_task(conn, task["id"], status="MERGED")
            task["status"] = "MERGED"
            state.mark_merged(
                conn,
                repo=task["repo"],
                pr_number=int(task.get("pr_number") or github_ops.parse_pr_number(task["pr_url"]) or 0),
                pr_url=task["pr_url"],
                task_id=task["id"],
                merged_at=status.get("mergedAt") or _now_iso(),
            )
            notifier.notify_status_changed(cfg, task, old_status, "MERGED", "PR merged.")
            updates.append({"task": task["id"], "status": "MERGED"})
        elif str(status.get("state") or "").upper() == "CLOSED":
            old_status = task.get("status")
            reason = "PR was closed without merge."
            state.update_task(conn, task["id"], status="CLOSED_UNMERGED", failure_reason=reason)
            task["status"] = "CLOSED_UNMERGED"
            state.add_human_review_item(
                conn,
                source="runtime",
                source_id=task["id"],
                repo=task["repo"],
                pr_number=task.get("pr_number"),
                subject=task.get("title"),
                reason=reason,
                summary=json.dumps(status, ensure_ascii=False)[:2000],
            )
            notifier.notify_failed_human(cfg, task, reason)
            notifier.notify_status_changed(cfg, task, old_status, "CLOSED_UNMERGED", reason)
            updates.append({"task": task["id"], "status": "CLOSED_UNMERGED"})
        elif github_ops.checks_failed(status):
            if int(task.get("fix_attempts") or 0) >= int(task.get("max_fix_attempts") or 5):
                old_status = task.get("status")
                state.update_task(conn, task["id"], status="FAILED_NEEDS_HUMAN", failure_reason="PR checks failed after max fix attempts")
                task["status"] = "FAILED_NEEDS_HUMAN"
                state.add_human_review_item(
                    conn,
                    source="runtime",
                    source_id=task["id"],
                    repo=task["repo"],
                    pr_number=task.get("pr_number"),
                    subject=task.get("title"),
                    reason="PR checks failed after 5 automated fix attempts.",
                    summary=json.dumps(status, ensure_ascii=False)[:2000],
                )
                notifier.notify_failed_human(cfg, task, "PR checks failed after max fix attempts.")
                notifier.notify_status_changed(cfg, task, old_status, "FAILED_NEEDS_HUMAN", "PR checks failed after max fix attempts.")
                updates.append({"task": task["id"], "status": "FAILED_NEEDS_HUMAN"})
            else:
                old_status = task.get("status")
                state.update_task(conn, task["id"], status="NEEDS_FIX")
                task["status"] = "NEEDS_FIX"
                notifier.notify_status_changed(cfg, task, old_status, "NEEDS_FIX", "PR checks failed; queued for automated fix.")
                updates.append({"task": task["id"], "status": "NEEDS_FIX"})
        elif github_ops.checks_passed(status):
            old_status = task.get("status")
            state.update_task(conn, task["id"], status="PR_READY")
            task["status"] = "PR_READY"
            notifier.notify_status_changed(cfg, task, old_status, "PR_READY", "All detected checks are passing.")
            updates.append({"task": task["id"], "status": "PR_READY"})
        else:
            old_status = task.get("status")
            state.update_task(conn, task["id"], status="WAITING_CI")
            task["status"] = "WAITING_CI"
            notifier.notify_status_changed(cfg, task, old_status, "WAITING_CI", "PR checks are pending or unknown.")
            updates.append({"task": task["id"], "status": "WAITING_CI"})
    return updates


def _run_fix(conn, cfg: dict[str, Any], task: dict[str, Any]) -> None:
    pr_status = github_ops.pr_status(task.get("pr_url") or "", cwd=task.get("workspace"))
    prompt = codex_executor.fix_prompt(task, pr_status)
    next_attempt = int(task.get("fix_attempts") or 0) + 1
    max_attempts = int(task.get("max_fix_attempts") or 5)
    state.update_task(conn, task["id"], status="FIXING_PR", fix_attempts=next_attempt)
    result = codex_executor.run_codex(task=task, cfg=cfg, prompt=prompt, kind="fix")
    if result["status"] == "succeeded":
        state.update_task(conn, task["id"], status="WAITING_CI", failure_reason=None)
        task["fix_attempts"] = next_attempt
        notifier.notify_pr_resubmitted(cfg, task, next_attempt)
    else:
        reason = result.get("output") or "Codex fix run failed."
        if next_attempt >= max_attempts:
            state.update_task(conn, task["id"], status="FAILED_NEEDS_HUMAN", failure_reason=reason)
            state.add_human_review_item(
                conn,
                source="runtime",
                source_id=task["id"],
                repo=task["repo"],
                pr_number=task.get("pr_number"),
                subject=task.get("title"),
                reason="Codex fix run failed after max fix attempts.",
                summary=reason,
            )
            notifier.notify_failed_human(cfg, task, "Codex fix run failed after max fix attempts.")
        else:
            state.update_task(conn, task["id"], status="NEEDS_FIX", failure_reason=reason)


def _run_new_task(conn, cfg: dict[str, Any], candidate: dict[str, Any]) -> None:
    max_fix = int(_agent_cfg(cfg).get("max_fix_attempts", 5) or 5)
    task_id = _task_id(candidate["repo"], candidate.get("issue_number"))
    workspace = _workspace_for(cfg, task_id)
    task = {
        "id": task_id,
        "repo": candidate["repo"],
        "issue_url": candidate.get("issue_url"),
        "issue_number": candidate.get("issue_number"),
        "title": candidate.get("title") or "",
        "status": "RUNNING_CODEX",
        "workspace": str(workspace),
        "fix_attempts": 0,
        "max_fix_attempts": max_fix,
        "score": candidate.get("score", 0),
    }
    state.upsert_task(conn, task)
    notifier.notify_task_started(cfg, task)
    github_ops.clone_or_update(candidate["repo"], workspace)
    result = codex_executor.run_codex(
        task=task,
        cfg=cfg,
        prompt=codex_executor.initial_prompt(task),
        kind="initial",
    )
    pr_url = github_ops.current_pr_url(workspace)
    pr_number = github_ops.parse_pr_number(pr_url)
    if result["status"] == "succeeded" and pr_url:
        state.update_task(conn, task_id, status="WAITING_CI", pr_url=pr_url, pr_number=pr_number)
        task["pr_url"] = pr_url
        task["pr_number"] = pr_number
        notifier.notify_pr_submitted(cfg, task)
    elif result["status"] == "succeeded":
        state.update_task(conn, task_id, status="FAILED_NEEDS_HUMAN", failure_reason="Codex finished but no PR URL was detected")
        state.add_human_review_item(
            conn,
            source="runtime",
            source_id=task_id,
            repo=candidate["repo"],
            pr_number=None,
            subject=candidate.get("title"),
            reason="Codex finished without opening a detectable PR.",
            summary=result.get("result") or result.get("output"),
        )
        notifier.notify_failed_human(cfg, task, "Codex finished but no PR URL was detected.")
    else:
        state.update_task(conn, task_id, status="FAILED_NEEDS_HUMAN", failure_reason=result.get("output"))
        state.add_human_review_item(
            conn,
            source="runtime",
            source_id=task_id,
            repo=candidate["repo"],
            subject=candidate.get("title"),
            reason="Initial Codex run failed.",
            summary=result.get("output"),
        )
        notifier.notify_failed_human(cfg, task, "Initial Codex run failed.")


def tick_once() -> dict[str, Any]:
    cfg = _cfg()
    if not _enabled(cfg):
        return {"status": "disabled"}
    lock_path = state.agent_home() / ".runtime.lock"
    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "locked"}

        with state.connect() as conn:
            if daily_review.due() and not daily_review.already_ran_today(conn):
                review = daily_review.run_daily_review(conn, cfg)
                notifier.notify_daily_review(cfg, review)
                return {"status": "daily_review_completed"}

            now = time.time()
            sleep_end = _sleep_end_timestamp(cfg, now)
            if sleep_end:
                state.set_meta(conn, "next_wake_at", sleep_end)
                state.set_meta(conn, "next_wake_reason", "sleep window")
                return {"status": "sleep_window", "next_wake_at": sleep_end}

            next_wake = float(state.get_meta(conn, "next_wake_at", 0) or 0)
            if now < next_wake:
                email_poll_interval = float(_agent_cfg(cfg).get("email_poll_interval_seconds", 0) or 0)
                last_email_check = float(state.get_meta(conn, "last_email_check_at", 0) or 0)
                if email_poll_interval > 0 and now - last_email_check >= email_poll_interval:
                    state.set_meta(conn, "last_email_check_at", now)
                    email_items = email_intake.fetch_latest_pr_emails(conn, cfg)
                    github_items = _fetch_latest_github_pr_events(conn, cfg)
                    intake_items = email_items + github_items
                    _handle_email_items(conn, cfg, intake_items)
                    state.write_human_review_markdown(conn)
                    needs_fix = state.list_tasks(conn, ["NEEDS_FIX"])
                    if needs_fix and github_ops.gh_available() and github_ops.gh_auth_ok():
                        _run_fix(conn, cfg, needs_fix[0])
                        _set_next_wake(conn, cfg, 15 * 60, "check PR after email-triggered fix")
                        return {"status": "fix_started_intake_poll", "task": needs_fix[0]["id"], "email_items": len(email_items), "github_items": len(github_items)}
                    return {"status": "intake_poll", "email_items": len(email_items), "github_items": len(github_items), "next_wake_at": next_wake}
                return {"status": "sleeping", "next_wake_at": next_wake}

            email_items = email_intake.fetch_latest_pr_emails(conn, cfg)
            github_items = _fetch_latest_github_pr_events(conn, cfg)
            intake_items = email_items + github_items
            _handle_email_items(conn, cfg, intake_items)
            if not github_ops.gh_available() or not github_ops.gh_auth_ok():
                state.write_human_review_markdown(conn)
                _set_next_wake(conn, cfg, 60 * 60, "GitHub CLI is not authenticated")
                return {"status": "github_auth_missing", "email_items": len(email_items), "github_items": len(github_items)}

            pr_updates = _monitor_active_prs(conn, cfg)
            recovered_tasks = _recover_stale_blocking_tasks(conn, cfg)
            state.write_human_review_markdown(conn)

            needs_fix = state.list_tasks(conn, ["NEEDS_FIX"])
            if needs_fix:
                _run_fix(conn, cfg, needs_fix[0])
                _set_next_wake(conn, cfg, 15 * 60, "check PR after automated fix")
                return {"status": "fix_started", "task": needs_fix[0]["id"]}

            blocking_active = state.list_tasks(conn, ["RUNNING_CODEX", "FIXING_PR", "OPENING_PR"])
            if blocking_active:
                _set_next_wake(conn, cfg, 20 * 60, "check active PRs")
                return {"status": "active_prs_waiting", "count": len(blocking_active), "updates": pr_updates, "recovered": len(recovered_tasks)}

            waiting_prs = state.list_tasks(conn, ["WAITING_CI", "PR_READY"])
            max_active_prs = int(_agent_cfg(cfg).get("max_active_prs", 999) or 999)

            budget = github_ops.budget_snapshot(cfg)
            budget_state = _budget_state(budget)
            aggression, min_score = _budget_aggression(budget)
            candidates = github_ops.discover_issues(cfg, min_score=min_score)
            active_keys = {
                (task.get("repo"), int(task.get("issue_number") or 0))
                for task in state.list_tasks(conn, ["RUNNING_CODEX", "OPENING_PR", "WAITING_CI", "NEEDS_FIX", "FIXING_PR", "PR_READY"])
            }
            candidates = [
                candidate for candidate in candidates
                if (candidate.get("repo"), int(candidate.get("issue_number") or 0)) not in active_keys
            ]
            context = {
                "time": _now_iso(),
                "budget": budget,
                "budget_state": budget_state,
                "aggression": aggression,
                "min_score": min_score,
                "recent_email_items": email_items[:5],
                "recent_github_items": github_items[:5],
                "pr_updates": pr_updates,
                "active_prs": waiting_prs[:5],
                "max_active_prs": max_active_prs,
                "candidates": candidates[:8],
            }
            decision = _planner_decision(cfg, context)
            delay_minutes = float(decision.get("next_wake_delay_minutes") or (45 if candidates else 120))
            action = str(decision.get("action") or ("start_task" if candidates else "sleep")).strip().lower()
            active_slots_available = len(waiting_prs) < max_active_prs
            if budget_state == "exhausted":
                _set_next_wake(conn, cfg, delay_minutes * 60, decision.get("reason") or "budget exhausted")
                return {"status": "planned_sleep", "decision": decision, "budget_state": budget_state, "candidates": len(candidates)}
            if action in {"sleep", "wait", "planned_sleep"} and candidates and active_slots_available and budget_state == "unknown":
                action = "start_task"
                decision = {
                    **decision,
                    "action": action,
                    "reason": "Budget telemetry is unknown, not exhausted; active PR slots and candidates are available.",
                }
            if action in {"start_task", "start_new_task", "start_new", "start_candidate", "open_pr", "run_codex", "start_pr"} and candidates and active_slots_available:
                idx = int(decision.get("candidate_index") or 0)
                idx = min(max(idx, 0), len(candidates) - 1)
                _run_new_task(conn, cfg, candidates[idx])
                _set_next_wake(conn, cfg, 20 * 60, "check newly opened PR")
                return {"status": "task_started", "candidate": candidates[idx], "decision": decision}

            reason = decision.get("reason") or ("active PR slot limit reached" if candidates and not active_slots_available else "planner sleep")
            _set_next_wake(conn, cfg, delay_minutes * 60, reason)
            return {"status": "planned_sleep", "decision": decision, "budget_state": budget_state, "candidates": len(candidates)}


async def async_tick_once() -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(tick_once)
