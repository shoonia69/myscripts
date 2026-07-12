from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from settings import Settings


class PanelError(RuntimeError):
    pass


class ThreeXuiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=f"{settings.panel_base_url}/panel/api",
            headers={"Authorization": f"Bearer {settings.panel_api_token}"},
            timeout=httpx.Timeout(20.0),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PanelError(f"3x-ui request failed: {error}") from error

        if not payload.get("success"):
            raise PanelError(payload.get("msg") or "3x-ui rejected the request")
        return payload.get("obj")

    async def server_status(self) -> dict[str, Any]:
        return await self._request("GET", "/server/status")

    async def inbounds(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/inbounds/list/slim")

    async def clients(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/clients/list")

    async def groups(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/clients/groups")

    async def get_client(self, email: str) -> dict[str, Any] | None:
        try:
            return await self._request("GET", f"/clients/get/{quote(email, safe='')}")
        except PanelError as error:
            if "not found" in str(error).lower():
                return None
            raise

    async def create_demo_client(self, telegram_user: Any, preferred_name: str) -> tuple[str, list[str]]:
        email = self._client_email(telegram_user)
        existing = await self.get_client(email)
        if existing:
            return email, await self.subscription_links(email, existing)

        now_ms = int(time.time() * 1000)
        expires_ms = now_ms + self.settings.demo_duration_hours * 60 * 60 * 1000
        client = {
            "email": email,
            "totalGB": self.settings.demo_traffic_gb * 1024**3,
            "expiryTime": expires_ms,
            "tgId": telegram_user.id,
            "limitIp": self.settings.demo_ip_limit,
            "enable": True,
            "comment": self._comment(telegram_user, preferred_name),
        }
        await self._request(
            "POST",
            "/clients/add",
            json={"client": client, "inboundIds": list(self.settings.inbound_ids)},
        )
        await self._request(
            "POST",
            "/clients/groups/bulkAdd",
            json={"emails": [email], "group": self.settings.demo_group},
        )
        created = await self.get_client(email)
        return email, await self.subscription_links(email, created or {})

    async def subscription_links(self, email: str, client: dict[str, Any]) -> list[str]:
        sub_id = client.get("subId")
        if sub_id:
            result = await self._request("GET", f"/clients/subLinks/{quote(str(sub_id), safe='')}")
        else:
            result = await self._request("GET", f"/clients/links/{quote(email, safe='')}")
        if isinstance(result, list):
            return [str(link) for link in result]
        if isinstance(result, str):
            return [result]
        return []

    @staticmethod
    def _client_email(telegram_user: Any) -> str:
        raw_name = telegram_user.username or telegram_user.full_name or "telegram"
        slug = re.sub(r"[^a-z0-9]+", "-", raw_name.lower()).strip("-") or "telegram"
        return f"demo-{slug[:28]}-{telegram_user.id}@telegram"

    @staticmethod
    def _comment(telegram_user: Any, preferred_name: str) -> str:
        username = f"@{telegram_user.username}" if telegram_user.username else "without username"
        return f"Demo | Call as: {preferred_name} | TG: {telegram_user.full_name} ({username}, id={telegram_user.id})"

