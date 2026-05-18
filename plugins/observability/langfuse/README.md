# Langfuse Observability Plugin

This plugin ships bundled with ZedClaw but is **opt-in** — it only loads when
you explicitly enable it.

## Enable

Pick one:

```bash
# Interactive: walks you through credentials + SDK install + enable
zedclaw tools  # → Langfuse Observability

# Manual
pip install langfuse
zedclaw plugins enable observability/langfuse
```

## Required credentials

Set these in `~/.zedclaw/.env` (or via `zedclaw tools`):

```bash
ZEDCLAW_LANGFUSE_PUBLIC_KEY=pk-lf-...
ZEDCLAW_LANGFUSE_SECRET_KEY=sk-lf-...
ZEDCLAW_LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or your self-hosted URL
```

Without the SDK or credentials the hooks no-op silently — the plugin fails
open.

## Verify

```bash
zedclaw plugins list                 # observability/langfuse should show "enabled"
zedclaw chat -q "hello"              # then check Langfuse for a "ZedClaw turn" trace
```

## Optional tuning

```bash
ZEDCLAW_LANGFUSE_ENV=production       # environment tag
ZEDCLAW_LANGFUSE_RELEASE=v1.0.0       # release tag
ZEDCLAW_LANGFUSE_SAMPLE_RATE=0.5      # sample 50% of traces
ZEDCLAW_LANGFUSE_MAX_CHARS=12000      # max chars per field (default: 12000)
ZEDCLAW_LANGFUSE_DEBUG=true           # verbose plugin logging
```

## Disable

```bash
zedclaw plugins disable observability/langfuse
```
