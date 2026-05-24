"""AI coding assistant usage readers."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("cc-apns-server.usage")

CCUSAGE_BINS = ("/opt/homebrew/bin/ccusage", "/usr/local/bin/ccusage", "ccusage")
CACHE_TTL_SECONDS = 5
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"


def _recompute_reset_after(window: Any, *, drop_expired: bool = False) -> dict[str, Any] | None:
    if not isinstance(window, dict):
        return None
    normalized = dict(window)
    try:
        reset_at = int(normalized.get("reset_at") or 0)
    except Exception:
        reset_at = 0
    if reset_at > 0:
        reset_after = max(0, int(reset_at - time.time()))
        if drop_expired and reset_after <= 0:
            return None
        normalized["reset_after_seconds"] = reset_after
    return normalized


class UsageReader:
    def __init__(self, cache_ttl: int = CACHE_TTL_SECONDS):
        self._lock = threading.Lock()
        self._cache_ttl = cache_ttl
        self._cached: dict[str, Any] | None = None
        self._cached_at: float = 0.0

    def get_active(self) -> dict[str, Any]:
        """Return simplified active block snapshot."""
        now = time.time()
        with self._lock:
            if self._cached is not None and (now - self._cached_at) < self._cache_ttl:
                return self._cached

        snapshot = self._fetch()
        with self._lock:
            self._cached = snapshot
            self._cached_at = time.time()
        return snapshot

    def _fetch(self) -> dict[str, Any]:
        exe = _first_existing_bin(CCUSAGE_BINS)
        if not exe:
            logger.warning("ccusage binary not found")
            return {"ok": True, "active": None, "error": "ccusage_not_installed"}
        try:
            proc = subprocess.run(
                [exe, "blocks", "--active", "--json"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except FileNotFoundError:
            logger.warning("ccusage binary not found at %s", exe)
            return {"ok": True, "active": None, "error": "ccusage_not_installed"}
        except subprocess.TimeoutExpired:
            logger.warning("ccusage timeout")
            return {"ok": True, "active": None, "error": "ccusage_timeout"}
        except Exception as e:
            logger.exception("ccusage subprocess fail")
            return {"ok": True, "active": None, "error": f"subprocess_fail: {e}"}

        if proc.returncode != 0:
            logger.warning("ccusage exit=%d stderr=%s", proc.returncode, proc.stderr[:200])
            return {"ok": True, "active": None, "error": f"ccusage_exit_{proc.returncode}"}

        try:
            data = json.loads(proc.stdout)
        except Exception as e:
            logger.warning("ccusage json parse fail: %s", e)
            return {"ok": True, "active": None, "error": "json_parse_fail"}

        blocks = data.get("blocks") or []
        active = next((b for b in blocks if b.get("isActive")), None)
        if not active:
            return {"ok": True, "active": None}

        token_counts = active.get("tokenCounts") or {}
        burn = active.get("burnRate") or {}
        projection = active.get("projection") or {}

        return {
            "ok": True,
            "active": {
                "start_time": active.get("startTime"),
                "end_time": active.get("endTime"),
                "models": active.get("models") or [],
                "entries": active.get("entries", 0),
                "total_tokens": active.get("totalTokens", 0),
                "input_tokens": token_counts.get("inputTokens", 0),
                "output_tokens": token_counts.get("outputTokens", 0),
                "cache_create_tokens": token_counts.get("cacheCreationInputTokens", 0),
                "cache_read_tokens": token_counts.get("cacheReadInputTokens", 0),
                "cost_usd": active.get("costUSD", 0.0),
                "burn_tokens_per_min": burn.get("tokensPerMinute", 0.0),
                "burn_indicator": burn.get("tokensPerMinuteForIndicator", 0.0),
                "burn_cost_per_hour": burn.get("costPerHour", 0.0),
                "projection_total_tokens": projection.get("totalTokens", 0),
                "projection_total_cost": projection.get("totalCost", 0.0),
                "projection_remaining_min": projection.get("remainingMinutes", 0),
            },
        }


class CodexUsageReader:
    """Read Codex quota from the ChatGPT backend API using Codex CLI OAuth."""

    def __init__(self, cache_ttl: int = 5, auth_path: Path | None = None):
        self._lock = threading.Lock()
        self._cache_ttl = cache_ttl
        self._auth_path = auth_path or (Path.home() / ".codex" / "auth.json")
        self._cache_path = Path.home() / ".codex" / "usage-limits.json"
        self._cached: dict[str, Any] | None = None
        self._cached_at: float = 0.0

    def get(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            if self._cached is not None and (now - self._cached_at) < self._cache_ttl:
                return self._normalize_cached_windows(self._cached)

        snapshot = self._fetch()
        with self._lock:
            self._cached = snapshot
            self._cached_at = time.time()
        return self._normalize_cached_windows(snapshot)

    def _fetch(self) -> dict[str, Any]:
        if not self._auth_path.exists():
            return {"available": False, "error": "codex_auth_not_found"}

        try:
            auth = json.loads(self._auth_path.read_text(encoding="utf-8"))
            tokens = auth.get("tokens") or {}
            access_token = tokens.get("access_token")
            account_id = tokens.get("account_id")
        except Exception as e:
            logger.warning("codex auth parse fail: %s", e)
            return {"available": False, "error": "codex_auth_parse_failed"}

        if not access_token:
            return {"available": False, "error": "codex_access_token_missing"}

        headers = {
            "Authorization": f"Bearer {access_token}",
            "OpenAI-Beta": "codex_cli",
            "User-Agent": "CcCompanion/usage-reader",
        }
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id

        req = urllib.request.Request(CODEX_USAGE_URL, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning("codex usage http=%s", e.code)
            cached = self._read_cached_file()
            if cached:
                cached["stale"] = True
                return cached
            return {"available": False, "error": f"codex_usage_http_{e.code}"}
        except Exception as e:
            logger.warning("codex usage fetch fail: %s", e)
            cached = self._read_cached_file()
            if cached:
                cached["stale"] = True
                return cached
            return {"available": False, "error": "codex_usage_fetch_failed"}

        result = self._simplify(raw)
        self._write_cached_file(result)
        return result

    def _simplify(self, raw: dict[str, Any]) -> dict[str, Any]:
        rate = raw.get("rate_limit") or {}
        credits = raw.get("credits") or {}
        additional = []
        for item in raw.get("additional_rate_limits") or []:
            item_rate = item.get("rate_limit") or {}
            additional.append({
                "name": item.get("limit_name") or item.get("metered_feature") or "model",
                "allowed": bool(item_rate.get("allowed", True)),
                "limit_reached": bool(item_rate.get("limit_reached", False)),
                "primary": self._window(item_rate.get("primary_window") or {}),
                "secondary": self._window(item_rate.get("secondary_window") or {}),
            })

        return {
            "available": True,
            "stale": False,
            "plan": raw.get("plan_type") or "",
            "allowed": bool(rate.get("allowed", True)),
            "limit_reached": bool(rate.get("limit_reached", False)),
            "rate_limit_reached_type": raw.get("rate_limit_reached_type"),
            "primary": self._window(rate.get("primary_window") or {}),
            "secondary": self._window(rate.get("secondary_window") or {}),
            "additional": additional,
            "credits": {
                "has_credits": bool(credits.get("has_credits", False)),
                "unlimited": bool(credits.get("unlimited", False)),
                "balance": str(credits.get("balance", "0")),
                "overage_limit_reached": bool(credits.get("overage_limit_reached", False)),
            },
        }

    def _window(self, window: dict[str, Any]) -> dict[str, Any]:
        return {
            "used_percent": int(window.get("used_percent") or 0),
            "limit_window_seconds": int(window.get("limit_window_seconds") or 0),
            "reset_after_seconds": int(window.get("reset_after_seconds") or 0),
            "reset_at": int(window.get("reset_at") or 0),
        }

    def _read_cached_file(self) -> dict[str, Any] | None:
        try:
            if self._cache_path.exists():
                data = json.loads(self._cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("available"):
                    data = self._normalize_cached_windows(data)
                    return data
        except Exception:
            return None
        return None

    def _write_cached_file(self, result: dict[str, Any]) -> None:
        try:
            self._cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            os.chmod(self._cache_path, 0o600)
        except Exception:
            pass

    def _normalize_cached_windows(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(data)
        normalized["primary"] = _recompute_reset_after(normalized.get("primary"))
        normalized["secondary"] = _recompute_reset_after(normalized.get("secondary"))
        additional = []
        for item in normalized.get("additional") or []:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            entry["primary"] = _recompute_reset_after(entry.get("primary"))
            entry["secondary"] = _recompute_reset_after(entry.get("secondary"))
            additional.append(entry)
        normalized["additional"] = additional
        return normalized


def _first_existing_bin(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if "/" in candidate:
            if Path(candidate).exists():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


class ClaudeRateLimitReader:
    """Read Claude Code subscription rate limits captured from statusLine stdin."""

    def __init__(self, path: Path | None = None, max_age_seconds: int = 120):
        self._path = path or (Path.home() / ".claude" / "rate_limits_latest.json")
        self._max_age_seconds = max_age_seconds

    def get(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"available": False, "error": "claude_statusline_not_seen_yet"}
        except Exception:
            return {"available": False, "error": "claude_statusline_parse_failed"}

        updated_at = int(data.get("updated_at") or 0)
        stale = updated_at <= 0 or (time.time() - updated_at) > self._max_age_seconds
        return {
            "available": bool(data.get("available", True)),
            "stale": stale,
            "updated_at": updated_at,
            "model": data.get("model") or "",
            "five_hour": _recompute_reset_after(data.get("five_hour"), drop_expired=True),
            "seven_day": _recompute_reset_after(data.get("seven_day"), drop_expired=True),
        }
