from __future__ import annotations

from oss_pr_agent import prefer, state


def test_normalize_repo_accepts_url_and_owner_repo():
    assert prefer.normalize_repo("https://github.com/ZardLi1115/zedclaw") == "ZardLi1115/zedclaw"
    assert prefer.normalize_repo("ZardLi1115/zedclaw") == "ZardLi1115/zedclaw"


def test_approved_preferred_plan_becomes_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEDCLAW_HOME", str(tmp_path / ".zedclaw"))
    with state.connect() as conn:
        state.upsert_preferred_plan(
            conn,
            {
                "id": "pref_test",
                "repo": "owner/repo",
                "repo_url": "https://github.com/owner/repo",
                "status": "approved",
                "title": "Fix flaky eval",
                "issue_url": "https://github.com/owner/repo/issues/1",
                "issue_number": 1,
                "score": 0.8,
                "plan_json": {"summary": "stabilize fixture"},
            },
        )
        candidate = prefer.next_approved_candidate(conn)

    assert candidate == {
        "repo": "owner/repo",
        "issue_url": "https://github.com/owner/repo/issues/1",
        "issue_number": 1,
        "title": "Fix flaky eval",
        "score": 0.99,
        "preferred_plan_id": "pref_test",
        "preferred_plan": {"summary": "stabilize fixture"},
    }
