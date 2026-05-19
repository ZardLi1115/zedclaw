from oss_pr_agent import github_ops, runtime


def test_normalize_openai_usage_limit_remaining():
    snapshot = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "limit": 1000,
        }
    }

    normalized = github_ops._normalize_usage_budget(snapshot)

    assert normalized["token_used"] == 125
    assert normalized["token_limit"] == 1000
    assert normalized["remaining"] == 875
    assert runtime._budget_state({"status": "ok", "normalized_usage": normalized}) == "available"


def test_normalize_anthropic_usage_with_limits():
    snapshot = {
        "usage": {
            "input_tokens": 200,
            "output_tokens": 50,
        },
        "limits": {
            "token_limit": 1000,
            "remaining_tokens": 750,
            "reset_at": "2026-05-19T12:00:00Z",
        },
    }

    normalized = github_ops._normalize_usage_budget(snapshot)

    assert normalized["token_used"] == 250
    assert normalized["remaining"] == 750
    assert normalized["reset_at"] == "2026-05-19T12:00:00Z"
    assert runtime._budget_state({"status": "ok", "normalized_usage": normalized}) == "available"


def test_normalize_usage_exhausted_when_remaining_zero():
    normalized = github_ops._normalize_usage_budget({"data": {"usage": {"total_tokens": 1000, "limit": 1000}}})

    assert normalized["remaining"] == 0
    assert runtime._budget_state({"status": "ok", "normalized_usage": normalized}) == "exhausted"


def test_normalize_usage_ignores_unrelated_payload():
    assert github_ops._normalize_usage_budget({"status": "ok", "models": ["gpt-test"]}) == {}
