# AI Usage Monitor

Local realtime quota monitor for Claude Code and OpenAI Codex.

AI Usage Monitor is a small local backend plus frontend integration pattern for showing assistant quota usage before a coding session is interrupted by rate limits. It is designed for developers who use Claude Code, Codex, or both, and want usage visibility inside an iOS app, desktop app, menu bar tool, or web dashboard.

This project is local-first. Provider credentials stay on the developer machine, and frontend clients receive only normalized usage percentages, reset timers, and status flags.

## Status

Working open-source release.

This repository contains a standalone, runnable implementation extracted from the CcCompanion local server: the usage readers, a dependency-free HTTP server (`GET /usage`, `GET /usage/active`), the Claude Code status-line capture script, and unit tests with sanitized fixtures. The iOS SwiftUI panel and web dashboard remain on the roadmap below.

Current implementation:

- Local Python backend endpoint: `GET /usage`
- Short cache window: 5 seconds
- Codex usage reader backed by the local Codex/ChatGPT session
- Claude Code usage reader backed by `ccusage` and a Claude Code status line capture file
- iOS SwiftUI usage panel with 5-second polling
- Warning threshold at 90%

## Why

AI coding assistants often stop during long refactors, debugging sessions, or build fixes because quota state is hidden in provider UI, CLI output, or local status data.

This project makes the useful parts visible in one normalized response:

- Claude Code current active block
- Claude Code subscription rate-limit windows when Claude exposes them
- Codex primary usage window
- Codex secondary weekly or longer usage window
- Additional Codex model-specific limits when available
- Reset countdowns
- Warning state for near-limit usage

## Data Sources

### Codex

Codex usage is read from the authenticated local Codex session:

```text
~/.codex/auth.json
```

The backend calls the Codex usage endpoint with the local access token and stores a sanitized cache at:

```text
~/.codex/usage-limits.json
```

The cache contains normalized usage data only, not the access token.

Important behavior:

- The backend prefers live API data.
- If the live API request fails, it falls back to the local cache.
- `reset_after_seconds` is recomputed from `reset_at` before returning cached data, so frontend countdowns do not freeze.

### Claude Code

Claude Code usage is collected from two sources:

```text
ccusage blocks --json
~/.claude/rate_limits_latest.json
```

`ccusage` provides the active Claude Code block, token totals, current cost, model list, and projected reset time.

`~/.claude/rate_limits_latest.json` is written by a Claude Code `statusLine` capture command when Claude exposes subscription rate-limit data.

Important behavior:

- The active block comes from `ccusage`.
- 5-hour and 7-day subscription windows come from the status line capture file when available.
- Expired Claude rate-limit windows are dropped instead of being returned with stale `0m` reset values.
- The frontend can fall back to the active `ccusage` block when a Claude subscription window is unavailable.

## API

### `GET /usage`

Returns the combined usage overview.

Example:

```json
{
  "ok": true,
  "codex": {
    "available": true,
    "stale": false,
    "plan": "prolite",
    "allowed": true,
    "limit_reached": false,
    "primary": {
      "used_percent": 26,
      "limit_window_seconds": 18000,
      "reset_after_seconds": 3369,
      "reset_at": 1779629625
    },
    "secondary": {
      "used_percent": 4,
      "limit_window_seconds": 604800,
      "reset_after_seconds": 590000,
      "reset_at": 1780216425
    },
    "additional": [],
    "credits": {
      "has_credits": false,
      "unlimited": false,
      "balance": "0",
      "overage_limit_reached": false
    }
  },
  "ccusage": {
    "available": true,
    "active_block": {
      "cost_usd": 1.13,
      "tokens": 607329,
      "end_time": "2026-05-24T15:00:00.000Z",
      "minutes_until_reset": 143,
      "models": ["claude-opus-4-7"]
    },
    "rate_limits": {
      "available": true,
      "stale": false,
      "model": "Opus 4.7",
      "five_hour": null,
      "seven_day": {
        "used_percent": 62,
        "reset_after_seconds": 73360,
        "reset_at": 1779699600
      }
    }
  }
}
```

### `GET /usage/active`

Returns only the active Claude Code block from `ccusage`.

Example:

```json
{
  "ok": true,
  "active": {
    "start_time": "2026-05-24T10:00:00.000Z",
    "end_time": "2026-05-24T15:00:00.000Z",
    "models": ["claude-opus-4-7"],
    "total_tokens": 544831,
    "cost_usd": 1.050393,
    "projection_remaining_min": 143
  }
}
```

## Frontend Behavior

Recommended UI rules:

- Poll `/usage` every 5 seconds while the usage panel is visible.
- Show provider sections independently; one provider being unavailable should not hide the other.
- Show a clear unavailable or waiting state instead of fake quota values.
- Use `used_percent` for progress bars.
- Use `reset_after_seconds` or `minutes_until_reset` for reset text.
- Mark rows red when usage reaches 90% or when `limit_reached` is true.
- Send at most one warning notification per quota window or per hour.

Suggested display:

```text
Claude Code
Current session
Resets in 2h23m
[==========----------] 544k tokens

Weekly limits
All models
Resets in 20h22m
[============--------] 62% used

Codex
Current session
Resets in 56m
[=====---------------] 26% used

Weekly limits
All models
Resets in 6d19h
[=-------------------] 4% used
```

## Security Model

Recommended defaults:

- Run the backend locally by default.
- Do not upload provider tokens.
- Do not log access tokens.
- Do not commit `~/.codex/auth.json`.
- Do not expose the backend publicly without authentication.
- Require a shared secret for LAN, Tailscale, or tunnel access.
- Return sanitized usage data to clients, not provider auth state.

For companion mobile apps:

- Backend runs on the developer machine.
- Phone connects over LAN, Tailscale, or another private tunnel.
- Requests include a shared secret, for example `X-Auth-Token`.
- The phone receives only normalized quota fields.

## Suggested Repository Layout

```text
ai-usage-monitor/
  README.md
  LICENSE
  server/
    app.py
    usage.py
    config.example.toml
  scripts/
    claude_status_capture.py
  examples/
    ios-swiftui/
      UsageSection.swift
    web/
      index.html
      app.js
  docs/
    api.md
    security.md
    claude-code.md
    codex.md
  tests/
    fixtures/
      codex_usage_response.json
      claude_rate_limits_latest.json
    test_usage.py
```

## Minimal Server Responsibilities

The standalone backend should:

- Read Codex auth from `~/.codex/auth.json`.
- Call the Codex usage endpoint.
- Cache sanitized Codex usage at `~/.codex/usage-limits.json`.
- Run `ccusage blocks --json` for active Claude Code block data.
- Read Claude status line quota data from `~/.claude/rate_limits_latest.json`.
- Normalize provider-specific fields into one API response.
- Recompute reset countdowns from absolute reset timestamps.
- Avoid returning expired rate-limit windows as live data.
- Protect remote access with a shared secret.

## Claude Status Line Capture

Claude Code can write rate-limit data through a status line command. A capture script should:

- Read JSON from stdin.
- Extract `rate_limits.five_hour` and `rate_limits.seven_day` when present.
- Write sanitized data to `~/.claude/rate_limits_latest.json`.
- Print a compact status line back to Claude Code.

Example Claude config shape:

```json
{
  "statusLine": {
    "type": "command",
    "command": "/path/to/ai-usage-monitor/scripts/claude_status_capture.py",
    "refreshInterval": 5
  }
}
```

## Install Sketch

This is the target standalone setup:

```bash
git clone https://github.com/waterside0219/ai-usage-monitor.git
cd ai-usage-monitor
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python server/app.py --host 127.0.0.1 --port 8795
```

For LAN or phone access:

```bash
python server/app.py --host 0.0.0.0 --port 8795 --shared-secret "$AI_USAGE_SECRET"
```

Then poll:

```http
GET http://127.0.0.1:8795/usage
X-Auth-Token: your-shared-secret
```

## Roadmap

- Extract current implementation into a standalone repo.
- Add a stable JSON schema.
- Add unit tests with sanitized fixture responses.
- Add a minimal FastAPI or stdlib HTTP server.
- Add sample SwiftUI usage panel.
- Add sample web dashboard.
- Add docs for Tailscale and LAN setup.
- Add packaging for a menu bar app or background service.

## Non-Goals

This project does not:

- Bypass provider limits.
- Increase quota.
- Replace official billing dashboards.
- Provide exact billing analytics.
- Upload usage data to a hosted service by default.

The goal is practical local visibility, not quota manipulation.

## License

MIT is a reasonable default for the standalone project.

## Disclaimer

This is an unofficial project. Claude, Claude Code, OpenAI, ChatGPT, and Codex are trademarks or products of their respective owners. This project only displays usage information available to the locally authenticated user.
