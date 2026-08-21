import json

import httpx
import pytest

from app.config import Settings
from app.identity import build_identity
from app.three_x_ui import ThreeXUIClient


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "panel_url": "https://panel.example/base/",
        "subscription_base_url": "https://sub.example/sub/",
        "inbound_ids": (7,),
        "api_mode": "auto",
        "api_token": "secret-token",
        "username": None,
        "password": None,
        "verify_tls": True,
        "request_timeout": 5.0,
        "total_gb": 0,
        "expiry_days": 0,
        "limit_ip": 0,
        "flow": "",
        "all_inbounds": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_modern_api_creates_client_and_returns_subscription() -> None:
    identity = build_identity("John Smith", "a" * 32)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer secret-token"
        if request.method == "GET":
            return httpx.Response(200, json={"success": False, "msg": "not found"})
        payload = json.loads(request.content)
        assert payload["inboundIds"] == [7]
        assert payload["client"]["email"] == identity.email
        assert payload["client"]["subId"] == identity.sub_id
        return httpx.Response(200, json={"success": True, "msg": "Client added"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ThreeXUIClient(settings(), http)
        result = await client.provision(identity)

    assert result == f"https://sub.example/sub/{identity.sub_id}"
    assert [request.url.path for request in calls] == [
        f"/base/panel/api/clients/get/{identity.email}",
        "/base/panel/api/clients/add",
    ]


@pytest.mark.asyncio
async def test_legacy_api_logs_in_and_adds_client() -> None:
    identity = build_identity("Anna", "b" * 32)
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/base/login":
            return httpx.Response(200, headers={"set-cookie": "session=ok"}, json={"success": True})
        assert request.headers.get("cookie") == "session=ok"
        if "/clients/get/" in request.url.path:
            return httpx.Response(404)
        if request.url.path == "/base/panel/api/inbounds/get/7":
            return httpx.Response(200, json={"success": True, "obj": {"id": 7, "settings": "{\"clients\":[]}"}})
        payload = json.loads(request.content)
        client_data = json.loads(payload["settings"])["clients"][0]
        assert payload["id"] == 7
        assert client_data["id"] == identity.client_id
        assert client_data["email"] == identity.email
        return httpx.Response(200, json={"success": True})

    config = settings(api_token=None, username="admin", password="pass")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ThreeXUIClient(config, http)
        result = await client.provision(identity)

    assert result.endswith(f"/{identity.sub_id}")
    assert seen_paths == [
        "/base/login",
        f"/base/panel/api/clients/get/{identity.email}",
        "/base/panel/api/inbounds/get/7",
        "/base/panel/api/inbounds/addClient",
    ]


@pytest.mark.asyncio
async def test_modern_session_auth_uses_csrf_for_create() -> None:
    identity = build_identity("Maria", "c" * 32)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/base/login":
            return httpx.Response(200, headers={"set-cookie": "session=ok"}, json={"success": True})
        if "/clients/get/" in request.url.path:
            return httpx.Response(200, json={"success": False})
        if request.url.path == "/base/csrf-token":
            return httpx.Response(200, json={"success": True, "obj": "csrf-value"})
        assert request.headers["X-CSRF-Token"] == "csrf-value"
        return httpx.Response(200, json={"success": True})

    config = settings(api_token=None, username="admin", password="pass")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ThreeXUIClient(config, http).provision(identity)

    assert result.endswith(identity.sub_id)
    assert seen[-2:] == ["/base/csrf-token", "/base/panel/api/clients/add"]


@pytest.mark.asyncio
async def test_all_inbounds_are_discovered_before_create() -> None:
    identity = build_identity("James Bond", "d" * 32)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/clients/get/" in request.url.path:
            return httpx.Response(200, json={"success": False})
        if request.url.path == "/base/panel/api/inbounds/list":
            return httpx.Response(200, json={"success": True, "obj": [{"id": 2}, {"id": 9}]})
        payload = json.loads(request.content)
        assert payload["inboundIds"] == [2, 9]
        return httpx.Response(200, json={"success": True})

    config = settings(inbound_ids=(), all_inbounds=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ThreeXUIClient(config, http).provision(identity)

    assert result.endswith(identity.sub_id)


@pytest.mark.asyncio
async def test_same_name_returns_existing_subscription_and_attaches_new_inbound() -> None:
    identity = build_identity("James Bond", "d" * 32)
    attached: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "/clients/get/" in request.url.path:
            return httpx.Response(200, json={
                "success": True,
                "obj": {"client": {"subId": "existing-sub"}, "inboundIds": [2]},
            })
        if request.url.path == "/base/panel/api/inbounds/list":
            return httpx.Response(200, json={"success": True, "obj": [{"id": 2}, {"id": 9}]})
        attached.extend(json.loads(request.content)["inboundIds"])
        return httpx.Response(200, json={"success": True})

    config = settings(inbound_ids=(), all_inbounds=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ThreeXUIClient(config, http).provision(identity)

    assert result == "https://sub.example/sub/existing-sub"
    assert attached == [9]
