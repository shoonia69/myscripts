from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from .config import Settings
from .identity import ClientIdentity


class PanelError(RuntimeError):
    pass


class ThreeXUIClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http = http
        self._authenticated = False
        self._auth_lock = asyncio.Lock()
        self._detected_mode: str | None = None
        self._csrf_token: str | None = None

    def _url(self, path: str) -> str:
        return urljoin(self.settings.panel_url.rstrip("/") + "/", path.lstrip("/"))

    async def _authenticate(self) -> None:
        if self.settings.api_token or self._authenticated:
            return
        async with self._auth_lock:
            if self._authenticated:
                return
            response = await self.http.post(
                self._url("login"),
                json={
                    "username": self.settings.username,
                    "password": self.settings.password,
                    "twoFactorCode": "",
                },
                timeout=self.settings.request_timeout,
            )
            data = self._json(response)
            if response.is_error or not data.get("success"):
                raise PanelError("Панель 3x-ui отклонила авторизацию")
            self._authenticated = True

    async def _ensure_csrf(self) -> str:
        if self._csrf_token:
            return self._csrf_token
        response = await self.http.get(
            self._url("csrf-token"), timeout=self.settings.request_timeout
        )
        data = self._json(response)
        token = data.get("obj")
        if response.is_error or not data.get("success") or not isinstance(token, str):
            raise PanelError("Не удалось получить CSRF-токен панели 3x-ui")
        self._csrf_token = token
        return token

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        await self._authenticate()
        headers = dict(kwargs.pop("headers", {}))
        if self.settings.api_token:
            headers["Authorization"] = f"Bearer {self.settings.api_token}"
        elif method.upper() not in {"GET", "HEAD", "OPTIONS"} and self._detected_mode == "modern":
            headers["X-CSRF-Token"] = await self._ensure_csrf()
        return await self.http.request(
            method,
            self._url(path),
            headers=headers,
            timeout=self.settings.request_timeout,
            **kwargs,
        )

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise PanelError("Панель 3x-ui вернула некорректный ответ") from exc
        if not isinstance(data, dict):
            raise PanelError("Панель 3x-ui вернула неожиданный ответ")
        return data

    def _client_payload(self, identity: ClientIdentity) -> dict[str, Any]:
        expiry = 0
        if self.settings.expiry_days:
            expiry = int((time.time() + self.settings.expiry_days * 86400) * 1000)
        return {
            "id": identity.client_id,
            "password": identity.password,
            "security": "auto",
            "email": identity.email,
            "limitIp": self.settings.limit_ip,
            "totalGB": self.settings.total_gb * 1024 * 1024 * 1024,
            "expiryTime": expiry,
            "enable": True,
            "tgId": 0,
            "subId": identity.sub_id,
            "flow": self.settings.flow,
            "comment": identity.comment,
            "reset": 0,
        }

    async def _resolve_inbound_ids(self) -> list[int]:
        if not self.settings.all_inbounds:
            return list(self.settings.inbound_ids)
        response = await self._request("GET", "panel/api/inbounds/list")
        data = self._json(response)
        if response.is_error or not data.get("success") or not isinstance(data.get("obj"), list):
            raise PanelError("Не удалось получить список inbound из 3x-ui")
        ids = [item.get("id") for item in data["obj"] if isinstance(item, dict)]
        result = [item for item in ids if isinstance(item, int) and item > 0]
        if not result:
            raise PanelError("В панели 3x-ui нет доступных inbound")
        return result

    def _subscription_url(self, sub_id: str) -> str:
        return self.settings.subscription_base_url.rstrip("/") + "/" + quote(sub_id, safe="")

    async def _detect_mode(self, identity: ClientIdentity) -> tuple[str, dict[str, Any] | None]:
        if self._detected_mode:
            return self._detected_mode, None
        if self.settings.api_mode != "auto":
            self._detected_mode = self.settings.api_mode
            return self._detected_mode, None
        response = await self._request("GET", f"panel/api/clients/get/{quote(identity.email, safe='')}")
        if response.status_code == 404:
            self._detected_mode = "legacy"
            return "legacy", None
        if response.is_error:
            raise PanelError(f"Панель 3x-ui недоступна (HTTP {response.status_code})")
        self._detected_mode = "modern"
        return "modern", self._json(response)

    async def provision(self, identity: ClientIdentity) -> str:
        mode, detection = await self._detect_mode(identity)
        if mode == "modern":
            sub_id = await self._provision_modern(identity, detection)
        else:
            sub_id = await self._provision_legacy(identity)
        return self._subscription_url(sub_id)

    async def _provision_modern(
        self, identity: ClientIdentity, detection: dict[str, Any] | None
    ) -> str:
        data = detection
        if data is None:
            response = await self._request("GET", f"panel/api/clients/get/{quote(identity.email, safe='')}")
            if response.is_error:
                raise PanelError(f"Не удалось проверить клиента (HTTP {response.status_code})")
            data = self._json(response)
        inbound_ids = await self._resolve_inbound_ids()
        if data.get("success"):
            obj = data.get("obj", {})
            existing = obj.get("client", {})
            attached = obj.get("inboundIds", [])
            missing = [item for item in inbound_ids if item not in attached]
            if missing:
                response = await self._request(
                    "POST",
                    f"panel/api/clients/{quote(identity.email, safe='')}/attach",
                    json={"inboundIds": missing},
                )
                result = self._json(response)
                if response.is_error or not result.get("success"):
                    raise PanelError(str(result.get("msg") or "Не удалось привязать клиента к inbound"))
            return str(existing.get("subId") or identity.sub_id)
        response = await self._request(
            "POST",
            "panel/api/clients/add",
            json={"client": self._client_payload(identity), "inboundIds": inbound_ids},
        )
        result = self._json(response)
        if response.is_error or not result.get("success"):
            raise PanelError(str(result.get("msg") or "Не удалось создать клиента в 3x-ui"))
        return identity.sub_id

    async def _provision_legacy(self, identity: ClientIdentity) -> str:
        client = self._client_payload(identity)
        inbound_ids = await self._resolve_inbound_ids()
        for inbound_id in inbound_ids:
            response = await self._request("GET", f"panel/api/inbounds/get/{inbound_id}")
            data = self._json(response)
            if response.is_error or not data.get("success"):
                raise PanelError(f"Inbound {inbound_id} не найден или недоступен")
            settings_raw = data.get("obj", {}).get("settings", "{}")
            try:
                inbound_settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
            except json.JSONDecodeError as exc:
                raise PanelError(f"Inbound {inbound_id} содержит некорректные settings") from exc
            clients = inbound_settings.get("clients", []) if isinstance(inbound_settings, dict) else []
            existing = next((item for item in clients if item.get("email") == identity.email), None)
            if existing:
                continue
            add_response = await self._request(
                "POST",
                "panel/api/inbounds/addClient",
                json={"id": inbound_id, "settings": json.dumps({"clients": [client]})},
            )
            result = self._json(add_response)
            if add_response.is_error or not result.get("success"):
                raise PanelError(str(result.get("msg") or f"Не удалось добавить клиента в inbound {inbound_id}"))
        return identity.sub_id
