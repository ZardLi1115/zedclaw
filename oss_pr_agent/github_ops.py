from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zedclaw_constants import get_zedclaw_home

logger = logging.getLogger(__name__)

CPA_BUDGET_CACHE_SUCCESS_SECONDS = 600
CPA_BUDGET_CACHE_FAILURE_SECONDS = 1800
CPA_BUDGET_MIN_QUERY_SECONDS = 600


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


def _read_cpa_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path or "/opt/cliproxyapi/config.yaml").expanduser()
    if not cfg_path.exists():
        return {}
    data: dict[str, Any] = {"config_path": str(cfg_path)}
    section: str | None = None
    for raw in cfg_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 and stripped.endswith(":"):
            section = stripped[:-1]
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        value = value.strip().strip("'\"")
        if indent == 0:
            data[key.strip()] = value
            section = key.strip()
        elif section == "remote-management" and key.strip() == "secret-key":
            data["management_key"] = value
        elif key.strip() == "auth-dir":
            data["auth_dir"] = value
    return data


def _load_codex_budget_auths(agent_cfg: dict[str, Any], cpa_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    explicit_file = str(agent_cfg.get("cpa_codex_auth_file") or "").strip()
    auth_dir = Path(str(agent_cfg.get("cpa_auth_dir") or cpa_cfg.get("auth_dir") or "~/.cli-proxy-api")).expanduser()
    candidates = [Path(explicit_file).expanduser()] if explicit_file else sorted(auth_dir.glob("codex-*.json"))
    wanted_emails = {
        email.strip().lower()
        for email in re.split(r"[\s,]+", str(agent_cfg.get("cpa_codex_auth_email") or agent_cfg.get("codex_budget_email") or ""))
        if email.strip()
    }
    explicit_index = str(agent_cfg.get("cpa_codex_auth_index") or "").strip()
    explicit_account = str(agent_cfg.get("cpa_codex_account_id") or "").strip()
    usable: list[tuple[int, Path, dict[str, Any], dict[str, Any]]] = []
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("disabled"):
            continue
        email = str(data.get("email") or "").lower()
        if wanted_emails and email not in wanted_emails:
            continue
        plan_hint = f"{path.name} {data.get('plan_type') or data.get('planType') or ''}".lower()
        priority = 0
        if "team" in plan_hint:
            priority = 30
        elif "plus" in plan_hint or "pro" in plan_hint:
            priority = 20
        elif "free" in plan_hint:
            priority = 5
        configured_auth_index = explicit_index or data.get("auth_index") or data.get("authIndex")
        auth_index = str(configured_auth_index or path.name)
        fallbacks = [auth_index]
        if configured_auth_index:
            fallbacks.extend([path.name, path.stem])
        auth = {
            "auth_index": auth_index,
            "auth_index_fallbacks": fallbacks,
            "account_id": str(explicit_account or data.get("account_id") or ""),
            "email": data.get("email"),
            "file": str(path),
            "priority": priority,
        }
        usable.append((priority, path, data, auth))
    if not usable:
        return []
    return [auth for _, _, _, auth in sorted(usable, key=lambda item: (-item[0], item[1].name))]


def _extract_cpa_budget_windows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rate_limit = data.get("rate_limit") or data.get("rateLimit") or {}
    review_limit = data.get("code_review_rate_limit") or data.get("codeReviewRateLimit") or {}
    additional = data.get("additional_rate_limits") or data.get("additionalRateLimits") or []
    specs = [
        ("codex_5h", rate_limit.get("primary_window") or rate_limit.get("primaryWindow")),
        ("codex_weekly", rate_limit.get("secondary_window") or rate_limit.get("secondaryWindow")),
        ("review_5h", review_limit.get("primary_window") or review_limit.get("primaryWindow")),
        ("review_weekly", review_limit.get("secondary_window") or review_limit.get("secondaryWindow")),
    ]
    for index, item in enumerate(additional if isinstance(additional, list) else []):
        limit = item.get("rate_limit") or item.get("rateLimit") or {}
        name = item.get("limit_name") or item.get("limitName") or item.get("metered_feature") or item.get("meteredFeature") or f"additional_{index + 1}"
        specs.append((f"{name}_5h", limit.get("primary_window") or limit.get("primaryWindow")))
        specs.append((f"{name}_weekly", limit.get("secondary_window") or limit.get("secondaryWindow")))
    windows: list[dict[str, Any]] = []
    for name, window in specs:
        if not isinstance(window, dict):
            continue
        used_percent = window.get("used_percent", window.get("usedPercent"))
        limit_seconds = window.get("limit_window_seconds", window.get("limitWindowSeconds"))
        reset_at = window.get("reset_at") or window.get("resetAt") or window.get("resets_at") or window.get("resetsAt")
        entry = {
            "name": name,
            "used_percent": used_percent,
            "remaining_percent": None,
            "limit_window_seconds": limit_seconds,
            "reset_at": reset_at,
        }
        try:
            entry["remaining_percent"] = max(0.0, 100.0 - float(used_percent))
        except Exception:
            pass
        windows.append(entry)
    return windows


def _window_remaining_sum(accounts: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for account in accounts:
        for window in account.get("windows") or []:
            name = window.get("name")
            remaining = window.get("remaining_percent")
            if not name or remaining is None:
                continue
            try:
                totals[str(name)] = totals.get(str(name), 0.0) + float(remaining)
            except Exception:
                continue
    return totals


def _budget_cache_path() -> Path:
    return get_zedclaw_home() / "oss_pr_agent" / "budget_cache.json"


def _budget_cache_ttls(agent_cfg: dict[str, Any]) -> tuple[float, float, float]:
    def read_seconds(key: str, default: int) -> float:
        try:
            return max(0.0, float(agent_cfg.get(key, default) or default))
        except Exception:
            return float(default)

    return (
        read_seconds("cpa_budget_cache_success_seconds", CPA_BUDGET_CACHE_SUCCESS_SECONDS),
        read_seconds("cpa_budget_cache_failure_seconds", CPA_BUDGET_CACHE_FAILURE_SECONDS),
        read_seconds("cpa_budget_min_query_seconds", CPA_BUDGET_MIN_QUERY_SECONDS),
    )


def _load_budget_cache(agent_cfg: dict[str, Any]) -> dict[str, Any] | None:
    try:
        data = json.loads(_budget_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    snapshot = data.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    try:
        fetched_at = float(data.get("fetched_at") or 0)
    except Exception:
        return None
    age = time.time() - fetched_at
    if age < 0:
        return None
    success_ttl, failure_ttl, min_interval = _budget_cache_ttls(agent_cfg)
    status = str(snapshot.get("status") or "").lower()
    ttl = success_ttl if status == "ok" else failure_ttl
    if age <= ttl or age <= min_interval:
        cached = dict(snapshot)
        cached["cached"] = True
        cached["cache_age_seconds"] = int(age)
        return cached
    return None


def _save_budget_cache(snapshot: dict[str, Any]) -> None:
    try:
        path = _budget_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"fetched_at": time.time(), "snapshot": snapshot}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception as exc:
        logger.debug("failed to write CPA budget cache: %s", exc)


def _query_cpa_wham_usage(base: str, key: str, auth: dict[str, Any]) -> dict[str, Any]:
    import urllib.request

    headers = {
        "Authorization": "Bearer $TOKEN$",
        "Content-Type": "application/json",
        "User-Agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
    }
    if auth.get("account_id"):
        headers["Chatgpt-Account-Id"] = auth["account_id"]
    last_error = ""
    for auth_index in dict.fromkeys(auth.get("auth_index_fallbacks") or [auth["auth_index"]]):
        payload = {
            "authIndex": auth_index,
            "method": "GET",
            "url": "https://chatgpt.com/backend-api/wham/usage",
            "header": headers,
        }
        req = urllib.request.Request(
            f"{base}/api-call",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read(100000).decode("utf-8", errors="replace")
            outer = json.loads(raw or "{}")
            status_code = int(outer.get("status_code") or outer.get("statusCode") or 0)
            body = outer.get("body")
            if isinstance(body, str):
                body_data = json.loads(body) if body.strip().startswith(("{", "[")) else {"raw": body}
            elif isinstance(body, dict):
                body_data = body
            else:
                body_data = {}
            if 200 <= status_code < 300 and isinstance(body_data, dict):
                return {
                    "status": "ok",
                    "auth_email": auth.get("email"),
                    "auth_index": auth_index,
                    "account_id": auth.get("account_id"),
                    "plan_type": body_data.get("plan_type") or body_data.get("planType"),
                    "windows": _extract_cpa_budget_windows(body_data),
                    "data": body_data,
                }
            last_error = f"api-call returned {status_code}: {str(body)[:500]}"
        except Exception as exc:
            last_error = str(exc)
    return {
        "status": "unknown",
        "auth_email": auth.get("email"),
        "account_id": auth.get("account_id"),
        "error": last_error,
    }


def _cpa_budget_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    cached = _load_budget_cache(agent_cfg)
    if cached is not None:
        return cached
    cpa_cfg = _read_cpa_config(str(agent_cfg.get("cpa_config_path") or "").strip() or None)
    base = str(
        agent_cfg.get("cpa_management_base_url")
        or agent_cfg.get("cpa_management_url")
        or cpa_cfg.get("management_base_url")
        or ""
    ).strip().rstrip("/")
    if not base and cpa_cfg:
        host = str(cpa_cfg.get("host") or "127.0.0.1").strip() or "127.0.0.1"
        port = str(cpa_cfg.get("port") or "8317").strip() or "8317"
        base = f"http://{host}:{port}/v0/management"
    if base and not base.endswith("/v0/management"):
        base = f"{base}/v0/management"
    key = str(agent_cfg.get("cpa_management_key") or cpa_cfg.get("management_key") or "").strip()
    auths = _load_codex_budget_auths(agent_cfg, cpa_cfg)
    if not base or not key or not auths:
        snapshot = {
            "status": "unknown",
            "source": "cpa_management",
            "reason": "missing CPA management base/key or Codex auth file",
        }
        _save_budget_cache(snapshot)
        return snapshot

    accounts = [_query_cpa_wham_usage(base, key, auth) for auth in auths]
    ok_accounts = [account for account in accounts if account.get("status") == "ok"]
    if ok_accounts:
        snapshot = {
            "status": "ok",
            "source": "cpa_wham_usage_pool",
            "account_count": len(accounts),
            "available_account_count": len(ok_accounts),
            "window_remaining_percent_sum": _window_remaining_sum(ok_accounts),
            "accounts": ok_accounts,
            "failed_accounts": [account for account in accounts if account.get("status") != "ok"],
        }
        _save_budget_cache(snapshot)
        return snapshot
    snapshot = {
        "status": "unknown",
        "source": "cpa_wham_usage_pool",
        "account_count": len(accounts),
        "accounts": accounts,
        "error": "; ".join(str(account.get("error") or "") for account in accounts if account.get("error"))[:1000],
    }
    _save_budget_cache(snapshot)
    return snapshot


def budget_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    agent_cfg = cfg.get("oss_pr_agent", {}) if isinstance(cfg, dict) else {}
    if (
        agent_cfg.get("cpa_budget_enabled", True)
        and (
            agent_cfg.get("cpa_management_base_url")
            or agent_cfg.get("cpa_management_url")
            or Path(str(agent_cfg.get("cpa_config_path") or "/opt/cliproxyapi/config.yaml")).expanduser().exists()
        )
    ):
        budget = _cpa_budget_snapshot(cfg)
        if budget.get("status") != "unknown" or not agent_cfg.get("budget_url"):
            return budget
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
