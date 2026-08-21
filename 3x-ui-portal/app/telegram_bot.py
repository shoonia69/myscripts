from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from html import escape
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import from_env
from .identity import ClientIdentity, build_telegram_identity
from .panels import (
    PanelDraft,
    PanelManager,
    PanelProvisionItem,
    PanelProvisionResult,
    PanelRecord,
    PanelRepository,
    PanelCipher,
)
from .three_x_ui import PanelError, ThreeXUIClient

Provisioner = Callable[[ClientIdentity], Awaitable[str]]


@dataclass(frozen=True)
class TelegramProfile:
    user_id: int
    username: str | None


@dataclass(frozen=True)
class BroadcastResult:
    total: int
    sent: int
    failed: int


class UserRegistry:
    def __init__(self, database_path: str | os.PathLike[str]) -> None:
        path = os.fspath(database_path)
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_users (
                    chat_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL
                )
                """
            )

    def upsert(self, *, chat_id: int, user_id: int, username: str | None) -> None:
        now = int(time.time())
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO bot_users (chat_id, user_id, username, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    username = excluded.username,
                    last_seen = excluded.last_seen
                """,
                (chat_id, user_id, username, now, now),
            )

    def list_chat_ids(self) -> list[int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT chat_id FROM bot_users ORDER BY chat_id"
            ).fetchall()
        return [int(row[0]) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class BotService:
    def __init__(
        self,
        app_secret: str,
        provisioner: Provisioner,
        *,
        registry: UserRegistry | None = None,
        admin_ids: frozenset[int] = frozenset(),
        panel_manager: PanelManager | None = None,
    ) -> None:
        self.app_secret = app_secret
        self.provisioner = provisioner
        self.registry = registry or UserRegistry(":memory:")
        self.admin_ids = admin_ids
        self.panel_manager = panel_manager

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def register(self, chat_id: int, profile: TelegramProfile) -> None:
        self.registry.upsert(
            chat_id=chat_id,
            user_id=profile.user_id,
            username=profile.username,
        )

    async def broadcast(self, bot: Bot, admin_id: int, text: str) -> BroadcastResult:
        if not self.is_admin(admin_id):
            raise PermissionError("Administrator role is required")
        chat_ids = self.registry.list_chat_ids()
        sent = 0
        failed = 0
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode=None)
                sent += 1
            except Exception:
                failed += 1
                logging.warning("Unable to deliver broadcast to chat %s", chat_id)
            await asyncio.sleep(0.04)
        return BroadcastResult(total=len(chat_ids), sent=sent, failed=failed)

    async def create_subscription(self, profile: TelegramProfile, name: str) -> str:
        identity = build_telegram_identity(
            name,
            self.app_secret,
            profile.user_id,
            profile.username,
        )
        return await self.provisioner(identity)

    def active_panels(self) -> list[PanelRecord]:
        return self.panel_manager.list_active() if self.panel_manager else []

    def all_panels(self) -> list[PanelRecord]:
        return self.panel_manager.list_all() if self.panel_manager else []

    async def create_subscriptions(
        self,
        profile: TelegramProfile,
        name: str,
        panel_ids: list[int],
    ) -> PanelProvisionResult:
        if not self.panel_manager:
            raise RuntimeError("Panel manager is not configured")
        identity = build_telegram_identity(
            name,
            self.app_secret,
            profile.user_id,
            profile.username,
        )
        return await self.panel_manager.provision(panel_ids, identity)

    async def add_panel(self, admin_id: int, draft: PanelDraft) -> PanelRecord:
        if not self.is_admin(admin_id) or not self.panel_manager:
            raise PermissionError("Administrator role is required")
        return await self.panel_manager.add(draft)

    def toggle_panel(self, admin_id: int, panel_id: int) -> PanelRecord:
        if not self.is_admin(admin_id) or not self.panel_manager:
            raise PermissionError("Administrator role is required")
        panel = self.panel_manager.get(panel_id)
        return self.panel_manager.set_enabled(panel_id, not panel.enabled)

    def delete_panel(self, admin_id: int, panel_id: int) -> bool:
        if not self.is_admin(admin_id) or not self.panel_manager:
            raise PermissionError("Administrator role is required")
        return self.panel_manager.delete(panel_id)


class SubscriptionRequest(StatesGroup):
    panels = State()
    name = State()


class BroadcastRequest(StatesGroup):
    message = State()
    confirm = State()


class PanelSetupRequest(StatesGroup):
    name = State()
    panel_url = State()
    api_token = State()
    subscription_url = State()
    confirm = State()


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Получить подписку", callback_data="subscription:create")]
        ]
    )


def subscription_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти по ссылке подписки ↗", url=url)]
        ]
    )


def panel_selection_keyboard(
    panels: list[PanelRecord], selected: set[int]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if panel.id in selected else '⬜'} {panel.name}",
                callback_data=f"subscription:panel:{panel.id}",
            )
        ]
        for panel in panels
    ]
    rows.append(
        [
            InlineKeyboardButton(text="Продолжить", callback_data="subscription:continue"),
            InlineKeyboardButton(text="Отмена", callback_data="subscription:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscriptions_keyboard(
    items: list[PanelProvisionItem],
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"{item.panel_name} ↗", url=item.url)]
            for item in items
            if item.url
        ]
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Панели", callback_data="admin:panels")],
            [InlineKeyboardButton(text="Создать рассылку", callback_data="admin:broadcast")],
        ]
    )


def admin_panels_keyboard(panels: list[PanelRecord]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for panel in panels:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{'🟢' if panel.enabled else '⚪'} {panel.name}",
                    callback_data=f"admin:panel:toggle:{panel.id}",
                )
            ]
        )
        if panel.source_key is None:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"🗑 Удалить {panel.name}",
                        callback_data=f"admin:panel:delete:{panel.id}",
                    )
                ]
            )
    rows.extend(
        [
            [InlineKeyboardButton(text="Добавить панель", callback_data="admin:panel:add")],
            [InlineKeyboardButton(text="Назад", callback_data="admin:back")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def panel_setup_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Проверить и сохранить", callback_data="admin:panel:save"),
                InlineKeyboardButton(text="Отмена", callback_data="admin:panel:cancel"),
            ]
        ]
    )


def panel_delete_confirmation_keyboard(panel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Удалить",
                    callback_data=f"admin:panel:confirm-delete:{panel_id}",
                ),
                InlineKeyboardButton(text="Отмена", callback_data="admin:panels"),
            ]
        ]
    )


def broadcast_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отправить всем", callback_data="admin:broadcast:confirm"),
                InlineKeyboardButton(text="Отмена", callback_data="admin:broadcast:cancel"),
            ]
        ]
    )


def create_router(service: BotService) -> Router:
    router = Router()

    async def show_panels(message: Message) -> None:
        panels = service.all_panels()
        active_count = sum(1 for panel in panels if panel.enabled)
        await message.answer(
            f"Панели: {len(panels)}\nДоступно пользователям: {active_count}\n\n"
            "Нажмите на панель, чтобы включить или выключить её.",
            reply_markup=admin_panels_keyboard(panels),
        )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.from_user:
            return
        profile = TelegramProfile(
            user_id=message.from_user.id,
            username=message.from_user.username,
        )
        service.register(message.chat.id, profile)
        if not message.from_user.username:
            await message.answer(
                "Для получения подписки установите Telegram username в настройках "
                "аккаунта, затем снова отправьте /start."
            )
            return
        await message.answer(
            "Я создам персональную подписку для всех доступных подключений 3x-ui.\n\n"
            "Нажмите кнопку ниже и укажите имя на английском.",
            reply_markup=start_keyboard(),
        )

    @router.message(Command("admin"))
    async def admin(message: Message, state: FSMContext) -> None:
        if not message.from_user or not service.is_admin(message.from_user.id):
            await message.answer("Недостаточно прав.")
            return
        service.register(
            message.chat.id,
            TelegramProfile(message.from_user.id, message.from_user.username),
        )
        await state.clear()
        await message.answer(
            "Панель администратора Telegram-бота.",
            reply_markup=admin_keyboard(),
        )

    @router.callback_query(F.data == "admin:back")
    async def admin_back(callback: CallbackQuery, state: FSMContext) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await state.clear()
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "Панель администратора Telegram-бота.",
                reply_markup=admin_keyboard(),
            )

    @router.callback_query(F.data == "admin:panels")
    async def list_admin_panels(callback: CallbackQuery, state: FSMContext) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await state.clear()
        await callback.answer()
        if callback.message:
            await show_panels(callback.message)

    @router.callback_query(F.data.startswith("admin:panel:toggle:"))
    async def toggle_admin_panel(callback: CallbackQuery) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        panel_id = int(callback.data.rsplit(":", 1)[1])
        panel = service.toggle_panel(callback.from_user.id, panel_id)
        await callback.answer("Панель включена" if panel.enabled else "Панель выключена")
        if callback.message:
            await callback.message.edit_reply_markup(
                reply_markup=admin_panels_keyboard(service.all_panels())
            )

    @router.callback_query(F.data.startswith("admin:panel:delete:"))
    async def request_delete_panel(callback: CallbackQuery) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        panel_id = int(callback.data.rsplit(":", 1)[1])
        panel = service.panel_manager.get(panel_id) if service.panel_manager else None
        await callback.answer()
        if callback.message and panel:
            await callback.message.answer(
                f"Удалить панель <b>{escape(panel.name)}</b>?",
                reply_markup=panel_delete_confirmation_keyboard(panel_id),
            )

    @router.callback_query(F.data.startswith("admin:panel:confirm-delete:"))
    async def confirm_delete_panel(callback: CallbackQuery) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        panel_id = int(callback.data.rsplit(":", 1)[1])
        deleted = service.delete_panel(callback.from_user.id, panel_id)
        await callback.answer("Панель удалена" if deleted else "Основную панель удалить нельзя")
        if callback.message:
            await show_panels(callback.message)

    @router.callback_query(F.data == "admin:panel:add")
    async def request_panel_name(callback: CallbackQuery, state: FSMContext) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        await state.clear()
        await state.set_state(PanelSetupRequest.name)
        if callback.message:
            await callback.message.answer("Введите отображаемое название панели, например Germany.")

    @router.message(PanelSetupRequest.name)
    async def receive_panel_name(message: Message, state: FSMContext) -> None:
        if not message.from_user or not service.is_admin(message.from_user.id):
            await state.clear()
            return
        name = " ".join((message.text or "").split())
        if not 1 <= len(name) <= 64:
            await message.answer("Название должно содержать от 1 до 64 символов.")
            return
        await state.update_data(panel_name=name)
        await state.set_state(PanelSetupRequest.panel_url)
        await message.answer(
            "Введите базовый URL панели вместе с secret path, но без /panel/.\n"
            "Пример: https://panel.example.com/secret-path/"
        )

    @router.message(PanelSetupRequest.panel_url)
    async def receive_panel_url(message: Message, state: FSMContext) -> None:
        if not message.from_user or not service.is_admin(message.from_user.id):
            await state.clear()
            return
        value = (message.text or "").strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            await message.answer("Введите полный HTTP(S) URL панели.")
            return
        await state.update_data(panel_url=value)
        await state.set_state(PanelSetupRequest.api_token)
        await message.answer(
            "Отправьте API Token из Settings → Security → API Token. "
            "Сообщение с токеном будет сразу удалено."
        )

    @router.message(PanelSetupRequest.api_token)
    async def receive_panel_token(message: Message, state: FSMContext) -> None:
        if not message.from_user or not service.is_admin(message.from_user.id):
            await state.clear()
            return
        token = (message.text or "").strip()
        if not token:
            await message.answer("API Token не может быть пустым.")
            return
        await state.update_data(panel_api_token=token)
        try:
            await message.delete()
        except Exception:
            logging.warning("Unable to delete message containing panel API token")
        await state.set_state(PanelSetupRequest.subscription_url)
        await message.answer(
            "Введите базовый URL подписки без subId.\n"
            "Пример: https://panel.example.com/sub-path/"
        )

    @router.message(PanelSetupRequest.subscription_url)
    async def receive_subscription_url(message: Message, state: FSMContext) -> None:
        if not message.from_user or not service.is_admin(message.from_user.id):
            await state.clear()
            return
        value = (message.text or "").strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            await message.answer("Введите полный HTTP(S) URL подписки.")
            return
        await state.update_data(panel_subscription_url=value)
        await state.set_state(PanelSetupRequest.confirm)
        data = await state.get_data()
        await message.answer(
            "Проверьте настройки:\n\n"
            f"Название: <b>{escape(str(data['panel_name']))}</b>\n"
            f"Панель: <code>{escape(str(data['panel_url']))}</code>\n"
            f"Подписка: <code>{escape(value)}</code>\n"
            "Inbound: все текущие и будущие\n\n"
            "API Token сохранится в зашифрованном виде.",
            reply_markup=panel_setup_confirmation_keyboard(),
        )

    @router.callback_query(PanelSetupRequest.confirm, F.data == "admin:panel:save")
    async def save_panel(callback: CallbackQuery, state: FSMContext) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer("Проверяю подключение")
        data = await state.get_data()
        try:
            draft = PanelDraft(
                name=str(data["panel_name"]),
                panel_url=str(data["panel_url"]),
                subscription_base_url=str(data["panel_subscription_url"]),
                api_token=str(data["panel_api_token"]),
                all_inbounds=True,
                inbound_ids=(),
            )
            panel = await service.add_panel(callback.from_user.id, draft)
        except (ValueError, PanelError, httpx.HTTPError, RuntimeError) as error:
            logging.exception("Unable to add 3x-ui panel")
            await state.clear()
            if callback.message:
                await callback.message.answer(
                    "Не удалось проверить или сохранить панель: " + escape(str(error)),
                    reply_markup=admin_panels_keyboard(service.all_panels()),
                )
            return
        await state.clear()
        if callback.message:
            await callback.message.answer(
                f"Панель <b>{escape(panel.name)}</b> добавлена и доступна пользователям.",
                reply_markup=admin_panels_keyboard(service.all_panels()),
            )

    @router.callback_query(PanelSetupRequest.confirm, F.data == "admin:panel:cancel")
    async def cancel_panel_setup(callback: CallbackQuery, state: FSMContext) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await state.clear()
        await callback.answer("Добавление отменено")
        if callback.message:
            await show_panels(callback.message)

    @router.callback_query(F.data == "admin:broadcast")
    async def request_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        await state.set_state(BroadcastRequest.message)
        if callback.message:
            await callback.message.answer(
                "Напишите текст сервисного сообщения. Максимум 3500 символов."
            )

    @router.message(BroadcastRequest.message)
    async def preview_broadcast(message: Message, state: FSMContext) -> None:
        if not message.from_user or not service.is_admin(message.from_user.id):
            await state.clear()
            await message.answer("Недостаточно прав.")
            return
        text = (message.text or "").strip()
        if not 1 <= len(text) <= 3500:
            await message.answer("Сообщение должно содержать от 1 до 3500 символов.")
            return
        await state.update_data(broadcast_text=text)
        await state.set_state(BroadcastRequest.confirm)
        await message.answer(
            f"Предпросмотр рассылки:\n\n{escape(text)}\n\nОтправить всем пользователям?",
            reply_markup=broadcast_confirmation_keyboard(),
        )

    @router.callback_query(BroadcastRequest.confirm, F.data == "admin:broadcast:confirm")
    async def confirm_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer("Рассылка запущена")
        data = await state.get_data()
        text = str(data.get("broadcast_text", ""))
        result = await service.broadcast(callback.bot, callback.from_user.id, text)
        await state.clear()
        if callback.message:
            await callback.message.answer(
                "Рассылка завершена.\n"
                f"Получателей: {result.total}\n"
                f"Доставлено: {result.sent}\n"
                f"Ошибок: {result.failed}"
            )

    @router.callback_query(BroadcastRequest.confirm, F.data == "admin:broadcast:cancel")
    async def cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not service.is_admin(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await state.clear()
        await callback.answer("Рассылка отменена")
        if callback.message:
            await callback.message.answer("Рассылка отменена.", reply_markup=admin_keyboard())

    @router.callback_query(F.data == "subscription:create")
    async def request_panels(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        service.register(
            callback.message.chat.id if callback.message else callback.from_user.id,
            TelegramProfile(callback.from_user.id, callback.from_user.username),
        )
        if not callback.from_user.username:
            if callback.message:
                await callback.message.answer(
                    "Сначала установите Telegram username и снова отправьте /start."
                )
            return
        panels = service.active_panels()
        if not panels:
            if callback.message:
                await callback.message.answer(
                    "Сейчас нет доступных панелей. Обратитесь к администратору."
                )
            return
        await state.set_state(SubscriptionRequest.panels)
        await state.update_data(selected_panel_ids=[])
        if callback.message:
            await callback.message.answer(
                "Выберите одну или несколько панелей:",
                reply_markup=panel_selection_keyboard(panels, set()),
            )

    @router.callback_query(SubscriptionRequest.panels, F.data.startswith("subscription:panel:"))
    async def toggle_subscription_panel(callback: CallbackQuery, state: FSMContext) -> None:
        panel_id = int(callback.data.rsplit(":", 1)[1])
        active_panels = service.active_panels()
        active_ids = {panel.id for panel in active_panels}
        if panel_id not in active_ids:
            await callback.answer("Панель больше недоступна", show_alert=True)
            return
        data = await state.get_data()
        selected = set(int(value) for value in data.get("selected_panel_ids", []))
        if panel_id in selected:
            selected.remove(panel_id)
        else:
            selected.add(panel_id)
        await state.update_data(selected_panel_ids=sorted(selected))
        await callback.answer()
        if callback.message:
            await callback.message.edit_reply_markup(
                reply_markup=panel_selection_keyboard(active_panels, selected)
            )

    @router.callback_query(SubscriptionRequest.panels, F.data == "subscription:continue")
    async def continue_subscription(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        selected = [int(value) for value in data.get("selected_panel_ids", [])]
        if not selected:
            await callback.answer("Выберите хотя бы одну панель", show_alert=True)
            return
        await callback.answer()
        await state.set_state(SubscriptionRequest.name)
        if callback.message:
            await callback.message.answer("Введите ваше имя на английском, например John Smith.")

    @router.callback_query(SubscriptionRequest.panels, F.data == "subscription:cancel")
    async def cancel_subscription(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer("Отменено")
        if callback.message:
            await callback.message.answer("Получение подписки отменено.")

    @router.message(SubscriptionRequest.name)
    async def create_subscription(message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        data = await state.get_data()
        panel_ids = [int(value) for value in data.get("selected_panel_ids", [])]
        name = message.text or ""
        profile = TelegramProfile(
            user_id=message.from_user.id,
            username=message.from_user.username,
        )
        service.register(message.chat.id, profile)
        try:
            result = await service.create_subscriptions(profile, name, panel_ids)
        except ValueError as error:
            await message.answer(escape(str(error)))
            return
        except (PanelError, httpx.HTTPError, RuntimeError, KeyError):
            logging.exception("Unable to provision 3x-ui subscriptions")
            await state.clear()
            await message.answer(
                "Не удалось создать подписки. Попробуйте позднее или обратитесь к администратору."
            )
            return

        await state.clear()
        username = escape(message.from_user.username or "")
        safe_name = escape(" ".join(name.split()))
        lines = [
            f"Подписки готовы для <b>{safe_name}</b>.",
            f"Telegram: <b>@{username}</b>",
        ]
        if result.failed:
            lines.append("")
            lines.append("Не удалось подключить: " + ", ".join(
                escape(item.panel_name) for item in result.failed
            ))
        if not result.successful:
            lines.append("")
            lines.append("Ни одна панель не выдала подписку. Обратитесь к администратору.")
            await message.answer("\n".join(lines))
            return
        lines.append("")
        lines.append("Выберите подписку:")
        await message.answer(
            "\n".join(lines),
            reply_markup=subscriptions_keyboard(result.successful),
        )

    return router


def _required_bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    return token


def _admin_ids() -> frozenset[int]:
    raw = os.environ.get("TELEGRAM_ADMIN_IDS", "")
    try:
        values = frozenset(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as error:
        raise RuntimeError("TELEGRAM_ADMIN_IDS must contain numeric IDs") from error
    if not values or any(value <= 0 for value in values):
        raise RuntimeError("TELEGRAM_ADMIN_IDS must contain at least one positive ID")
    return values


async def main() -> None:
    settings = from_env()
    token = _required_bot_token()
    admin_ids = _admin_ids()
    database_path = os.environ.get("BOT_DB_PATH", "bot-data/bot.db")
    registry = UserRegistry(database_path)
    panel_repository = PanelRepository(database_path, PanelCipher(settings.app_secret))
    if settings.api_token:
        panel_repository.seed_default(
            PanelDraft(
                name=os.environ.get("DEFAULT_PANEL_NAME", "Основная панель"),
                panel_url=settings.panel_url,
                subscription_base_url=settings.subscription_base_url,
                api_token=settings.api_token,
                all_inbounds=settings.all_inbounds,
                inbound_ids=settings.inbound_ids,
            )
        )
    panel_manager = PanelManager(panel_repository, settings)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    async with httpx.AsyncClient(
        verify=settings.verify_tls,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    ) as http:
        panel = ThreeXUIClient(settings, http)
        service = BotService(
            settings.app_secret,
            panel.provision,
            registry=registry,
            admin_ids=admin_ids,
            panel_manager=panel_manager,
        )
        bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        dispatcher = Dispatcher(storage=MemoryStorage())
        dispatcher.include_router(create_router(service))
        try:
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
        finally:
            await bot.session.close()
            registry.close()
            panel_repository.close()


if __name__ == "__main__":
    asyncio.run(main())
