from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def web_settings() -> Settings:
    return Settings(
        panel_url="https://panel.example/",
        subscription_base_url="https://sub.example/sub/",
        inbound_ids=(1,),
        api_token="token",
        app_secret="s" * 32,
    )


def test_form_provisions_client_and_shows_subscription_link() -> None:
    received: list[str] = []

    async def provision(identity):
        received.append(identity.comment)
        return f"https://sub.example/sub/{identity.sub_id}"

    with TestClient(create_app(web_settings(), provisioner=provision)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "csrf_token" in page.text

        token = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        response = client.post("/", data={"name": "John Smith", "csrf_token": token})

    assert response.status_code == 200
    assert received == ["John Smith"]
    assert "https://sub.example/sub/" in response.text
    assert "Скопировать ссылку" in response.text


def test_invalid_csrf_is_rejected() -> None:
    async def provision(identity):
        raise AssertionError("must not provision")

    with TestClient(create_app(web_settings(), provisioner=provision)) as client:
        response = client.post("/", data={"name": "Иван", "csrf_token": "wrong"})

    assert response.status_code == 403
