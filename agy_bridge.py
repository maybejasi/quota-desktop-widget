"""Official AGY (Antigravity CLI) statusLine bridge for QuotaDesktopWidget.

When configured in ~/.gemini/antigravity-cli/settings.json under `statusLine`,
Antigravity CLI pipes its live session JSON (including `quota`, `plan_tier`,
`email`, `model`, `cost`) to stdin. This script atomically caches that snapshot
for the desktop widget and exits immediately.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import get_agy_cache_path

BRIDGE_MARKER = "agy_bridge.py"


def get_agy_settings_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def _bridge_command() -> str:
    py = Path(sys.executable)
    # Prefer python.exe (stdout works reliably when agy invokes cmd /c)
    if py.name.lower() == "pythonw.exe":
        cand = py.with_name("python.exe")
        if cand.exists():
            py = cand
    script = Path(__file__).resolve()
    return f'"{py}" "{script}"'


def is_bridge_installed() -> bool:
    settings_path = get_agy_settings_path()
    if not settings_path.exists():
        return False
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        sl = data.get("statusLine")
        if isinstance(sl, dict) and sl.get("enabled", True):
            cmd = str(sl.get("command", ""))
            return BRIDGE_MARKER in cmd
    except Exception:
        pass
    return False


def install_bridge() -> tuple[bool, str]:
    settings_path = get_agy_settings_path()
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if settings_path.exists():
            backup = settings_path.with_suffix(".json.bak")
            shutil.copy2(settings_path, backup)
            raw = settings_path.read_text(encoding="utf-8").strip()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    data = parsed

        data["statusLine"] = {
            "type": "command",
            "command": _bridge_command(),
            "enabled": True,
            "stack_with_default": True,
        }
        tmp = settings_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, settings_path)
        return True, f"已写入 AGY 配置：{settings_path}"
    except Exception as exc:
        return False, f"安装 AGY 桥接失败：{exc}"


def uninstall_bridge() -> tuple[bool, str]:
    settings_path = get_agy_settings_path()
    if not settings_path.exists():
        return True, "未找到 AGY 配置文件，无需卸载"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False, "AGY 配置文件格式异常"
        sl = data.get("statusLine")
        if isinstance(sl, dict) and BRIDGE_MARKER in str(sl.get("command", "")):
            del data["statusLine"]
            tmp = settings_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, settings_path)
            return True, "已移除 AGY 状态栏桥接配置"
        return True, "当前未启用本小组件的 AGY 桥接"
    except Exception as exc:
        return False, f"卸载失败：{exc}"


def _run_as_statusline_bridge() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0

        snapshot = {
            "product": payload.get("product", "antigravity"),
            "version": payload.get("version", ""),
            "plan_tier": payload.get("plan_tier", ""),
            "email": payload.get("email", ""),
            "model": payload.get("model"),
            "quota": payload.get("quota") or {},
            "cost": payload.get("cost"),
            "agent_state": payload.get("agent_state", ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        cache_path = get_agy_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, cache_path)

        # Print a concise single-line summary for stack_with_default
        quota = snapshot.get("quota") or {}
        if isinstance(quota, dict) and quota:
            parts = []
            for key, info in list(quota.items())[:2]:
                if isinstance(info, dict) and "remaining_fraction" in info:
                    pct = max(0.0, min(100.0, float(info["remaining_fraction"]) * 100.0))
                    parts.append(f"{key}:{pct:.0f}%")
            if parts:
                sys.stdout.write("Widget已同步 · " + " | ".join(parts))
                sys.stdout.flush()
    except Exception:
        # Never break the user's agy statusLine session
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_as_statusline_bridge())
