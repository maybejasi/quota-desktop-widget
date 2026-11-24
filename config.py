from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

APP_NAME = "QuotaDesktopWidget"
APP_DISPLAY_NAME = "订阅额度桌面小组件"


def get_app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else (Path.home() / ".config")
    d = root / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_path() -> Path:
    return get_app_data_dir() / "config.json"


def get_agy_cache_path() -> Path:
    return get_app_data_dir() / "agy_quota_cache.json"


def get_default_t3_caches_dir() -> Path:
    return Path.home() / ".t3" / "caches"


@dataclass
class AppConfig:
    x: Optional[int] = None
    y: Optional[int] = None
    locked: bool = False
    layer_mode: str = "desktop"  # "desktop" | "floating" | "normal"
    theme: str = "dark"  # "dark" | "light"
    opacity: float = 0.92
    refresh_interval_sec: int = 60

    # Shared T3 Code cache directory (empty = ~/.t3/caches)
    t3_cache_dir: str = ""

    # OpenCode Go
    opencode_mode: str = "t3_cache"  # "t3_cache" | "api"
    opencode_api_key: str = ""
    opencode_endpoint: str = "https://opencode.ai/zen/go/v1/usage"

    # AGY (Antigravity)
    agy_mode: str = "t3_cache"  # "t3_cache" | "statusline" | "command" | "json_file"
    agy_command: str = ""
    agy_json_path: str = ""

    def effective_t3_caches_dir(self) -> Path:
        if self.t3_cache_dir.strip():
            return Path(self.t3_cache_dir.strip()).expanduser()
        return get_default_t3_caches_dir()

    def t3_agy_cache_path(self) -> Path:
        return self.effective_t3_caches_dir() / "antigravity.json"

    def t3_opencode_cache_path(self) -> Path:
        return self.effective_t3_caches_dir() / "opencode.json"

    def effective_agy_json_path(self) -> Path:
        if self.agy_mode == "t3_cache":
            return self.t3_agy_cache_path()
        if self.agy_mode == "json_file" and self.agy_json_path.strip():
            return Path(self.agy_json_path.strip()).expanduser()
        return get_agy_cache_path()


def load_config() -> AppConfig:
    path = get_config_path()
    if not path.exists():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return AppConfig()
        valid_fields = AppConfig.__dataclass_fields__.keys()
        filtered = {k: v for k, v in raw.items() if k in valid_fields}
        cfg = AppConfig(**filtered)
        cfg.opacity = max(0.55, min(1.0, float(cfg.opacity)))
        cfg.refresh_interval_sec = max(15, min(3600, int(cfg.refresh_interval_sec)))
        if cfg.layer_mode not in ("desktop", "floating", "normal"):
            cfg.layer_mode = "desktop"
        if cfg.theme not in ("dark", "light"):
            cfg.theme = "dark"
        if cfg.opencode_mode not in ("t3_cache", "api"):
            cfg.opencode_mode = "t3_cache"
        if cfg.agy_mode not in ("t3_cache", "statusline", "command", "json_file"):
            cfg.agy_mode = "t3_cache"
        return cfg
    except Exception:
        return AppConfig()


def save_config(cfg: AppConfig) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)
