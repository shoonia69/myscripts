from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    panel_url: str
    subscription_base_url: str
    inbound_ids: tuple[int, ...]
    all_inbounds: bool = False
    api_mode: str = "auto"
    api_token: str | None = None
    username: str | None = None
    password: str | None = None
    verify_tls: bool = True
    request_timeout: float = 15.0
    total_gb: int = 0
    expiry_days: int = 0
    limit_ip: int = 0
    flow: str = ""
    app_secret: str = ""
    rate_limit_per_hour: int = 10
    site_title: str = "Получить доступ"

    def __post_init__(self) -> None:
        for field_name in ("panel_url", "subscription_base_url"):
            value = getattr(self, field_name)
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{field_name} должен быть полным HTTP(S) URL")
        if (not self.inbound_ids and not self.all_inbounds) or any(item <= 0 for item in self.inbound_ids):
            raise ValueError("Укажите XUI_INBOUND_IDS как список ID или all")
        if self.api_mode not in {"auto", "modern", "legacy"}:
            raise ValueError("XUI_API_MODE: auto, modern или legacy")
        if not self.api_token and not (self.username and self.password):
            raise ValueError("Укажите XUI_API_TOKEN либо XUI_USERNAME и XUI_PASSWORD")
        if self.total_gb < 0 or self.expiry_days < 0 or self.limit_ip < 0:
            raise ValueError("Лимиты не могут быть отрицательными")
        if self.rate_limit_per_hour <= 0:
            raise ValueError("RATE_LIMIT_PER_HOUR должен быть положительным")


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"XUI_{name}") or os.environ.get(f"3XUI_{name}") or default


def from_env() -> Settings:
    inbound_raw = _env("INBOUND_IDS") or _env("INBOUND_ID")
    all_inbounds = inbound_raw.strip().lower() == "all"
    inbound_ids = () if all_inbounds else tuple(
        int(part.strip()) for part in inbound_raw.split(",") if part.strip()
    )
    panel_url = _env("PANEL_URL")
    return Settings(
        panel_url=panel_url,
        subscription_base_url=_env("SUBSCRIPTION_BASE_URL", f"{panel_url.rstrip('/')}/sub/"),
        inbound_ids=inbound_ids,
        all_inbounds=all_inbounds,
        api_mode=_env("API_MODE", "auto").lower(),
        api_token=_env("API_TOKEN") or None,
        username=_env("USERNAME") or None,
        password=_env("PASSWORD") or None,
        verify_tls=_bool(_env("VERIFY_TLS", "true")),
        request_timeout=float(_env("REQUEST_TIMEOUT", "15")),
        total_gb=int(os.environ.get("CLIENT_TOTAL_GB", "0")),
        expiry_days=int(os.environ.get("CLIENT_EXPIRY_DAYS", "0")),
        limit_ip=int(os.environ.get("CLIENT_LIMIT_IP", "0")),
        flow=os.environ.get("CLIENT_FLOW", ""),
        app_secret=os.environ.get("APP_SECRET", ""),
        rate_limit_per_hour=int(os.environ.get("RATE_LIMIT_PER_HOUR", "10")),
        site_title=os.environ.get("SITE_TITLE", "Получить доступ"),
    )
