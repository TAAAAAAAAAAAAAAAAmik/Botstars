"""Проверка маршрутизации: доходит ли /admin до админ-панели.

Апдейты прогоняются через настоящий Dispatcher с подменённой сессией,
поэтому проверяется вся цепочка: middleware → фильтр доступа → хендлер.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "8209449729:TESTTESTTESTTESTTESTTESTTESTTESTTES")
os.environ.setdefault("BACKEND_SERVICE_TOKEN", "test-service-token")
os.environ.setdefault("BACKEND_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("ADMIN_CHAT_ID", "6887373040")

ADMIN_ID = 6887373040
STRANGER_ID = 111222333


@pytest.fixture()
def harness(monkeypatch):
    """Диспетчер бота с перехватом исходящих вызовов Telegram."""
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession

    import admin_panel
    import bot as bot_module

    sent: list[dict] = []

    class FakeSession(BaseSession):
        async def close(self):
            return None

        async def make_request(self, bot, method, timeout=None):
            sent.append({"method": type(method).__name__, "obj": method})
            from aiogram.methods import SendMessage, SendPhoto
            # Хендлеры читают поля ответа, поэтому возвращаем настоящий Message.
            if isinstance(method, (SendMessage, SendPhoto)):
                return _make_message(1, ADMIN_ID, getattr(method, "text", None) or getattr(method, "caption", "") or "")
            return True

        async def stream_content(self, *args, **kwargs):
            yield b""

    test_bot = Bot(token=os.environ["BOT_TOKEN"], session=FakeSession())

    # Бэкенд в тестах недоступен — подменяем сетевые вызовы панели.
    async def fake_backend_get(path: str):
        if path == "/admin/secrets":
            return 200, {"ok": True, "status": {key: False for key in admin_panel.SECRET_FIELDS}}
        return 200, {"ok": True, "stats": {}, "settings": {}}

    async def fake_backend_post(path: str, payload: dict):
        return 200, {"ok": True}

    monkeypatch.setattr(admin_panel, "backend_get", fake_backend_get)
    monkeypatch.setattr(admin_panel, "backend_post", fake_backend_post)

    # Бэкенда в тестах нет — клиентские хендлеры не должны ходить в сеть.
    async def fake_http_request(method: str, url: str, json_body=None):
        if "/referral/users/register" in url:
            return 200, {"ok": True, "profile": {"user": {"ref_code": "TEST", "balance": 0}}}
        if "/settings/prices" in url:
            return 200, {"ok": True, "prices": {}}
        return 200, {"ok": True}

    monkeypatch.setattr(bot_module, "http_request", fake_http_request)

    # Антифлуд не должен мешать тестам подряд идущих апдейтов.
    for observer in (bot_module.dp.message, bot_module.dp.callback_query):
        for mw in observer.middleware:
            if isinstance(mw, bot_module.ThrottlingMiddleware):
                mw.rate_limit = 0.0

    return bot_module.dp, test_bot, sent


def _make_message(message_id: int, user_id: int, text: str):
    from aiogram.types import Chat, Message, User

    return Message(
        message_id=message_id,
        date=datetime.now(),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


def _make_update(update_id: int, user_id: int, text: str):
    from aiogram.types import Update

    return Update(update_id=update_id, message=_make_message(update_id, user_id, text))


async def _feed(dp, test_bot, update):
    return await dp.feed_update(test_bot, update)


@pytest.mark.asyncio
async def test_admin_command_reaches_panel(harness):
    """/admin от владельца открывает панель."""
    dp, test_bot, sent = harness
    await _feed(dp, test_bot, _make_update(1, ADMIN_ID, "/admin"))

    texts = [s["obj"].text for s in sent if s["method"] == "SendMessage"]
    assert texts, "бот вообще ничего не отправил в ответ на /admin"
    assert any("Админ-панель" in t for t in texts), f"панель не открылась, ответы: {texts}"


@pytest.mark.asyncio
async def test_admin_panel_has_buttons(harness):
    """В ответе есть кнопки разделов, а не просто текст."""
    dp, test_bot, sent = harness
    await _feed(dp, test_bot, _make_update(2, ADMIN_ID, "/admin"))

    markups = [s["obj"].reply_markup for s in sent if s["method"] == "SendMessage"]
    assert any(m is not None for m in markups), "клавиатура не приложена"

    buttons = [
        button.text
        for markup in markups
        if markup is not None
        for row in markup.inline_keyboard
        for button in row
    ]
    assert any("Статистика" in b for b in buttons), buttons
    assert any("Настройка доступов" in b for b in buttons), buttons


@pytest.mark.asyncio
async def test_setup_command_opens_secrets(harness):
    """/setup открывает мастер настройки доступов."""
    dp, test_bot, sent = harness
    await _feed(dp, test_bot, _make_update(3, ADMIN_ID, "/setup"))

    texts = [s["obj"].text for s in sent if s["method"] == "SendMessage"]
    assert any("Настройка доступов" in t for t in texts), texts


@pytest.mark.asyncio
async def test_stranger_cannot_open_panel(harness):
    """Посторонний не получает панель — фильтр стоит на уровне роутера."""
    dp, test_bot, sent = harness
    await _feed(dp, test_bot, _make_update(4, STRANGER_ID, "/admin"))

    texts = [s["obj"].text for s in sent if s["method"] == "SendMessage"]
    assert not any("Админ-панель" in t for t in texts), f"панель утекла постороннему: {texts}"


@pytest.mark.asyncio
async def test_stranger_cannot_open_setup(harness):
    dp, test_bot, sent = harness
    await _feed(dp, test_bot, _make_update(5, STRANGER_ID, "/setup"))

    texts = [s["obj"].text for s in sent if s["method"] == "SendMessage"]
    assert not any("Настройка доступов" in t for t in texts), texts


@pytest.mark.asyncio
async def test_admin_still_gets_client_menu(harness):
    """У админа обычный /start работает как у покупателя.

    Админ-роутер подключён первым, но ловит только свои команды — иначе
    владелец не смог бы пользоваться ботом как клиент.
    """
    dp, test_bot, sent = harness
    await _feed(dp, test_bot, _make_update(6, ADMIN_ID, "/start"))

    assert sent, "на /start админу бот не ответил"
    texts = [
        getattr(s["obj"], "text", None) or getattr(s["obj"], "caption", None) or ""
        for s in sent
        if s["method"] in {"SendMessage", "SendPhoto"}
    ]
    assert texts, "на /start не было ни текста, ни фото"
    assert not any("Админ-панель" in t for t in texts), "/start подменился админ-панелью"
