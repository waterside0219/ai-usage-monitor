#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


OUT = Path.home() / ".claude" / "rate_limits_latest.json"


def window(raw: dict, key: str) -> dict | None:
    item = (raw.get("rate_limits") or {}).get(key)
    if not isinstance(item, dict):
        return None
    used = item.get("used_percentage")
    resets_at = item.get("resets_at")
    if used is None or resets_at is None:
        return None
    try:
        return {
            "used_percent": int(round(float(used))),
            "reset_at": int(float(resets_at)),
            "reset_after_seconds": max(0, int(float(resets_at) - time.time())),
        }
    except Exception:
        return None


def main() -> int:
    raw_text = sys.stdin.read()
    try:
        raw = json.loads(raw_text)
    except Exception:
        print("Claude")
        return 0

    five_hour = window(raw, "five_hour")
    seven_day = window(raw, "seven_day")
    if five_hour or seven_day:
        payload = {
            "available": True,
            "updated_at": int(time.time()),
            "model": ((raw.get("model") or {}).get("display_name") or ""),
            "five_hour": five_hour,
            "seven_day": seven_day,
        }
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(OUT)

    bits = []
    if five_hour:
        bits.append(f"5h {five_hour['used_percent']}%")
    if seven_day:
        bits.append(f"7d {seven_day['used_percent']}%")
    print("Claude " + " ".join(bits) if bits else "Claude")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
