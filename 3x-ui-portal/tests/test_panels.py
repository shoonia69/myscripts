import pytest

from app.config import Settings
from app.identity import build_telegram_identity
from app.panels import PanelCipher, PanelDraft, PanelManager, PanelRepository


def draft(name: str, token: str) -> PanelDraft:
    return PanelDraft(
        name=name,
        panel_url=f"https://{name.lower()}.example/secret/",
        subscription_base_url=f"https://{name.lower()}.example/sub/",
        api_token=token,
        all_inbounds=True,
        inbound_ids=(),
    )


def base_settings() -> Settings:
    return Settings(
        panel_url="https://default.example/",
        subscription_base_url="https://default.example/sub/",
        inbound_ids=(),
        all_inbounds=True,
        api_token="default-token",
        app_secret="a" * 32,
    )


def test_panel_tokens_are_encrypted_and_repository_is_persistent(tmp_path) -> None:
    database = tmp_path / "bot.db"
    repository = PanelRepository(database, PanelCipher("a" * 32))
    created = repository.add(draft("Germany", "very-secret-token"))
    repository.close()

    assert b"very-secret-token" not in database.read_bytes()

    reopened = PanelRepository(database, PanelCipher("a" * 32))
    loaded = reopened.get(created.id)
    assert loaded.name == "Germany"
    assert loaded.api_token == "very-secret-token"
    assert loaded.enabled is True
    reopened.close()


def test_default_panel_seed_is_idempotent(tmp_path) -> None:
    repository = PanelRepository(tmp_path / "bot.db", PanelCipher("a" * 32))
    first = repository.seed_default(draft("Main", "token-one"))
    second = repository.seed_default(draft("Main", "token-two"))

    assert first.id == second.id
    assert len(repository.list_all()) == 1
    assert repository.get(first.id).api_token == "token-two"
    repository.close()


def test_panels_can_be_disabled_and_deleted(tmp_path) -> None:
    repository = PanelRepository(tmp_path / "bot.db", PanelCipher("a" * 32))
    first = repository.add(draft("Germany", "token-one"))
    second = repository.add(draft("Finland", "token-two"))

    repository.set_enabled(first.id, False)
    assert [panel.id for panel in repository.list_active()] == [second.id]
    repository.delete(second.id)
    assert [panel.id for panel in repository.list_all()] == [first.id]
    repository.close()


@pytest.mark.asyncio
async def test_manager_provisions_only_selected_panels(tmp_path) -> None:
    repository = PanelRepository(tmp_path / "bot.db", PanelCipher("a" * 32))
    germany = repository.add(draft("Germany", "token-one"))
    finland = repository.add(draft("Finland", "token-two"))
    calls = []

    async def provision_panel(panel, identity):
        calls.append((panel.name, identity.telegram_id))
        return panel.subscription_base_url + identity.sub_id

    manager = PanelManager(
        repository,
        base_settings(),
        provision_panel=provision_panel,
    )
    identity = build_telegram_identity("John Smith", "a" * 32, 123456, "john_smith")

    result = await manager.provision([finland.id, germany.id], identity)

    assert [item.panel_name for item in result.successful] == ["Finland", "Germany"]
    assert result.failed == []
    assert calls == [("Finland", 123456), ("Germany", 123456)]
    repository.close()


@pytest.mark.asyncio
async def test_manager_reports_one_panel_failure_without_losing_other_results(tmp_path) -> None:
    repository = PanelRepository(tmp_path / "bot.db", PanelCipher("a" * 32))
    good = repository.add(draft("Germany", "token-one"))
    bad = repository.add(draft("Finland", "token-two"))

    async def provision_panel(panel, identity):
        if panel.id == bad.id:
            raise RuntimeError("offline")
        return panel.subscription_base_url + identity.sub_id

    manager = PanelManager(repository, base_settings(), provision_panel=provision_panel)
    identity = build_telegram_identity("John Smith", "a" * 32, 123456, "john_smith")

    result = await manager.provision([good.id, bad.id], identity)

    assert [item.panel_name for item in result.successful] == ["Germany"]
    assert [item.panel_name for item in result.failed] == ["Finland"]
    repository.close()
