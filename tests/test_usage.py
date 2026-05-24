"""Unit tests for the usage readers, using sanitized fixtures (no network, no tokens)."""
import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import usage  # noqa: E402
import claude_status_capture as capture  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load(name: str) -> dict:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class CodexSimplifyTest(unittest.TestCase):
    def test_simplify_primary_secondary(self):
        out = usage.CodexUsageReader()._simplify(_load("codex_usage_response.json"))
        self.assertTrue(out["available"])
        self.assertEqual(out["plan"], "prolite")
        self.assertEqual(out["primary"]["used_percent"], 26)
        self.assertEqual(out["secondary"]["used_percent"], 4)
        self.assertFalse(out["limit_reached"])

    def test_window_defaults_to_zero(self):
        self.assertEqual(
            usage.CodexUsageReader()._window({}),
            {"used_percent": 0, "limit_window_seconds": 0, "reset_after_seconds": 0, "reset_at": 0},
        )


class RecomputeResetTest(unittest.TestCase):
    def test_drop_expired_window(self):
        self.assertIsNone(
            usage._recompute_reset_after({"reset_at": int(time.time()) - 100}, drop_expired=True)
        )

    def test_future_window_recomputed(self):
        out = usage._recompute_reset_after({"reset_at": int(time.time()) + 3600})
        self.assertGreater(out["reset_after_seconds"], 0)


class ClaudeRateLimitTest(unittest.TestCase):
    def test_parse_fixture(self):
        reader = usage.ClaudeRateLimitReader(
            path=Path(FIX) / "claude_rate_limits_latest.json", max_age_seconds=10**12
        )
        out = reader.get()
        self.assertTrue(out["available"])
        self.assertEqual(out["model"], "Opus 4.7")
        self.assertEqual(out["seven_day"]["used_percent"], 62)
        self.assertIsNone(out["five_hour"])

    def test_missing_file_is_graceful(self):
        out = usage.ClaudeRateLimitReader(path=Path(FIX) / "does_not_exist.json").get()
        self.assertFalse(out["available"])


class CaptureWindowTest(unittest.TestCase):
    def test_window_parse(self):
        raw = {"rate_limits": {"seven_day": {"used_percentage": 62.4, "resets_at": time.time() + 7200}}}
        w = capture.window(raw, "seven_day")
        self.assertEqual(w["used_percent"], 62)
        self.assertGreater(w["reset_after_seconds"], 0)

    def test_window_missing(self):
        self.assertIsNone(capture.window({"rate_limits": {}}, "five_hour"))


if __name__ == "__main__":
    unittest.main()
