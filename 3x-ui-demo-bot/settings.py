from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise RuntimeError(f"Environment variable {name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    panel_base_url: str
    panel_api_token: str
    inbound_ids: tuple[int, ...]
    demo_group: str
    demo_duration_hours: int
    demo_traffic_gb: int
    demo_ip_limit: int
    log_level: str


def load_settings() -> Settings:
    load_dotenv()
    admin_ids = frozenset(
        int(value.strip())
        for value in _required("ADMIN_IDS").split(",")
        if value.strip()
    )
    inbound_ids = tuple(
        int(value.strip())
        for value in _required("THREEXUI_INBOUND_IDS").split(",")
        if value.strip()
    )
    if not admin_ids or not inbound_ids:
        raise RuntimeError("ADMIN_IDS and THREEXUI_INBOUND_IDS cannot be empty")

    return Settings(
        bot_token=_required("BOT_TOKEN"),
        admin_ids=admin_ids,
        panel_base_url=_required("THREEXUI_BASE_URL").rstrip("/"),
        panel_api_token=_required("THREEXUI_API_TOKEN"),
        inbound_ids=inbound_ids,
        demo_group=os.getenv("DEMO_GROUP", "demo").strip() or "demo",
        demo_duration_hours=_positive_int("DEMO_DURATION_HOURS", 24),
        demo_traffic_gb=_positive_int("DEMO_TRAFFIC_GB", 5),
        demo_ip_limit=_positive_int("DEMO_IP_LIMIT", 1),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )

