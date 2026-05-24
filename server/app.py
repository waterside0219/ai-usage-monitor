#!/usr/bin/env python3
"""AI Usage Monitor — local HTTP server exposing normalized usage.

Endpoints:
  GET /usage         Combined overview: Codex quota + Claude Code active block + rate limits.
  GET /usage/active  Active Claude Code block only.

Remote access:
  Pass --shared-secret (or set env AI_USAGE_SECRET, or config.toml). When set,
  clients must send the same value in the  X-Auth-Token  header. Localhost
  requests are allowed without a secret by default.

This server reads provider credentials from the local machine at request time
and returns only normalized usage data. It never returns or logs access tokens.
"""
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from server.usage import ClaudeRateLimitReader, CodexUsageReader, UsageReader

logger = logging.getLogger("ai-usage-monitor")

_usage = UsageReader()
_codex = CodexUsageReader()
_claude = ClaudeRateLimitReader()


def build_overview() -> dict:
    """Assemble the combined /usage response."""
    active = _usage.get_active().get("active")
    ccusage: dict = {"available": active is not None}
    if active:
        ccusage["active_block"] = {
            "cost_usd": round(active.get("cost_usd", 0.0), 2),
            "tokens": active.get("total_tokens", 0),
            "end_time": active.get("end_time", ""),
            "minutes_until_reset": active.get("projection_remaining_min"),
            "models": active.get("models", []),
        }
    else:
        ccusage["active_block"] = None
    ccusage["rate_limits"] = _claude.get()
    return {"ok": True, "codex": _codex.get(), "ccusage": ccusage}


class Handler(BaseHTTPRequestHandler):
    shared_secret = ""

    def log_message(self, *args):  # keep stdout quiet
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not self.shared_secret:
            return True
        return hmac.compare_digest(self.headers.get("X-Auth-Token", ""), self.shared_secret)

    def do_GET(self):
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            if self.path == "/usage":
                self._send(200, build_overview())
            elif self.path == "/usage/active":
                self._send(200, _usage.get_active())
            else:
                self._send(404, {"ok": False, "error": "not_found"})
        except Exception as e:  # noqa: BLE001 — return message only, never token state
            logger.exception("request failed")
            self._send(500, {"ok": False, "error": str(e)})


def _load_config(path: str) -> dict:
    if not path:
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:
        logger.warning("tomllib unavailable (needs Python 3.11+); ignoring --config, use CLI flags")
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f).get("server", {})


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Usage Monitor local server")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--shared-secret", default=None,
                        help="require matching X-Auth-Token header (recommended off-localhost)")
    parser.add_argument("--config", default=None, help="path to config.toml")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    host = args.host or cfg.get("host") or "127.0.0.1"
    port = args.port or cfg.get("port") or 8795
    secret = args.shared_secret or cfg.get("shared_secret") or os.environ.get("AI_USAGE_SECRET", "")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Handler.shared_secret = secret
    if host not in ("127.0.0.1", "localhost") and not secret:
        logger.warning("binding to %s WITHOUT a shared secret — anyone on the network can read your usage", host)

    server = ThreadingHTTPServer((host, port), Handler)
    logger.info("AI Usage Monitor listening on http://%s:%d  (GET /usage, /usage/active)", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
