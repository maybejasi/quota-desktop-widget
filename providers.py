from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from config import AppConfig


@dataclass
class QuotaBucket:
    key: str
    label: str
    remaining_pct: float  # 0.0 ~ 100.0
    used_pct: float  # 0.0 ~ 100.0
    reset_at: Optional[str] = None
    reset_in_seconds: Optional[float] = None
    status: str = "active"
    detail: str = ""

    def live_reset_text(self, synced_at_iso: Optional[str] = None) -> str:
        return format_reset_countdown(
            self.reset_at,
            fallback_seconds=self.reset_in_seconds,
            synced_at_iso=synced_at_iso,
        )


@dataclass
class ProviderQuota:
    provider: str  # "agy" | "opencode"
    title: str
    status: str = "loading"  # "ok" | "setup" | "stale" | "error" | "loading"
    badge: str = ""
    subtitle: str = ""
    buckets: list[QuotaBucket] = field(default_factory=list)
    updated_at: Optional[str] = None
    error: str = ""
    needs_setup: bool = False


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def parse_iso_utc(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str or not isinstance(iso_str, str):
        return None
    s = iso_str.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def format_reset_countdown(
    iso_str: Optional[str],
    fallback_seconds: Optional[float] = None,
    synced_at_iso: Optional[str] = None,
) -> str:
    now = datetime.now(timezone.utc)
    seconds: Optional[float] = None

    dt = parse_iso_utc(iso_str)
    if dt is not None:
        seconds = (dt - now).total_seconds()
    elif fallback_seconds is not None:
        elapsed = 0.0
        sync_dt = parse_iso_utc(synced_at_iso)
        if sync_dt is not None:
            elapsed = max(0.0, (now - sync_dt).total_seconds())
        seconds = float(fallback_seconds) - elapsed

    if seconds is None:
        return ""
    if seconds <= 0:
        return "即将重置"

    total_mins = int(seconds // 60)
    days = total_mins // (24 * 60)
    hours = (total_mins % (24 * 60)) // 60
    mins = total_mins % 60

    if days >= 1:
        return f"{days}天{hours}小时后重置" if hours > 0 else f"{days}天后重置"
    if hours >= 1:
        return f"{hours}小时{mins}分后重置" if mins > 0 else f"{hours}小时后重置"
    return f"{max(1, mins)}分钟后重置"


def format_relative_time(iso_str: Optional[str]) -> str:
    dt = parse_iso_utc(iso_str)
    if dt is None:
        return ""
    delta = (datetime.now(timezone.utc) - dt).total_seconds()
    if delta < 45:
        return "刚刚"
    mins = int(delta // 60)
    if mins < 60:
        return f"{mins}分钟前"
    hours = mins // 60
    if hours < 24:
        return f"{hours}小时前"
    days = hours // 24
    return f"{days}天前"


def _friendly_opencode_label(key: str, raw_label: str = "") -> str:
    k = key.lower().strip()
    mapping = {
        "rolling": "5小时滚动",
        "go_rolling": "5小时滚动",
        "fivehour": "5小时滚动",
        "five_hour": "5小时滚动",
        "5h": "5小时滚动",
        "session": "5小时滚动",
        "daily": "今日额度",
        "weekly": "本周额度",
        "go_weekly": "本周额度",
        "monthly": "本月额度",
        "go_monthly": "本月额度",
    }
    if k in mapping:
        return mapping[k]
    return raw_label or key


def _friendly_agy_label(key: str, raw_label: str = "") -> str:
    k = key.strip()
    low = k.lower()
    exact = {
        "gemini-5h": "Gemini · 5h",
        "gemini-rolling": "Gemini · 5h",
        "gemini-weekly": "Gemini · 本周",
        "3p-5h": "Claude/GPT · 5h",
        "3p-weekly": "Claude/GPT · 本周",
        "gemini-pro": "Gemini Pro",
        "gemini-flash": "Gemini Flash",
        "claude-weekly": "Claude · 本周",
        "claude-5h": "Claude · 5h",
        "third-party-weekly": "Claude/GPT · 本周",
        "third-party-5h": "Claude/GPT · 5h",
    }
    if low in exact:
        return exact[low]
    if raw_label:
        cleaned = (
            raw_label.replace("Claude & GPT (shared)", "Claude/GPT")
            .replace("5-hour", "5h")
            .replace("Weekly", "本周")
            .replace("Monthly", "本月")
        )
        return cleaned
    parts = [p for p in k.replace("_", "-").split("-") if p]
    translated = []
    for p in parts:
        pl = p.lower()
        if pl == "weekly":
            translated.append("本周")
        elif pl in ("5h", "rolling"):
            translated.append("5h")
        elif pl == "daily":
            translated.append("每日")
        elif pl == "monthly":
            translated.append("本月")
        else:
            translated.append(p.capitalize())
    return " ".join(translated) or k


def parse_t3_cache_payload(
    data: Any,
    provider: str,
    title: str,
    fallback_updated_at: Optional[str] = None,
) -> ProviderQuota:
    """Parse ~/.t3/caches/{antigravity,opencode}.json usageLimits schema."""
    if not isinstance(data, dict):
        return ProviderQuota(
            provider=provider,
            title=title,
            status="error",
            badge="格式异常",
            error="T3 Code 缓存格式无效",
        )

    usage_limits = data.get("usageLimits")
    if not isinstance(usage_limits, dict):
        return ProviderQuota(
            provider=provider,
            title=title,
            status="stale",
            badge="暂无额度",
            subtitle="T3 Code 缓存",
            error="T3 Code 缓存中尚无 usageLimits 数据（请在 T3 Code 中打开一次对应模型）",
        )

    checked_at = str(
        usage_limits.get("checkedAt")
        or data.get("checkedAt")
        or fallback_updated_at
        or datetime.now(timezone.utc).isoformat()
    )
    windows = usage_limits.get("windows")
    if not isinstance(windows, list) or not windows:
        return ProviderQuota(
            provider=provider,
            title=title,
            status="stale",
            badge="等待刷新",
            subtitle="T3 Code 缓存",
            updated_at=checked_at,
            error="T3 Code 暂未返回额度窗口数据",
        )

    # Order buckets logically (Gemini first for AGY; rolling -> weekly -> monthly for OpenCode Go)
    if provider == "agy":
        order_map = {"gemini-5h": 0, "gemini-weekly": 1, "3p-5h": 2, "3p-weekly": 3}
    else:
        order_map = {"go_rolling": 0, "rolling": 0, "go_weekly": 1, "weekly": 1, "go_monthly": 2, "monthly": 2}

    sorted_windows = sorted(
        [w for w in windows if isinstance(w, dict)],
        key=lambda w: order_map.get(str(w.get("id", "")).lower(), 99),
    )

    buckets: list[QuotaBucket] = []
    any_exhausted = False
    for idx, w in enumerate(sorted_windows):
        wid = str(w.get("id") or w.get("kind") or f"win-{idx}")
        raw_label = str(w.get("label") or "")

        if "usedPercent" in w and w["usedPercent"] is not None:
            used = _clamp_pct(float(w["usedPercent"]))
            rem = _clamp_pct(100.0 - used)
        elif "remainingPercent" in w and w["remainingPercent"] is not None:
            rem = _clamp_pct(float(w["remainingPercent"]))
            used = _clamp_pct(100.0 - rem)
        else:
            continue

        if rem <= 0.1:
            any_exhausted = True

        resets_at = w.get("resetsAt") or w.get("resetAt") or w.get("reset_time")
        label = (
            _friendly_agy_label(wid, raw_label)
            if provider == "agy"
            else _friendly_opencode_label(wid, raw_label)
        )
        buckets.append(
            QuotaBucket(
                key=wid,
                label=label,
                remaining_pct=rem,
                used_pct=used,
                reset_at=str(resets_at) if resets_at else None,
                detail=f"已用 {used:.1f}%",
            )
        )

    if not buckets:
        return ProviderQuota(
            provider=provider,
            title=title,
            status="stale",
            badge="无数据",
            subtitle="T3 Code 缓存",
            updated_at=checked_at,
            error="未能解析出有效额度百分比",
        )

    rel = format_relative_time(checked_at)
    subtitle = f"T3 Code · {rel}同步" if rel else "T3 Code 同步"
    provider_status = str(data.get("status", "ready")).lower()
    if any_exhausted:
        badge = "已达上限"
    elif provider_status == "ready":
        badge = "T3 已同步"
    else:
        badge = provider_status

    return ProviderQuota(
        provider=provider,
        title=title,
        status="ok",
        badge=badge,
        subtitle=subtitle,
        buckets=buckets,
        updated_at=checked_at,
    )


def _fetch_from_t3_cache(path: Path, provider: str, title: str) -> ProviderQuota:
    if not path.exists():
        return ProviderQuota(
            provider=provider,
            title=title,
            status="setup",
            badge="未找到缓存",
            subtitle="T3 Code 本地缓存",
            needs_setup=True,
            error=f"未找到 T3 Code 缓存文件：{path}（请先启动 T3 Code 或切换为其他数据源）",
        )
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        mtime_iso = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception as e:
        return ProviderQuota(
            provider=provider,
            title=title,
            status="error",
            badge="读取失败",
            error=f"读取 T3 Code 缓存失败：{e}",
        )
    return parse_t3_cache_payload(data, provider=provider, title=title, fallback_updated_at=mtime_iso)


def resolve_opencode_api_key(cfg: AppConfig) -> tuple[str, str]:
    """Return (api_key, source_name) without logging or exposing the key."""
    if cfg.opencode_api_key.strip():
        return cfg.opencode_api_key.strip(), "配置"

    env_key = os.environ.get("OPENCODE_API_KEY", "").strip()
    if env_key:
        return env_key, "环境变量"

    auth_paths = [
        Path.home() / ".local" / "share" / "opencode" / "auth.json",
        Path(os.environ.get("APPDATA", "")) / "opencode" / "auth.json",
    ]
    for p in auth_paths:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for provider_key in ("opencode-go", "opencode"):
                        entry = data.get(provider_key)
                        if isinstance(entry, dict):
                            tok = (
                                entry.get("key")
                                or entry.get("access")
                                or entry.get("token")
                                or ""
                            )
                            if isinstance(tok, str) and tok.strip():
                                return tok.strip(), "本地 auth.json"
            except Exception:
                pass
    return "", ""


def fetch_opencode_quota(cfg: AppConfig) -> ProviderQuota:
    if cfg.opencode_mode == "t3_cache":
        return _fetch_from_t3_cache(
            cfg.t3_opencode_cache_path(),
            provider="opencode",
            title="OpenCode Go",
        )

    key, _source = resolve_opencode_api_key(cfg)
    if not key:
        return ProviderQuota(
            provider="opencode",
            title="OpenCode Go",
            status="setup",
            badge="未配置",
            subtitle="OpenCode 用量接口",
            needs_setup=True,
            error="填入你的 OpenCode Console API Key 即可实时拉取 5h / 周 / 月额度",
        )

    endpoint = (cfg.opencode_endpoint or "https://opencode.ai/zen/go/v1/usage").strip()
    req = urllib.request.Request(
        endpoint,
        method="GET",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "QuotaDesktopWidget/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            msg = f"认证失败 (HTTP {e.code})，请检查 API Key 或 Go 订阅状态"
        elif e.code == 429:
            msg = "请求过于频繁 (HTTP 429)，将在下个周期重试"
        else:
            msg = f"接口返回错误 HTTP {e.code}"
        return ProviderQuota(
            provider="opencode",
            title="OpenCode Go",
            status="error",
            badge="异常",
            error=msg,
        )
    except Exception as e:
        return ProviderQuota(
            provider="opencode",
            title="OpenCode Go",
            status="error",
            badge="离线",
            error=f"网络请求失败：{e}",
        )

    return _parse_opencode_payload(data)


def _parse_opencode_payload(data: Any) -> ProviderQuota:
    now_iso = datetime.now(timezone.utc).isoformat()
    buckets: list[QuotaBucket] = []
    usage_obj = data.get("usage") if isinstance(data, dict) else None
    source_dict = usage_obj if isinstance(usage_obj, dict) else (data if isinstance(data, dict) else {})

    preferred_order = ["rolling", "fiveHour", "five_hour", "5h", "daily", "weekly", "monthly"]
    seen: set[str] = set()
    ordered_keys = [k for k in preferred_order if k in source_dict] + [
        k for k in source_dict.keys() if k not in preferred_order
    ]

    overall_status = "Active"
    for k in ordered_keys:
        if k in seen:
            continue
        v = source_dict.get(k)
        if not isinstance(v, dict):
            continue
        if "percent" not in v and "used_percent" not in v and "remaining_percent" not in v:
            continue
        seen.add(k)

        if "remaining_percent" in v:
            rem = _clamp_pct(float(v["remaining_percent"]))
            used = _clamp_pct(100.0 - rem)
        else:
            used = _clamp_pct(float(v.get("percent", v.get("used_percent", 0.0))))
            rem = _clamp_pct(100.0 - used)

        reset_at = v.get("resetsAt") or v.get("resetAt") or v.get("reset_time")
        st = str(v.get("status", "active"))
        if st.lower() in ("limited", "exhausted", "exceeded"):
            overall_status = "已达上限"

        buckets.append(
            QuotaBucket(
                key=k,
                label=_friendly_opencode_label(k),
                remaining_pct=rem,
                used_pct=used,
                reset_at=str(reset_at) if reset_at else None,
                status=st,
                detail=f"已用 {used:.0f}%",
            )
        )

    if not buckets:
        return ProviderQuota(
            provider="opencode",
            title="OpenCode Go",
            status="error",
            badge="解析失败",
            error="接口返回中未找到用量字段",
            updated_at=now_iso,
        )

    plan_badge = str(data.get("plan") or data.get("tier") or overall_status)
    return ProviderQuota(
        provider="opencode",
        title="OpenCode Go",
        status="ok",
        badge=plan_badge,
        subtitle="实时接口",
        buckets=buckets,
        updated_at=now_iso,
    )


def fetch_agy_quota(cfg: AppConfig) -> ProviderQuota:
    if cfg.agy_mode == "t3_cache":
        return _fetch_from_t3_cache(
            cfg.t3_agy_cache_path(),
            provider="agy",
            title="Antigravity (AGY)",
        )

    if cfg.agy_mode == "command":
        return _fetch_agy_from_command(cfg.agy_command)

    path = cfg.effective_agy_json_path()
    if not path.exists():
        if cfg.agy_mode == "json_file":
            return ProviderQuota(
                provider="agy",
                title="Antigravity (AGY)",
                status="setup",
                badge="未找到文件",
                needs_setup=True,
                error=f"未找到指定的 JSON 文件：{path}",
            )
        return ProviderQuota(
            provider="agy",
            title="Antigravity (AGY)",
            status="setup",
            badge="待接入",
            subtitle="AGY CLI StatusLine 同步",
            needs_setup=True,
            error="点下方「一键接入 AGY」，并在终端启动一次 agy 登录即可自动同步",
        )

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        mtime_iso = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception as e:
        return ProviderQuota(
            provider="agy",
            title="Antigravity (AGY)",
            status="error",
            badge="读取失败",
            error=f"缓存文件解析失败：{e}",
        )

    # Also auto-handle T3 Code cache format if pointed at a json_file
    if isinstance(data, dict) and isinstance(data.get("usageLimits"), dict):
        return parse_t3_cache_payload(data, provider="agy", title="Antigravity (AGY)", fallback_updated_at=mtime_iso)

    return _parse_agy_payload(data, fallback_updated_at=mtime_iso)


def _fetch_agy_from_command(command: str) -> ProviderQuota:
    cmd = (command or "").strip()
    if not cmd:
        return ProviderQuota(
            provider="agy",
            title="Antigravity (AGY)",
            status="setup",
            badge="未配置",
            needs_setup=True,
            error="请在设置中填写输出 AGY 额度 JSON 的命令",
        )
    try:
        kwargs: dict[str, Any] = {
            "shell": True,
            "capture_output": True,
            "text": True,
            "timeout": 10,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        res = subprocess.run(cmd, **kwargs)
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip()[:120]
            return ProviderQuota(
                provider="agy",
                title="Antigravity (AGY)",
                status="error",
                badge="命令失败",
                error=f"退出码 {res.returncode}: {err}",
            )
        data = json.loads(res.stdout)
        now_iso = datetime.now(timezone.utc).isoformat()
        if isinstance(data, dict) and isinstance(data.get("usageLimits"), dict):
            return parse_t3_cache_payload(data, provider="agy", title="Antigravity (AGY)", fallback_updated_at=now_iso)
        return _parse_agy_payload(data, fallback_updated_at=now_iso)
    except subprocess.TimeoutExpired:
        return ProviderQuota(
            provider="agy",
            title="Antigravity (AGY)",
            status="error",
            badge="超时",
            error="自定义命令执行超过 10 秒",
        )
    except Exception as e:
        return ProviderQuota(
            provider="agy",
            title="Antigravity (AGY)",
            status="error",
            badge="异常",
            error=f"执行自定义命令出错：{e}",
        )


def _parse_agy_payload(data: Any, fallback_updated_at: Optional[str] = None) -> ProviderQuota:
    if not isinstance(data, dict):
        return ProviderQuota(
            provider="agy",
            title="Antigravity (AGY)",
            status="error",
            badge="格式错误",
            error="AGY 数据必须是 JSON 对象",
        )

    updated_at = str(data.get("updated_at") or fallback_updated_at or datetime.now(timezone.utc).isoformat())
    plan_tier = str(data.get("plan_tier") or data.get("tier") or "").strip()
    email = str(data.get("email") or "").strip()

    buckets: list[QuotaBucket] = []

    quota_obj = data.get("quota")
    if isinstance(quota_obj, dict):
        for key, info in quota_obj.items():
            if not isinstance(info, dict):
                continue
            if info.get("disabled"):
                continue
            rem: Optional[float] = None
            detail = ""
            if "remaining_fraction" in info and info["remaining_fraction"] is not None:
                frac = float(info["remaining_fraction"])
                rem = _clamp_pct(frac * 100.0 if frac <= 1.0 else frac)
            elif "remaining_percent" in info and info["remaining_percent"] is not None:
                rem = _clamp_pct(float(info["remaining_percent"]))
            elif "used_percent" in info and info["used_percent"] is not None:
                rem = _clamp_pct(100.0 - float(info["used_percent"]))
            elif "remaining_amount" in info and info["remaining_amount"] is not None:
                rem = 100.0 if float(info["remaining_amount"]) > 0 else 0.0
                detail = f"余量 {info['remaining_amount']}"

            if rem is None:
                continue

            reset_time = info.get("reset_time") or info.get("resetsAt")
            reset_secs = info.get("reset_in_seconds")
            buckets.append(
                QuotaBucket(
                    key=str(key),
                    label=_friendly_agy_label(str(key)),
                    remaining_pct=rem,
                    used_pct=_clamp_pct(100.0 - rem),
                    reset_at=str(reset_time) if reset_time else None,
                    reset_in_seconds=float(reset_secs) if reset_secs is not None else None,
                    detail=detail or f"已用 {100.0 - rem:.0f}%",
                )
            )

    if not buckets and isinstance(data.get("buckets"), list):
        for idx, item in enumerate(data["buckets"]):
            if not isinstance(item, dict):
                continue
            rem_val = item.get("remaining_pct", item.get("remaining_percent"))
            if rem_val is None and "used_pct" in item:
                rem_val = 100.0 - float(item["used_pct"])
            if rem_val is None:
                continue
            rem = _clamp_pct(float(rem_val))
            key = str(item.get("key") or f"bucket-{idx}")
            label = str(item.get("label") or _friendly_agy_label(key))
            buckets.append(
                QuotaBucket(
                    key=key,
                    label=label,
                    remaining_pct=rem,
                    used_pct=_clamp_pct(100.0 - rem),
                    reset_at=item.get("reset_at") or item.get("reset_time"),
                    detail=str(item.get("detail") or f"已用 {100.0 - rem:.0f}%"),
                )
            )

    if not buckets:
        return ProviderQuota(
            provider="agy",
            title="Antigravity (AGY)",
            status="stale",
            badge=plan_tier or "等待数据",
            subtitle=email,
            updated_at=updated_at,
            error="已收到 AGY 会话状态，但尚无额度字段（请在 agy 中运行 /usage 刷新）",
        )

    rel = format_relative_time(updated_at)
    subtitle_parts = []
    if email:
        subtitle_parts.append(email.split("@")[0])
    if rel:
        subtitle_parts.append(f"{rel}同步")
    subtitle = " · ".join(subtitle_parts)

    return ProviderQuota(
        provider="agy",
        title="Antigravity (AGY)",
        status="ok",
        badge=plan_tier or "已同步",
        subtitle=subtitle,
        buckets=buckets,
        updated_at=updated_at,
    )


def make_demo_quotas() -> tuple[ProviderQuota, ProviderQuota]:
    """Sample quotas for `--demo` visual inspection without overwriting real data."""
    now = datetime.now(timezone.utc)
    iso = now.isoformat()

    agy = ProviderQuota(
        provider="agy",
        title="Antigravity (AGY)",
        status="ok",
        badge="T3 已同步",
        subtitle="T3 Code · 刚刚同步",
        updated_at=iso,
        buckets=[
            QuotaBucket(
                key="gemini-5h",
                label="Gemini · 5h",
                remaining_pct=82.0,
                used_pct=18.0,
                reset_at=(now + timedelta(hours=3, minutes=25)).isoformat(),
                detail="已用 18%",
            ),
            QuotaBucket(
                key="gemini-weekly",
                label="Gemini · 本周",
                remaining_pct=93.8,
                used_pct=6.2,
                reset_at=(now + timedelta(days=5, hours=11)).isoformat(),
                detail="已用 6%",
            ),
        ],
    )
    opencode = ProviderQuota(
        provider="opencode",
        title="OpenCode Go",
        status="ok",
        badge="T3 已同步",
        subtitle="T3 Code · 刚刚同步",
        updated_at=iso,
        buckets=[
            QuotaBucket(
                key="go_rolling",
                label="5小时滚动",
                remaining_pct=38.0,
                used_pct=62.0,
                reset_at=(now + timedelta(hours=2, minutes=14)).isoformat(),
                detail="已用 62%",
            ),
            QuotaBucket(
                key="go_weekly",
                label="本周额度",
                remaining_pct=69.0,
                used_pct=31.0,
                reset_at=(now + timedelta(days=4, hours=6)).isoformat(),
                detail="已用 31%",
            ),
            QuotaBucket(
                key="go_monthly",
                label="本月额度",
                remaining_pct=56.0,
                used_pct=44.0,
                reset_at=(now + timedelta(days=19, hours=2)).isoformat(),
                detail="已用 44%",
            ),
        ],
    )
    return agy, opencode
