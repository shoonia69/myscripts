from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken

from .config import Settings
from .identity import ClientIdentity
from .three_x_ui import ThreeXUIClient


@dataclass(frozen=True)
class PanelDraft:
    name: str
    panel_url: str
    subscription_base_url: str
    api_token: str = field(repr=False)
    all_inbounds: bool = True
    inbound_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        name = " ".join(self.name.split())
        if not 1 <= len(name) <= 64:
            raise ValueError("Название панели должно содержать от 1 до 64 символов.")
        object.__setattr__(self, "name", name)
        for field_name in ("panel_url", "subscription_base_url"):
            value = getattr(self, field_name).strip()
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{field_name} должен быть полным HTTP(S) URL.")
            object.__setattr__(self, field_name, value.rstrip("/") + "/")
        if not self.api_token.strip():
            raise ValueError("API Token панели не может быть пустым.")
        object.__setattr__(self, "api_token", self.api_token.strip())
        if (not self.all_inbounds and not self.inbound_ids) or any(
            inbound_id <= 0 for inbound_id in self.inbound_ids
        ):
            raise ValueError("Укажите all или положительные inbound ID.")


@dataclass(frozen=True)
class PanelRecord:
    id: int
    name: str
    panel_url: str
    subscription_base_url: str
    api_token: str = field(repr=False)
    all_inbounds: bool = True
    inbound_ids: tuple[int, ...] = ()
    enabled: bool = True
    source_key: str | None = None


@dataclass(frozen=True)
class PanelProvisionItem:
    panel_id: int
    panel_name: str
    url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class PanelProvisionResult:
    successful: list[PanelProvisionItem]
    failed: list[PanelProvisionItem]


class PanelCipher:
    def __init__(self, app_secret: str) -> None:
        digest = hashlib.sha256(f"panel-credentials:{app_secret}".encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as error:
            raise RuntimeError("Не удалось расшифровать API Token панели") from error


class PanelRepository:
    def __init__(self, database_path: str | os.PathLike[str], cipher: PanelCipher) -> None:
        path = os.fspath(database_path)
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._cipher = cipher
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS panels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    panel_url TEXT NOT NULL,
                    subscription_base_url TEXT NOT NULL,
                    api_token_encrypted TEXT NOT NULL,
                    all_inbounds INTEGER NOT NULL DEFAULT 1,
                    inbound_ids TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    source_key TEXT UNIQUE,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _row_to_record(self, row: sqlite3.Row) -> PanelRecord:
        return PanelRecord(
            id=int(row["id"]),
            name=str(row["name"]),
            panel_url=str(row["panel_url"]),
            subscription_base_url=str(row["subscription_base_url"]),
            api_token=self._cipher.decrypt(str(row["api_token_encrypted"])),
            all_inbounds=bool(row["all_inbounds"]),
            inbound_ids=tuple(int(value) for value in json.loads(row["inbound_ids"])),
            enabled=bool(row["enabled"]),
            source_key=row["source_key"],
        )

    def add(self, draft: PanelDraft) -> PanelRecord:
        now = int(time.time())
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    """
                    INSERT INTO panels (
                        name, panel_url, subscription_base_url, api_token_encrypted,
                        all_inbounds, inbound_ids, enabled, source_key, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)
                    """,
                    (
                        draft.name,
                        draft.panel_url,
                        draft.subscription_base_url,
                        self._cipher.encrypt(draft.api_token),
                        int(draft.all_inbounds),
                        json.dumps(draft.inbound_ids),
                        now,
                        now,
                    ),
                )
                panel_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            raise ValueError("Панель с таким названием уже существует.") from error
        return self.get(panel_id)

    def seed_default(self, draft: PanelDraft) -> PanelRecord:
        now = int(time.time())
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO panels (
                    name, panel_url, subscription_base_url, api_token_encrypted,
                    all_inbounds, inbound_ids, enabled, source_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'environment-default', ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    name = excluded.name,
                    panel_url = excluded.panel_url,
                    subscription_base_url = excluded.subscription_base_url,
                    api_token_encrypted = excluded.api_token_encrypted,
                    all_inbounds = excluded.all_inbounds,
                    inbound_ids = excluded.inbound_ids,
                    updated_at = excluded.updated_at
                """,
                (
                    draft.name,
                    draft.panel_url,
                    draft.subscription_base_url,
                    self._cipher.encrypt(draft.api_token),
                    int(draft.all_inbounds),
                    json.dumps(draft.inbound_ids),
                    now,
                    now,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM panels WHERE source_key = 'environment-default'"
            ).fetchone()
        if row is None:
            raise RuntimeError("Не удалось создать основную панель")
        return self._row_to_record(row)

    def get(self, panel_id: int) -> PanelRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM panels WHERE id = ?", (panel_id,)
            ).fetchone()
        if row is None:
            raise KeyError(panel_id)
        return self._row_to_record(row)

    def list_all(self) -> list[PanelRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM panels ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_active(self) -> list[PanelRecord]:
        return [panel for panel in self.list_all() if panel.enabled]

    def set_enabled(self, panel_id: int, enabled: bool) -> PanelRecord:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE panels SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), int(time.time()), panel_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(panel_id)
        return self.get(panel_id)

    def delete(self, panel_id: int) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM panels WHERE id = ? AND source_key IS NULL", (panel_id,)
            )
        return cursor.rowcount == 1

    def close(self) -> None:
        with self._lock:
            self._connection.close()


PanelProvisioner = Callable[[PanelRecord, ClientIdentity], Awaitable[str]]


class PanelManager:
    def __init__(
        self,
        repository: PanelRepository,
        base_settings: Settings,
        *,
        provision_panel: PanelProvisioner | None = None,
    ) -> None:
        self.repository = repository
        self.base_settings = base_settings
        self._provision_panel = provision_panel or self._provision_with_api

    def list_all(self) -> list[PanelRecord]:
        return self.repository.list_all()

    def list_active(self) -> list[PanelRecord]:
        return self.repository.list_active()

    def get(self, panel_id: int) -> PanelRecord:
        return self.repository.get(panel_id)

    def set_enabled(self, panel_id: int, enabled: bool) -> PanelRecord:
        return self.repository.set_enabled(panel_id, enabled)

    def delete(self, panel_id: int) -> bool:
        return self.repository.delete(panel_id)

    async def test_connection(self, draft: PanelDraft) -> list[int]:
        settings = self._settings_for(draft)
        async with self._http(settings) as http:
            return await ThreeXUIClient(settings, http).list_inbound_ids()

    async def add(self, draft: PanelDraft) -> PanelRecord:
        await self.test_connection(draft)
        return self.repository.add(draft)

    async def provision(
        self, panel_ids: list[int], identity: ClientIdentity
    ) -> PanelProvisionResult:
        panels: list[PanelRecord] = []
        unavailable: list[PanelProvisionItem] = []
        seen: set[int] = set()
        for panel_id in panel_ids:
            if panel_id in seen:
                continue
            seen.add(panel_id)
            try:
                panel = self.repository.get(panel_id)
            except KeyError:
                unavailable.append(
                    PanelProvisionItem(panel_id, f"Panel #{panel_id}", error="Панель удалена")
                )
                continue
            if panel.enabled:
                panels.append(panel)
            else:
                unavailable.append(
                    PanelProvisionItem(panel.id, panel.name, error="Панель выключена")
                )

        async def run(panel: PanelRecord) -> PanelProvisionItem:
            try:
                url = await self._provision_panel(panel, identity)
                return PanelProvisionItem(panel.id, panel.name, url=url)
            except Exception:
                logging.exception("Unable to provision subscription on panel %s", panel.id)
                return PanelProvisionItem(
                    panel.id,
                    panel.name,
                    error="Панель временно недоступна",
                )

        items = await asyncio.gather(*(run(panel) for panel in panels))
        return PanelProvisionResult(
            successful=[item for item in items if item.url],
            failed=unavailable + [item for item in items if item.error],
        )

    def _settings_for(self, panel: PanelDraft | PanelRecord) -> Settings:
        return Settings(
            panel_url=panel.panel_url,
            subscription_base_url=panel.subscription_base_url,
            inbound_ids=panel.inbound_ids,
            all_inbounds=panel.all_inbounds,
            api_mode="auto",
            api_token=panel.api_token,
            verify_tls=self.base_settings.verify_tls,
            request_timeout=self.base_settings.request_timeout,
            total_gb=self.base_settings.total_gb,
            expiry_days=self.base_settings.expiry_days,
            limit_ip=self.base_settings.limit_ip,
            flow=self.base_settings.flow,
            app_secret=self.base_settings.app_secret,
            rate_limit_per_hour=self.base_settings.rate_limit_per_hour,
            site_title=self.base_settings.site_title,
        )

    def _http(self, settings: Settings) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=settings.verify_tls,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def _provision_with_api(
        self, panel: PanelRecord, identity: ClientIdentity
    ) -> str:
        settings = self._settings_for(panel)
        async with self._http(settings) as http:
            return await ThreeXUIClient(settings, http).provision(identity)
