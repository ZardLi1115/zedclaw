from __future__ import annotations

import email as email_lib
import imaplib
import logging
import os
import re
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Any

from . import llm

logger = logging.getLogger(__name__)

SUBJECT_RE = re.compile(r"(?:Re:\s*)?\[([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\].*?(?:#(\d+))?", re.I)
URL_RE = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:pull|issues)/(\d+)")


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for payload, charset in decode_header(value):
        if isinstance(payload, bytes):
            parts.append(payload.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(payload)
    return "".join(parts)


def _body(msg: email_lib.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                data = part.get_payload(decode=True) or b""
                return data.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    data = msg.get_payload(decode=True)
    if isinstance(data, bytes):
        return data.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return str(msg.get_payload() or "")


def _parse_pr(subject: str, body: str) -> tuple[str, int | None, str]:
    match = SUBJECT_RE.search(subject)
    repo = match.group(1) if match else ""
    number = int(match.group(2)) if match and match.group(2) else None
    url = ""
    url_match = URL_RE.search(body)
    if url_match:
        repo = repo or url_match.group(1)
        number = number or int(url_match.group(2))
        url = f"https://github.com/{url_match.group(1)}/pull/{url_match.group(2)}"
    return repo, number, url


def _is_github_pr_mail(sender: str, subject: str, body: str) -> bool:
    sender_l = sender.lower()
    if "github.com" not in sender_l and "notifications@github.com" not in sender_l:
        return False
    repo, number, _ = _parse_pr(subject, body)
    return bool(repo and number)


def _fallback_intent(subject: str, body: str) -> dict[str, Any]:
    text = f"{subject}\n{body[:2000]}".lower()
    if "merged" in text:
        return {"intent": "pr_merged", "urgency": "normal", "summary": "PR appears to be merged."}
    if any(token in text for token in ("failed", "failure", "requested changes", "changes requested", "bug")):
        return {"intent": "ci_failed", "urgency": "immediate", "summary": "PR appears to need fixes."}
    if "commented" in text or "review" in text:
        return {"intent": "review_comment", "urgency": "soon", "summary": "PR has a new comment or review."}
    return {"intent": "other_unhandled", "urgency": "normal", "summary": "PR-related email needs review."}


def classify_email(cfg: dict[str, Any], *, subject: str, body: str, repo: str, pr_number: int | None) -> dict[str, Any]:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    model = str(agent_cfg.get("email_classifier_model") or "gpt-5.4-mini")
    prompt = {
        "repo": repo,
        "pr_number": pr_number,
        "subject": subject,
        "body_snippet": body[:4000],
    }
    result = llm.call_json(
        model=model,
        system=(
            "Classify GitHub PR notification email intent. Return strict JSON with keys: "
            "intent, urgency, summary, recommended_runtime_action. intents: "
            "pr_merged, ci_failed, review_requested_changes, review_comment, "
            "new_bug, maintainer_question, closed_unmerged, other_actionable, other_unhandled."
        ),
        user=str(prompt),
        max_tokens=500,
    )
    return result or _fallback_intent(subject, body)


def fetch_latest_pr_emails(conn, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    limit = int(agent_cfg.get("email_check_limit", 10) or 10)
    addr = os.getenv("EMAIL_ADDRESS", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    host = os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com")
    port = int(os.getenv("EMAIL_IMAP_PORT", "993") or 993)
    if not addr or not password or not host:
        logger.info("oss_pr_agent email intake skipped: EMAIL_* IMAP config missing")
        return []

    out: list[dict[str, Any]] = []
    imap = imaplib.IMAP4_SSL(host, port, timeout=30)
    try:
        imap.login(addr, password)
        imap.select("INBOX")
        status, data = imap.uid("search", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()[-limit:]
        for uid in reversed(uids):
            status, msg_data = imap.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            message_id = (msg.get("Message-ID") or uid.decode()).strip()
            if conn.execute("SELECT 1 FROM email_list WHERE gmail_message_id=?", (message_id,)).fetchone():
                continue
            subject = _decode(msg.get("Subject"))
            sender = _decode(msg.get("From"))
            body = _body(msg)
            if not _is_github_pr_mail(sender, subject, body):
                continue
            repo, number, github_url = _parse_pr(subject, body)
            classified = classify_email(cfg, subject=subject, body=body, repo=repo, pr_number=number)
            received_at = ""
            try:
                received_at = parsedate_to_datetime(msg.get("Date")).isoformat()
            except Exception:
                pass
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO email_list(gmail_message_id, received_at, sender,
                    subject, repo, pr_number, github_url, model_intent, urgency,
                    summary, raw_snippet, action_taken, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id, received_at, sender, subject, repo, number,
                    github_url, classified.get("intent"), classified.get("urgency"),
                    classified.get("summary"), body[:2000], "pending", time.time(),
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
            item = {
                "id": row_id,
                "repo": repo,
                "pr_number": number,
                "github_url": github_url,
                "subject": subject,
                "intent": classified.get("intent"),
                "urgency": classified.get("urgency"),
                "summary": classified.get("summary"),
                "body": body,
            }
            out.append(item)
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return out

