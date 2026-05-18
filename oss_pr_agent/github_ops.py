from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def run(cmd: list[str], *, cwd: str | Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def gh_available() -> bool:
    proc = run(["gh", "--version"], timeout=15)
    return proc.returncode == 0


def gh_auth_ok() -> bool:
    proc = run(["gh", "auth", "status"], timeout=30)
    return proc.returncode == 0


def _score_issue(item: dict[str, Any], *, min_score: float) -> float:
    title = str(item.get("title") or "").lower()
    labels = " ".join(str(l.get("name") or "").lower() for l in item.get("labels", []) if isinstance(l, dict))
    score = 0.35
    if any(x in labels for x in ("good first issue", "help wanted", "bug", "tests", "ci", "documentation")):
        score += 0.2
    if any(x in title for x in ("test", "ci", "docs", "typo", "bug", "fixture", "harness", "eval")):
        score += 0.2
    return max(min(score, 1.0), min_score - 0.2)


def _parse_github_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _repo_eligible(repo: str, *, max_inactive_days: int, min_stars: int) -> tuple[bool, str, int | None]:
    proc = run(
        ["gh", "repo", "view", repo, "--json", "pushedAt,updatedAt,isArchived,stargazerCount"],
        timeout=60,
    )
    if proc.returncode != 0:
        logger.info("repo maintenance check failed for %s: %s", repo, proc.stderr.strip())
        return False, "repo metadata unavailable", None
    try:
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return False, "repo metadata parse failed", None
    stars = data.get("stargazerCount")
    try:
        stars = int(stars)
    except Exception:
        stars = None
    if data.get("isArchived"):
        return False, "repo archived", stars
    if min_stars > 0 and (stars is None or stars < min_stars):
        return False, f"repo has {stars or 0} stars below required {min_stars}", stars
    timestamps = [
        _parse_github_time(data.get("pushedAt")),
        _parse_github_time(data.get("updatedAt")),
    ]
    latest = max((ts for ts in timestamps if ts is not None), default=None)
    if latest is None:
        return False, "repo activity unknown", stars
    inactive_days = (datetime.now(timezone.utc) - latest.astimezone(timezone.utc)).days
    if inactive_days > max_inactive_days:
        return False, f"repo inactive for {inactive_days} days", stars
    return True, f"repo active {inactive_days} days ago, {stars or 0} stars", stars


def discover_issues(cfg: dict[str, Any], *, min_score: float) -> list[dict[str, Any]]:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    limit = int(agent_cfg.get("github_search_limit", 12) or 12)
    max_inactive_days = int(agent_cfg.get("max_repo_inactive_days", 30) or 30)
    fallback_inactive_days = int(agent_cfg.get("fallback_max_repo_inactive_days", 60) or 60)
    min_stars = int(agent_cfg.get("min_repo_stars", 100) or 100)
    queries = agent_cfg.get("repository_queries") or [
        "agent label:\"good first issue\" language:Python",
        "LLM eval harness label:bug",
        "agent framework help wanted",
        "benchmark harness bug",
        "MCP agent tests",
    ]
    if not gh_available() or not gh_auth_ok():
        logger.warning("oss_pr_agent discovery skipped: gh unavailable or unauthenticated")
        return []

    def collect(search_queries: list[str], *, score_floor: float, inactive_days: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        per_query_limit = str(max(5, (limit * 2) // max(len(search_queries), 1)))
        for query in search_queries:
            proc = run(
                [
                    "gh", "search", "issues",
                    "--state", "open",
                    "--json", "repository,title,url,number,labels,updatedAt",
                    "--limit", per_query_limit,
                    query,
                ],
                timeout=120,
            )
            if proc.returncode != 0:
                logger.info("gh search failed for %r: %s", query, proc.stderr.strip())
                continue
            try:
                items = json.loads(proc.stdout or "[]")
            except Exception:
                items = []
            for item in items:
                repo_obj = item.get("repository") or {}
                repo = repo_obj.get("fullName") or repo_obj.get("nameWithOwner") or ""
                if not repo:
                    continue
                score = _score_issue(item, min_score=score_floor)
                candidates.append({
                    "repo": repo,
                    "issue_url": item.get("url"),
                    "issue_number": item.get("number"),
                    "title": item.get("title") or "",
                    "score": score,
                    "labels": item.get("labels") or [],
                    "updated_at": item.get("updatedAt"),
                })
        candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        seen = set()
        unique = []
        repo_activity: dict[str, tuple[bool, str, int | None]] = {}
        for c in candidates:
            key = (c["repo"], c.get("issue_number"))
            if key in seen:
                continue
            seen.add(key)
            if c["repo"] not in repo_activity:
                repo_activity[c["repo"]] = _repo_eligible(c["repo"], max_inactive_days=inactive_days, min_stars=min_stars)
            active, reason, stars = repo_activity[c["repo"]]
            c["repo_activity"] = reason
            c["repo_stars"] = stars
            if not active:
                logger.info("skip candidate %s #%s: %s", c["repo"], c.get("issue_number"), reason)
                continue
            if float(c.get("score", 0)) >= score_floor:
                unique.append(c)
        return unique

    strict = collect(list(queries), score_floor=min_score, inactive_days=max_inactive_days)
    if len(strict) >= limit:
        return strict[:limit]

    fallback_queries = agent_cfg.get("fallback_repository_queries") or [
        "agent language:Python",
        "LLM language:Python",
        "harness language:Python",
        "eval language:Python",
        "MCP language:Python",
        "AI agent tests",
        "large language model bug",
    ]
    fallback_score = float(agent_cfg.get("fallback_min_score", 0.2) or 0.2)
    relaxed = collect(list(fallback_queries), score_floor=fallback_score, inactive_days=fallback_inactive_days)
    merged: list[dict[str, Any]] = []
    seen_keys = set()
    for item in strict + relaxed:
        key = (item["repo"], item.get("issue_number"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        merged.append(item)
    return merged[:limit]


def clone_or_update(repo: str, workspace: Path) -> None:
    workspace.parent.mkdir(parents=True, exist_ok=True)
    if (workspace / ".git").exists():
        run(["git", "fetch", "--all", "--prune"], cwd=workspace, timeout=300)
        return
    proc = run(["gh", "repo", "clone", repo, str(workspace)], timeout=600)
    if proc.returncode != 0:
        proc = run(["git", "clone", f"https://github.com/{repo}.git", str(workspace)], timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"clone failed for {repo}")


def current_pr_url(workspace: Path) -> str:
    proc = run(["gh", "pr", "view", "--json", "url", "-q", ".url"], cwd=workspace, timeout=60)
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    return ""


def parse_pr_number(pr_url: str) -> int | None:
    match = re.search(r"/pull/(\d+)", pr_url or "")
    return int(match.group(1)) if match else None


def pr_status(pr_url: str, *, cwd: str | Path | None = None) -> dict[str, Any]:
    if not pr_url:
        return {"state": "unknown", "checks": []}
    view = run(
        ["gh", "pr", "view", pr_url, "--json", "url,state,mergedAt,isDraft,reviewDecision,number,title,headRefName"],
        cwd=cwd,
        timeout=90,
    )
    data: dict[str, Any] = {}
    if view.returncode == 0:
        try:
            data = json.loads(view.stdout or "{}")
        except Exception:
            data = {}
    checks = run(["gh", "pr", "checks", pr_url, "--json", "name,state,conclusion,link"], cwd=cwd, timeout=120)
    check_items: list[dict[str, Any]] = []
    if checks.returncode == 0:
        try:
            check_items = json.loads(checks.stdout or "[]")
        except Exception:
            check_items = []
    data["checks"] = check_items
    data["checks_error"] = checks.stderr.strip() if checks.returncode != 0 else ""
    return data


def checks_failed(status: dict[str, Any]) -> bool:
    for check in status.get("checks") or []:
        state = str(check.get("state") or "").lower()
        conclusion = str(check.get("conclusion") or "").lower()
        if state in {"failure", "failed"} or conclusion in {"failure", "failed", "cancelled", "timed_out"}:
            return True
    return False


def checks_pending(status: dict[str, Any]) -> bool:
    for check in status.get("checks") or []:
        state = str(check.get("state") or "").lower()
        conclusion = str(check.get("conclusion") or "").lower()
        if state in {"pending", "queued", "in_progress"} or conclusion in {"", "pending"}:
            return True
    return False


def checks_passed(status: dict[str, Any]) -> bool:
    checks = status.get("checks") or []
    if not checks:
        return False
    return not checks_failed(status) and not checks_pending(status)


def budget_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    url = str(agent_cfg.get("budget_url") or "").strip()
    if not url:
        model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
        base = str(model_cfg.get("base_url") or "").rstrip("/")
        url = f"{base}/usage" if base else ""
    if not url:
        return {"status": "unknown"}
    try:
        import urllib.request

        req = urllib.request.Request(url)
        api_key = str((cfg.get("model", {}) or {}).get("api_key") or "")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read(20000).decode("utf-8", errors="replace")
        try:
            return {"status": "ok", "data": json.loads(raw)}
        except Exception:
            return {"status": "ok", "raw": raw[:2000]}
    except Exception as exc:
        return {"status": "unknown", "error": str(exc)}
