import httpx
import pytest

from app.config import Settings
from app.identity import build_telegram_identity
from app.telegram_bot import BotService, TelegramProfile
from app.three_x_ui import ThreeXUIClient


def test_telegram_identity_is_stable_and_contains_panel_metadata() -> None:
    first = build_telegram_identity("John Smith", "a" * 32, 123456, "john_smith")
    second = build_telegram_identity(" John   Smith ", "a" * 32, 123456, "john_smith")
    another_user = build_telegram_identity("John Smith", "a" * 32, 999999, "john_smith_2")

    assert first == second
    assert first != another_user
    assert first.telegram_id == 123456
    assert "@john_smith" in first.comment
    assert "John Smith" in first.comment
    assert first.email.startswith("john-smith-tg123456-")


def test_telegram_username_is_required() -> None:
    with pytest.raises(ValueError, match="username"):
        build_telegram_identity("John Smith", "a" * 32, 123456, None)


@pytest.mark.asyncio
async def test_bot_service_provisions_subscription_for_telegram_profile() -> None:
    captured = []

    async def provision(identity):
        captured.append(identity)
        return f"https://sub.example/{identity.sub_id}"

    service = BotService("a" * 32, provision)
    profile = TelegramProfile(user_id=123456, username="john_smith")

    result = await service.create_subscription(profile, "John Smith")

    assert result.startswith("https://sub.example/")
    assert captured[0].telegram_id == 123456
    assert captured[0].comment == "John Smith | Telegram: @john_smith"


@pytest.mark.asyncio
async def test_panel_payload_contains_telegram_id_and_username() -> None:
    identity = build_telegram_identity("John Smith", "a" * 32, 123456, "john_smith")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"success": False})
        payload = __import__("json").loads(request.content)["client"]
        assert payload["tgId"] == 123456
        assert payload["comment"] == "John Smith | Telegram: @john_smith"
        return httpx.Response(200, json={"success": True})

    settings = Settings(
        panel_url="https://panel.example/",
        subscription_base_url="https://sub.example/",
        inbound_ids=(1,),
        api_token="token",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await ThreeXUIClient(settings, http).provision(identity)

    assert result.endswith(identity.sub_id)
