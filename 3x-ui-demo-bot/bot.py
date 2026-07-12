from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from panel import PanelError, ThreeXuiClient
from settings import Settings, load_settings


class DemoRequest(StatesGroup):
    preferred_name = State()


router = Router()
settings: Settings
panel: ThreeXuiClient


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def demo_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Получить демо-доступ", callback_data="demo:request")
    return builder.as_markup()


def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Статус", callback_data="admin:status")
    builder.button(text="Входящие", callback_data="admin:inbounds")
    builder.button(text="Пользователи", callback_data="admin:clients")
    builder.button(text="Группы", callback_data="admin:groups")
    builder.button(text="Настройки демо", callback_data="admin:demo")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if is_admin(message.from_user.id):
        await message.answer("Панель управления 3x-ui", reply_markup=admin_keyboard())
        return
    await message.answer("Запросите демо-доступ одной кнопкой.", reply_markup=demo_keyboard())


@router.callback_query(F.data == "demo:request")
async def request_demo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(DemoRequest.preferred_name)
    await callback.message.answer("Как к вам обращаться?")


@router.message(DemoRequest.preferred_name)
async def create_demo(message: Message, state: FSMContext) -> None:
    preferred_name = (message.text or "").strip()
    if not 2 <= len(preferred_name) <= 80:
        await message.answer("Укажите имя от 2 до 80 символов.")
        return

    await state.clear()
    try:
        email, links = await panel.create_demo_client(message.from_user, preferred_name)
    except PanelError:
        logging.exception("Unable to create demo client")
        await message.answer("Не удалось выдать демо-доступ. Попробуйте позднее.")
        return

    link_text = "\n".join(escape(link) for link in links) or "Ссылка подписки пока недоступна."
    await message.answer(f"Демо-доступ готов.\n\n{link_text}")
    notification = (
        "Новый демо-пользователь\n"
        f"Telegram: {escape(message.from_user.full_name)} (ID: <code>{message.from_user.id}</code>)\n"
        f"Обращение: {escape(preferred_name)}\n"
        f"Клиент: <code>{escape(email)}</code>"
    )
    for admin_id in settings.admin_ids:
        try:
            await message.bot.send_message(admin_id, notification)
        except Exception:
            logging.exception("Unable to notify administrator %s", admin_id)


@router.callback_query(F.data.startswith("admin:"))
async def admin_dashboard(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await callback.answer()
    action = callback.data.split(":", 1)[1]
    try:
        text = await admin_text(action)
    except PanelError:
        logging.exception("Unable to load admin dashboard: %s", action)
        text = "Панель 3x-ui не ответила. Проверьте адрес, API-токен и журнал бота."
    await callback.message.edit_text(text, reply_markup=admin_keyboard())


async def admin_text(action: str) -> str:
    if action == "status":
        value = await panel.server_status()
        return (
            "Статус сервера\n"
            f"Xray: {value.get('xray', {}).get('state', 'unknown')}\n"
            f"CPU: {value.get('cpu', 0)}%\n"
            f"Память: {value.get('mem', {}).get('current', 0)} / {value.get('mem', {}).get('total', 0)}\n"
            f"Соединения: {value.get('openConns', 0)}"
        )
    if action == "inbounds":
        values = await panel.inbounds()
        lines = [f"Входящие: {len(values)}"]
        lines.extend(f"#{item.get('id')} {item.get('remark', '-')}: {item.get('protocol', '-')}" for item in values[:30])
        return "\n".join(lines)
    if action == "clients":
        values = await panel.clients()
        enabled = sum(1 for item in values if item.get("enable"))
        return f"Пользователи: {len(values)}\nАктивные: {enabled}\n\nДля полной выборки используйте API панели."
    if action == "groups":
        values = await panel.groups()
        lines = ["Группы:"]
        lines.extend(f"{item.get('name')}: {item.get('clientCount', 0)}" for item in values)
        return "\n".join(lines)
    if action == "demo":
        return (
            "Настройки демо\n"
            f"Группа: {settings.demo_group}\n"
            f"Срок: {settings.demo_duration_hours} ч.\n"
            f"Трафик: {settings.demo_traffic_gb} ГБ\n"
            f"IP-лимит: {settings.demo_ip_limit}\n"
            f"Inbound ID: {', '.join(map(str, settings.inbound_ids))}"
        )
    return "Неизвестный раздел."


@router.message(Command("admin"))
async def admin_command(message: Message) -> None:
    if is_admin(message.from_user.id):
        await message.answer("Панель управления 3x-ui", reply_markup=admin_keyboard())


async def main() -> None:
    global settings, panel
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    panel = ThreeXuiClient(settings)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(router)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await panel.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
