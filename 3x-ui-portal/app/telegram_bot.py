from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from html import escape
from typing import Awaitable, Callable

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
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


class BotService:
    def __init__(self, app_secret: str, provisioner: Provisioner) -> None:
        self.app_secret = app_secret
        self.provisioner = provisioner

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


def create_router(service: BotService) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.from_user or not message.from_user.username:
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

    @router.callback_query(F.data == "subscription:create")
    async def request_name(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
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


async def main() -> None:
    settings = from_env()
    token = _required_bot_token()
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
        service = BotService(settings.app_secret, panel.provision)
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


if __name__ == "__main__":
    asyncio.run(main())
