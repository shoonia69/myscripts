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
    ) -> None:
        self.app_secret = app_secret
        self.provisioner = provisioner
        self.registry = registry or UserRegistry(":memory:")
        self.admin_ids = admin_ids

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


class SubscriptionRequest(StatesGroup):
    name = State()


class BroadcastRequest(StatesGroup):
    message = State()
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


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать рассылку", callback_data="admin:broadcast")]
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
    async def request_name(callback: CallbackQuery, state: FSMContext) -> None:
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
        await state.set_state(SubscriptionRequest.name)
        if callback.message:
            await callback.message.answer("Введите ваше имя на английском, например John Smith.")

    @router.message(SubscriptionRequest.name)
    async def create_subscription(message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        name = message.text or ""
        profile = TelegramProfile(
            user_id=message.from_user.id,
            username=message.from_user.username,
        )
        service.register(message.chat.id, profile)
        try:
            subscription_url = await service.create_subscription(profile, name)
        except ValueError as error:
            await message.answer(escape(str(error)))
            return
        except (PanelError, httpx.HTTPError):
            logging.exception("Unable to provision 3x-ui subscription")
            await state.clear()
            await message.answer(
                "Не удалось создать подписку. Попробуйте позднее или обратитесь к администратору."
            )
            return

        await state.clear()
        username = escape(message.from_user.username or "")
        safe_name = escape(" ".join(name.split()))
        await message.answer(
            f"Подписка готова для <b>{safe_name}</b>.\n"
            f"Telegram: <b>@{username}</b>\n\n"
            "При повторном запросе с тем же именем бот вернёт прежнюю подписку.",
            reply_markup=subscription_keyboard(subscription_url),
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
    registry = UserRegistry(os.environ.get("BOT_DB_PATH", "bot-data/bot.db"))
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


if __name__ == "__main__":
    asyncio.run(main())
