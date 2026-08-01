"""Админ-панель, встроенная в клиентский бот.

Раньше это был отдельный процесс (admin_bot.py) со своим токеном, и доступ
проверялся вручную в каждом хендлере — один пропущенный `if` открывал панель
любому. Здесь проверка поднята на уровень роутера: фильтр применяется ко всем
хендлерам сразу, добавить незащищённый обработчик физически нельзя.

Роутер подключается ПЕРВЫМ и ловит только свои команды, callback-и с известными
префиксами и собственные FSM-состояния. Никаких catch-all обработчиков: иначе
админ не смог бы пользоваться ботом как обычный покупатель.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from typing import Any

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from common import esc, format_datetime_text, money_text, username_text

load_dotenv(dotenv_path=".env")
logger = logging.getLogger(__name__)

# Единственный пользователь с доступом к панели.
ADMIN_USER_ID = int(os.getenv("ADMIN_CHAT_ID", "6887373040").strip() or 6887373040)

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").strip().rstrip("/")
BACKEND_ADMIN_TOKEN = os.getenv("BACKEND_ADMIN_TOKEN", "").strip()
ORDERS_DB_PATH = os.getenv("ORDERS_DB_PATH", "orders.db").strip() or "orders.db"

BROADCAST_DELAY_SECONDS = float(os.getenv("BROADCAST_DELAY_SECONDS", "0.05").strip() or 0.05)


class AdminOnly(BaseFilter):
    """Пропускает только владельца. Применяется ко всему роутеру."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = getattr(event, "from_user", None)
        return bool(user and user.id == ADMIN_USER_ID)


router = Router(name="admin_panel")
router.message.filter(AdminOnly())
router.callback_query.filter(AdminOnly())


class AdminStates(StatesGroup):
    waiting_setting_value = State()
    waiting_user_query = State()
    waiting_balance_amount = State()
    waiting_broadcast_text = State()
    waiting_broadcast_photo = State()
    waiting_broadcast_video = State()
    waiting_ozon_cookies = State()
    waiting_secret_value = State()


# Поля мастера настройки. Значения принимаются в чат и сразу пишутся в .env,
# а сообщение с секретом удаляется.
SECRET_FIELDS: dict[str, dict[str, str]] = {
    "FRAGMENT_WALLET_MNEMONIC": {
        "title": "🔑 Сид-фраза кошелька",
        "prompt": (
            "Отправьте сид-фразу одним сообщением: 24 слова через пробел.\n\n"
            "Сообщение будет удалено сразу после проверки, а в ответ покажу "
            "адрес кошелька — сверьте его со своим."
        ),
        "danger": "1",
    },
    "FRAGMENT_ACCOUNT_ADDRESS": {
        "title": "💎 Адрес TON-кошелька",
        "prompt": "Отправьте адрес кошелька (начинается с <code>EQ</code> или <code>UQ</code>).",
    },
    "FRAGMENT_COOKIES_JSON": {
        "title": "🍪 Cookies Fragment",
        "prompt": (
            "Отправьте cookies Fragment одной строкой JSON, например:\n"
            "<code>{\"stel_ssid\":\"...\",\"stel_token\":\"...\"}</code>"
        ),
    },
    "FRAGMENT_HASH": {
        "title": "🧩 Fragment hash",
        "prompt": "Отправьте значение <code>hash</code> из адресной строки fragment.com.",
    },
    "OZON_PIN": {
        "title": "🔢 PIN Ozon",
        "prompt": "Отправьте PIN от приложения Ozon (только цифры).",
    },
    "CARD_NUMBER": {
        "title": "💳 Номер карты",
        "prompt": "Отправьте номер карты для приёма оплаты (12-19 цифр).",
    },
    "CARD_HOLDER": {
        "title": "👤 Владелец карты",
        "prompt": "Отправьте имя владельца карты, как указано в банке.",
    },
    "TONCENTER_API_KEY": {
        "title": "🔌 Ключ toncenter",
        "prompt": "Отправьте API-ключ toncenter (получить: @tonapibot).",
    },
    "SUPPORT_USERNAME": {
        "title": "🆘 Username поддержки",
        "prompt": "Отправьте username поддержки вместе с @.",
    },
}


# --------------------------------------------------------------------------
# Клавиатуры
# --------------------------------------------------------------------------


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="panel:stats"),
                InlineKeyboardButton(text="💰 Цены", callback_data="panel:prices"),
            ],
            [
                InlineKeyboardButton(text="💵 Курс доллара", callback_data="panel:usd"),
                InlineKeyboardButton(text="👤 Пользователи", callback_data="panel:users"),
            ],
            [
                InlineKeyboardButton(text="💳 Баланс", callback_data="panel:balance"),
                InlineKeyboardButton(text="🚫 Blacklist", callback_data="panel:blacklist"),
            ],
            [
                InlineKeyboardButton(text="📣 Рассылка", callback_data="panel:broadcast"),
                InlineKeyboardButton(text="🏆 Топ рефералов", callback_data="panel:toprefs"),
            ],
            [
                InlineKeyboardButton(text="🛠 Ozon", callback_data="panel:ozon"),
                InlineKeyboardButton(text="🚨 Разбор", callback_data="panel:review"),
            ],
            [InlineKeyboardButton(text="🔐 Настройка доступов", callback_data="panel:secrets")],
            [InlineKeyboardButton(text="🛒 В клиентское меню", callback_data="back_menu")],
        ]
    )


def back_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 Админ-меню", callback_data="panel:menu")]]
    )


def prices_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="50-99 ⭐", callback_data="setting:price_50_99")],
            [InlineKeyboardButton(text="100-4999 ⭐", callback_data="setting:price_100_4999")],
            [InlineKeyboardButton(text="5000+ ⭐", callback_data="setting:price_5000_plus")],
            [InlineKeyboardButton(text="Выкуп ⭐", callback_data="setting:sale_price_per_star")],
            [InlineKeyboardButton(text="🏠 Админ-меню", callback_data="panel:menu")],
        ]
    )


def broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Текст", callback_data="broadcast:text"),
                InlineKeyboardButton(text="🖼 Фото", callback_data="broadcast:photo"),
            ],
            [InlineKeyboardButton(text="🎬 Видео", callback_data="broadcast:video")],
            [InlineKeyboardButton(text="🏠 Админ-меню", callback_data="panel:menu")],
        ]
    )


def ozon_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить статус", callback_data="ozon:refresh"),
                InlineKeyboardButton(text="🍪 Обновить cookies", callback_data="ozon:cookies"),
            ],
            [InlineKeyboardButton(text="🏠 Админ-меню", callback_data="panel:menu")],
        ]
    )


def user_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Изменить баланс", callback_data=f"user:balance:{user_id}"),
                InlineKeyboardButton(text="🚫 В blacklist", callback_data=f"user:blacklist:on:{user_id}"),
            ],
            [InlineKeyboardButton(text="✅ Убрать из blacklist", callback_data=f"user:blacklist:off:{user_id}")],
            [InlineKeyboardButton(text="🏠 Админ-меню", callback_data="panel:menu")],
        ]
    )


# --------------------------------------------------------------------------
# Работа с бэкендом
# --------------------------------------------------------------------------


async def http_request(method: str, url: str, json_body: dict[str, Any] | None = None) -> tuple[int, Any]:
    timeout = aiohttp.ClientTimeout(total=180)
    headers: dict[str, str] = {}
    if url.startswith(BACKEND_URL) and BACKEND_ADMIN_TOKEN:
        headers["X-Backend-Token"] = BACKEND_ADMIN_TOKEN
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, json=json_body, headers=headers) as response:
            text = await response.text()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
            return response.status, data


async def backend_get(path: str) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}{path}")


async def backend_post(path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    return await http_request("POST", f"{BACKEND_URL}{path}", payload)


def parse_backend_error(data: Any) -> str:
    if isinstance(data, dict) and data.get("detail"):
        return esc(data["detail"])
    return esc(data)


# --------------------------------------------------------------------------
# Форматирование
# --------------------------------------------------------------------------


def format_user_card(user: dict) -> str:
    return (
        "👤 <b>Пользователь</b>\n\n"
        f"ID: <code>{esc(user.get('telegram_user_id', '—'))}</code>\n"
        f"Username: {username_text(user)}\n"
        f"Имя: <code>{esc(user.get('telegram_full_name') or '—')}</code>\n"
        f"Баланс: <code>{money_text(user.get('balance', 0))}</code> RUB\n"
        f"Реф. доступно: <code>{money_text(user.get('ref_balance_available', 0))}</code> RUB\n"
        f"Оборот рефералов: <code>{money_text(user.get('ref_total_turnover', 0))}</code> RUB\n"
        f"Blacklist: <code>{'да' if user.get('is_blacklisted') else 'нет'}</code>\n"
        f"Причина: <code>{esc(user.get('blacklist_reason') or '—')}</code>"
    )


def format_stats_text(stats: dict) -> str:
    periods = stats.get("periods") or {}
    labels = [
        ("day", "День"),
        ("three_days", "3 дня"),
        ("week", "Неделя"),
        ("month", "Месяц"),
        ("all_time", "Всё время"),
    ]
    new_users = stats.get("new_users") or {}
    statuses = stats.get("order_statuses") or {}
    lines = [
        "📊 <b>Статистика AraStars</b>\n",
        f"👥 Пользователей всего: <code>{esc(stats.get('users_total', 0))}</code>",
        f"🚫 В blacklist: <code>{esc(stats.get('blacklisted_users', 0))}</code>",
        f"🆕 Новые: день <code>{esc(new_users.get('day', 0))}</code> / "
        f"3 дня <code>{esc(new_users.get('three_days', 0))}</code> / "
        f"неделя <code>{esc(new_users.get('week', 0))}</code> / "
        f"месяц <code>{esc(new_users.get('month', 0))}</code>",
        f"🧾 Заказов сегодня: <code>{esc(stats.get('orders_today', 0))}</code>",
        f"📦 Статусы: paid <code>{esc(statuses.get('paid', 0))}</code>, "
        f"pending <code>{esc(statuses.get('payment_pending', 0))}</code>, "
        f"failed <code>{esc(statuses.get('failed', 0))}</code>, "
        f"на разборе <code>{esc(statuses.get('needs_review', 0))}</code>",
    ]
    for key, label in labels:
        item = periods.get(key) or {}
        lines.append(
            "\n"
            f"<b>{label}</b>\n"
            f"⭐ Оборот: <code>{esc(item.get('turnover_stars', 0))}</code> ⭐\n"
            f"₽ Оборот: <code>{money_text(item.get('turnover_rub', 0))}</code>\n"
            f"$ Оборот: <code>{money_text(item.get('turnover_usd', 0))}</code>\n"
            f"💎 Прибыль до реф.: <code>{money_text(item.get('profit_before_referrals', 0))}</code>\n"
            f"🤝 Реф. выплаты: <code>{money_text(item.get('referral_rewards', 0))}</code>\n"
            f"✅ Чистая прибыль: <code>{money_text(item.get('net_profit', 0))}</code>\n"
            f"🧾 Заказов: <code>{esc(item.get('orders_count', 0))}</code>, "
            f"средний чек <code>{money_text(item.get('average_order_rub', 0))}</code>"
        )
    return "\n".join(lines)


def format_settings_text(settings: dict) -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"💵 Курс доллара: <code>{esc(settings.get('usd_rate', '—'))}</code> ₽\n"
        f"50-99 ⭐: <code>{esc(settings.get('price_50_99', '—'))}</code> ₽/⭐\n"
        f"100-4999 ⭐: <code>{esc(settings.get('price_100_4999', '—'))}</code> ₽/⭐\n"
        f"5000+ ⭐: <code>{esc(settings.get('price_5000_plus', '—'))}</code> ₽/⭐\n"
        f"Выкуп ⭐: <code>{esc(settings.get('sale_price_per_star', '—'))}</code> ₽/⭐"
    )


def format_ozon_status(data: Any) -> str:
    if not isinstance(data, dict):
        return f"Ozon status error:\n<code>{esc(data)}</code>"
    health = data.get("health") if isinstance(data.get("health"), dict) else data
    last_fetch = data.get("last_fetch") if isinstance(data.get("last_fetch"), dict) else {}
    ok_icon = "✅ OK" if not health.get("last_error") else "❌ ERROR"
    lines = [
        f"<b>Ozon status: {ok_icon}</b>",
        f"Режим: <code>{esc(health.get('mode', 'unknown'))}</code>",
        f"Последний успех: <code>{esc(health.get('last_success_at') or '-')}</code>",
        f"Последняя проверка: <code>{esc(health.get('last_checked_at') or '-')}</code>",
        f"Операций: <code>{esc(health.get('last_operations_count', 0))}</code>",
        f"Ожидают оплаты: <code>{esc(data.get('pending_count', health.get('last_pending_count', 0)))}</code>",
        f"Сматчено за проход: <code>{esc(health.get('last_matched_count', 0))}</code>",
        f"Fetch: <code>{esc(last_fetch.get('mode', health.get('mode', '-')))}</code> / "
        f"<code>{esc(last_fetch.get('status', health.get('last_fetch_status', '-')))}</code>",
        f"Cookies обновлены: <code>{esc(health.get('last_cookie_refresh_at') or '-')}</code>",
        f"Cookies сохранено: <code>{esc(health.get('last_cookie_count', 0))}</code>",
        f"Keepalive: <code>{esc(health.get('last_keepalive_at') or '-')}</code>",
    ]
    if health.get("last_keepalive_error"):
        lines.append(f"Keepalive error: <code>{esc(str(health.get('last_keepalive_error'))[:700])}</code>")
    if health.get("last_error"):
        lines.append(f"Last error: <code>{esc(str(health.get('last_error'))[:900])}</code>")
    return "\n".join(lines)


def format_referral_result(referral_result: dict | None) -> str:
    if not isinstance(referral_result, dict):
        return ""
    if referral_result.get("awarded") is True:
        transactions = referral_result.get("transactions") or []
        transaction = referral_result.get("transaction") or (transactions[0] if transactions else {})
        referrer = referral_result.get("referrer") or {}
        lines = [
            "\n\n💸 <b>Реферальная прибыль начислена</b>",
            f"🤝 <b>Партнёр:</b> {username_text(referrer)}",
            f"💰 <b>Сумма заказа:</b> <code>{money_text(transaction.get('order_amount', 0))}</code> RUB",
            f"🧮 <b>Чистая база:</b> <code>{money_text(transaction.get('base_profit', 0))}</code> RUB",
            f"🪙 <b>Всего начислено:</b> <code>{money_text(referral_result.get('total_reward', 0))}</code> RUB",
            f"✅ <b>Чистая после реф. выплат:</b> "
            f"<code>{money_text(referral_result.get('final_net_profit', 0))}</code> RUB",
        ]
        for item in transactions[:5]:
            lines.append(
                f"• L{esc(item.get('chain_depth', 1))} {esc(item.get('reward_kind', 'direct'))}: "
                f"<code>{esc(item.get('percent_used', 0))}</code>% = "
                f"<code>{money_text(item.get('reward_amount', 0))}</code> RUB"
            )
        return "\n".join(lines)
    if referral_result.get("already_exists"):
        return "\n\nℹ️ <b>Реферальное начисление по этому заказу уже было учтено ранее.</b>"
    return ""


# --------------------------------------------------------------------------
# Прямые запросы к БД (только чтение, для рассылки и топа)
# --------------------------------------------------------------------------


def _read_only_connection() -> sqlite3.Connection | None:
    if not os.path.exists(ORDERS_DB_PATH):
        return None
    conn = sqlite3.connect(ORDERS_DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_broadcast_user_ids() -> list[int]:
    conn = _read_only_connection()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT telegram_user_id FROM users WHERE telegram_user_id IS NOT NULL
            UNION
            SELECT telegram_user_id FROM orders WHERE telegram_user_id IS NOT NULL
            """
        ).fetchall()
        result: list[int] = []
        for row in rows:
            try:
                result.append(int(row[0]))
            except (TypeError, ValueError):
                continue
        return result
    finally:
        conn.close()


def get_top_referrers(limit: int = 10) -> list[dict[str, Any]]:
    conn = _read_only_connection()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT
                u.telegram_user_id, u.telegram_username, u.telegram_full_name,
                u.ref_code, u.ref_balance_available, u.ref_total_paid,
                u.ref_total_turnover, u.ref_level, u.ref_percent,
                COUNT(DISTINCT inv.telegram_user_id) AS invite_count,
                COALESCE(SUM(rt.reward_amount), 0) AS total_reward
            FROM users u
            LEFT JOIN users inv ON inv.invited_by_user_id = u.telegram_user_id
            LEFT JOIN referral_transactions rt ON rt.referrer_user_id = u.telegram_user_id
            GROUP BY u.telegram_user_id
            HAVING invite_count > 0 OR u.ref_total_turnover > 0 OR total_reward > 0
            ORDER BY u.ref_total_turnover DESC, total_reward DESC, invite_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def build_top_referrers_text(limit: int = 10) -> str:
    rows = get_top_referrers(limit)
    if not rows:
        return "🏆 <b>Топ рефералов</b>\n\nПока нет реферальной статистики."

    lines = ["🏆 <b>Топ рефералов</b>"]
    for index, row in enumerate(rows, start=1):
        lines.append(
            "\n"
            f"<b>{index}. {username_text(row)}</b>\n"
            f"👥 Приглашено: <code>{esc(int(row.get('invite_count') or 0))}</code>\n"
            f"💰 Оборот: <code>{money_text(row.get('ref_total_turnover', 0))}</code> RUB\n"
            f"🪙 Начислено: <code>{money_text(row.get('total_reward', 0))}</code> RUB\n"
            f"💼 Доступно: <code>{money_text(row.get('ref_balance_available', 0))}</code> RUB\n"
            f"🏅 Уровень: <code>{esc(row.get('ref_level') or '—')}</code> / "
            f"<code>{esc(row.get('ref_percent') or 0)}%</code>"
        )
    return "\n".join(lines)


def get_orders_needing_review(limit: int = 10) -> list[dict[str, Any]]:
    """Заказы, где TON ушли, но выдача не подтверждена."""
    conn = _read_only_connection()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT id, telegram_user_id, telegram_username, recipient_username,
                   stars_amount, amount_rub, fragment_error, created_at
            FROM orders WHERE status = 'needs_review'
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def build_review_text() -> str:
    rows = get_orders_needing_review()
    if not rows:
        return "🚨 <b>Заказы на разборе</b>\n\nТаких заказов нет — всё чисто."
    lines = [
        "🚨 <b>Заказы на разборе</b>\n",
        "TON ушли в сеть, но заказ не завершён. Деньги НЕ возвращены автоматически.",
        "Проверьте кошелёк и Fragment вручную.\n",
    ]
    for row in rows:
        lines.append(
            f"🆔 <code>{esc(row.get('id'))}</code>\n"
            f"👤 {username_text(row)}\n"
            f"🎁 <code>{esc(row.get('recipient_username'))}</code> — "
            f"<code>{esc(row.get('stars_amount'))}</code>⭐ / "
            f"<code>{money_text(row.get('amount_rub'))}</code> ₽\n"
            f"⚠️ <code>{esc(str(row.get('fragment_error') or '')[:300])}</code>\n"
            f"🕒 {format_datetime_text(row.get('created_at'))}\n"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Рассылки
# --------------------------------------------------------------------------


async def _run_broadcast(message: Message, bot: Bot, kind: str, content: str, caption: str) -> None:
    user_ids = get_broadcast_user_ids()
    if not user_ids:
        await message.answer("Получатели для рассылки не найдены.", reply_markup=admin_menu_keyboard())
        return

    titles = {"text": "📣 Рассылка", "photo": "🖼 Рассылка с фото", "video": "🎬 Рассылка с видео"}
    progress = await message.answer(
        f"{titles[kind]}\n\nПолучателей: <code>{len(user_ids)}</code>\nЗапускаю..."
    )

    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            if kind == "text":
                await bot.send_message(chat_id=user_id, text=content, parse_mode=None)
            elif kind == "photo":
                await bot.send_photo(chat_id=user_id, photo=content, caption=caption or None, parse_mode=None)
            else:
                await bot.send_video(chat_id=user_id, video=content, caption=caption or None, parse_mode=None)
            sent += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            logger.exception("Ошибка рассылки пользователю %s", user_id)
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY_SECONDS)

    await progress.edit_text(
        f"✅ <b>{esc(titles[kind])} завершена</b>\n\n"
        f"Успешно: <code>{sent}</code>\n"
        f"Не доставлено: <code>{failed}</code>",
        reply_markup=admin_menu_keyboard(),
    )


# --------------------------------------------------------------------------
# Обработка заявок
# --------------------------------------------------------------------------


async def approve_order_by_id(order_id: str) -> tuple[bool, str, dict | None]:
    status_code, result = await http_request("POST", f"{BACKEND_URL}/orders/{order_id}/approve", {})
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось подтвердить заказ</b>\n\n"
            f"🆔 ID: <code>{esc(order_id)}</code>\n"
            f"<code>{parse_backend_error(result)}</code>"
        ), None

    order = result.get("order") if isinstance(result, dict) else {}
    fragment_result = result.get("fragment_result") if isinstance(result, dict) else {}
    referral_result = result.get("referral_result") if isinstance(result, dict) else None
    already_processed = bool(result.get("already_processed")) if isinstance(result, dict) else False

    req_id = "—"
    confirm_status = "—"
    total_ton = "—"
    if isinstance(fragment_result, dict):
        req_id = fragment_result.get("req_id", "—")
        total_ton = fragment_result.get("total_ton", "—")
        confirm_result = fragment_result.get("confirm_result")
        if isinstance(confirm_result, dict):
            confirm_status = "ok" if confirm_result.get("ok") else str(confirm_result)[:200]

    prefix = "✅ <b>Оплата уже была подтверждена ранее</b>" if already_processed else "✅ <b>Оплата подтверждена</b>"
    text = (
        f"{prefix}\n🚀 <b>Автовыдача выполнена</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{esc(order_id)}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{esc(order.get('recipient_username', '—'))}</code>\n"
        f"⭐ <b>Звёзд:</b> <code>{esc(order.get('stars_amount') or order.get('stars') or '—')}</code>\n"
        f"💰 <b>Сумма:</b> <code>{money_text(order.get('amount_rub') or order.get('amount') or 0)}</code> RUB\n"
        f"📦 <b>Статус:</b> <code>{esc(order.get('status', '—'))}</code>\n"
        f"💎 <b>Отправлено TON:</b> <code>{esc(total_ton)}</code>\n"
        f"🧩 <b>Fragment req_id:</b> <code>{esc(req_id)}</code>\n"
        f"📡 <b>confirmReq:</b> <code>{esc(confirm_status)}</code>"
    )
    text += format_referral_result(referral_result)
    return True, text, result if isinstance(result, dict) else None


async def reject_order_by_id(order_id: str) -> tuple[bool, str]:
    status_code, result = await http_request("POST", f"{BACKEND_URL}/orders/{order_id}/reject", {})
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось отклонить заказ</b>\n\n"
            f"🆔 ID: <code>{esc(order_id)}</code>\n"
            f"<code>{parse_backend_error(result)}</code>"
        )
    order = result.get("order") if isinstance(result, dict) else {}
    return True, (
        "❌ <b>Оплата отклонена</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{esc(order_id)}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{esc(order.get('recipient_username', '—'))}</code>\n"
        f"⭐ <b>Звёзд:</b> <code>{esc(order.get('stars_amount') or order.get('stars') or '—')}</code>"
    )


async def process_withdrawal(withdraw_id: str, action: str, processed_by: int | None) -> tuple[bool, str, dict | None]:
    endpoint = "mark-paid" if action == "paid" else "reject"
    status_code, result = await http_request(
        "POST",
        f"{BACKEND_URL}/referral/withdrawals/{withdraw_id}/{endpoint}",
        {"processed_by": processed_by},
    )
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось обработать заявку на вывод</b>\n\n"
            f"🆔 <code>{esc(withdraw_id)}</code>\n<code>{parse_backend_error(result)}</code>"
        ), None

    withdrawal = result.get("withdrawal") if isinstance(result, dict) else {}
    user = result.get("user") if isinstance(result, dict) else {}
    already = bool(result.get("already_processed")) if isinstance(result, dict) else False

    if action == "paid":
        text = (
            "✅ <b>Вывод отмечен как выплаченный</b>\n\n"
            f"🆔 <code>{esc(withdraw_id)}</code>\n"
            f"👤 {username_text(user)}\n"
            f"💸 <b>Сумма:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB\n"
            f"💼 <b>Доступно:</b> <code>{money_text(user.get('ref_balance_available', 0))}</code> RUB\n"
            f"💰 <b>Выплачено всего:</b> <code>{money_text(user.get('ref_total_paid', 0))}</code> RUB"
        )
    else:
        text = (
            "❌ <b>Заявка на вывод отклонена</b>\n\n"
            f"🆔 <code>{esc(withdraw_id)}</code>\n"
            f"👤 {username_text(user)}\n"
            f"💸 <b>Возвращено:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB\n"
            f"💼 <b>Доступно:</b> <code>{money_text(user.get('ref_balance_available', 0))}</code> RUB"
        )
    if already:
        text += "\n\nℹ️ <b>Эта заявка уже была обработана ранее.</b>"
    return True, text, result if isinstance(result, dict) else None


async def process_topup(topup_id: str, action: str, processed_by: int | None) -> tuple[bool, str, dict | None]:
    status_code, result = await http_request(
        "POST",
        f"{BACKEND_URL}/balance/topups/{topup_id}/{action}",
        {"processed_by": processed_by},
    )
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось обработать пополнение</b>\n\n"
            f"🆔 <code>{esc(topup_id)}</code>\n<code>{parse_backend_error(result)}</code>"
        ), None

    topup = result.get("topup") if isinstance(result, dict) else {}
    user = result.get("user") if isinstance(result, dict) else {}
    already = bool(result.get("already_processed")) if isinstance(result, dict) else False

    if action == "approve":
        text = (
            "✅ <b>Пополнение зачислено</b>\n\n"
            f"🆔 <code>{esc(topup_id)}</code>\n"
            f"👤 {username_text(user)}\n"
            f"💸 <b>Зачислено:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB\n"
            f"💳 <b>Баланс:</b> <code>{money_text(user.get('balance', 0))}</code> RUB"
        )
    else:
        text = (
            "❌ <b>Пополнение отклонено</b>\n\n"
            f"🆔 <code>{esc(topup_id)}</code>\n"
            f"👤 {username_text(user)}\n"
            f"💸 <b>Сумма:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB"
        )
    if already:
        text += "\n\nℹ️ <b>Эта заявка уже была обработана ранее.</b>"
    return True, text, result if isinstance(result, dict) else None


async def process_sale_access(request_id: str, action: str, processed_by: int | None) -> tuple[bool, str, dict | None]:
    status_code, result = await http_request(
        "POST",
        f"{BACKEND_URL}/sale-access/requests/{request_id}/{action}",
        {"processed_by": processed_by},
    )
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось обработать заявку на продажу</b>\n\n"
            f"🆔 <code>{esc(request_id)}</code>\n<code>{parse_backend_error(result)}</code>"
        ), None

    request = result.get("request") if isinstance(result, dict) else {}
    user = result.get("user") if isinstance(result, dict) else {}
    already = bool(result.get("already_processed")) if isinstance(result, dict) else False

    if action == "approve":
        text = (
            "✅ <b>Продажа звёзд активирована</b>\n\n"
            f"🆔 <code>{esc(request_id)}</code>\n"
            f"👤 {username_text(user)}\n"
            f"📦 <b>Тариф:</b> <code>{esc(request.get('plan_title', '—'))}</code>\n"
            f"💸 <b>Сумма:</b> <code>{money_text(request.get('amount', 0))}</code> RUB\n"
            f"⏰ <b>Доступ до:</b> "
            f"{format_datetime_text(request.get('expires_at') or user.get('sale_access_until'))}"
        )
    else:
        text = (
            "❌ <b>Заявка на продажу отклонена</b>\n\n"
            f"🆔 <code>{esc(request_id)}</code>\n"
            f"👤 {username_text(user)}\n"
            f"📦 <b>Тариф:</b> <code>{esc(request.get('plan_title', '—'))}</code>"
        )
    if already:
        text += "\n\nℹ️ <b>Эта заявка уже была обработана ранее.</b>"
    return True, text, result if isinstance(result, dict) else None


async def notify_user(bot: Bot, user_id: Any, text: str) -> None:
    if not user_id:
        return
    try:
        await bot.send_message(chat_id=int(user_id), text=text)
    except Exception:
        logger.exception("Не удалось уведомить пользователя %s", user_id)


# --------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------


@router.message(Command("admin", "panel"))
async def admin_start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 <b>Админ-панель AraStars</b>\n\nВыберите раздел:",
        reply_markup=admin_menu_keyboard(),
    )


@router.message(Command("approve"))
async def approve_handler(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n<code>/approve ORDER_ID</code>")
        return
    order_id = parts[1].strip()
    await message.answer(f"Подтверждаю заказ <code>{esc(order_id)}</code>...")
    _, text, _ = await approve_order_by_id(order_id)
    await message.answer(text)


@router.message(Command("reject"))
async def reject_handler(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n<code>/reject ORDER_ID</code>")
        return
    order_id = parts[1].strip()
    _, text = await reject_order_by_id(order_id)
    await message.answer(text)


@router.message(Command("toprefs", "top_referrals"))
async def top_referrals_handler(message: Message) -> None:
    await message.answer(build_top_referrers_text(), reply_markup=back_admin_keyboard())


@router.message(Command("review"))
async def review_handler(message: Message) -> None:
    await message.answer(build_review_text(), reply_markup=back_admin_keyboard())


@router.message(Command("ozon", "ozon_status"))
async def ozon_status_handler(message: Message) -> None:
    status, result = await backend_get("/payments/ozon/debug")
    if status >= 400:
        await message.answer(f"Ozon status failed: <code>{parse_backend_error(result)}</code>")
        return
    await message.answer(format_ozon_status(result), reply_markup=ozon_keyboard())


# --------------------------------------------------------------------------
# Навигация по панели
# --------------------------------------------------------------------------


@router.callback_query(F.data == "panel:menu")
async def panel_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "👋 <b>Админ-панель AraStars</b>\n\nВыберите раздел:",
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:stats")
async def panel_stats_callback(callback: CallbackQuery) -> None:
    status, result = await backend_get("/admin/stats")
    if status >= 400:
        await callback.answer(parse_backend_error(result)[:180], show_alert=True)
        return
    stats = result.get("stats") if isinstance(result, dict) else {}
    await callback.message.edit_text(format_stats_text(stats or {}), reply_markup=back_admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "panel:review")
async def panel_review_callback(callback: CallbackQuery) -> None:
    await callback.message.edit_text(build_review_text(), reply_markup=back_admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "panel:toprefs")
async def panel_toprefs_callback(callback: CallbackQuery) -> None:
    await callback.message.edit_text(build_top_referrers_text(), reply_markup=back_admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "panel:prices")
async def panel_prices_callback(callback: CallbackQuery) -> None:
    status, result = await backend_get("/admin/settings")
    if status >= 400:
        await callback.answer(parse_backend_error(result)[:180], show_alert=True)
        return
    await callback.message.edit_text(
        format_settings_text((result or {}).get("settings") or {}),
        reply_markup=prices_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "panel:usd")
async def panel_usd_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.waiting_setting_value)
    await state.update_data(setting_key="usd_rate")
    await callback.message.answer("💵 Введите новый курс доллара. Например: <code>92.50</code>")
    await callback.answer()


@router.callback_query(F.data.startswith("setting:"))
async def setting_select_callback(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 1)[1]
    names = {
        "price_50_99": "цену для 50-99 ⭐",
        "price_100_4999": "цену для 100-4999 ⭐",
        "price_5000_plus": "цену для 5000+ ⭐",
        "sale_price_per_star": "цену выкупа ⭐",
    }
    await state.set_state(AdminStates.waiting_setting_value)
    await state.update_data(setting_key=key)
    await callback.message.answer(f"💰 Введите {names.get(key, esc(key))} числом. Например: <code>1.32</code>")
    await callback.answer()


@router.message(AdminStates.waiting_setting_value)
async def setting_value_handler(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(",", ".").strip()
    try:
        value = float(raw)
    except ValueError:
        await message.answer("Введите число. Например: <code>1.32</code>")
        return
    if value <= 0:
        await message.answer("Значение должно быть больше нуля.")
        return

    data = await state.get_data()
    key = str(data.get("setting_key") or "")
    await state.clear()
    status, result = await backend_post("/admin/settings", {"settings": {key: value}})
    if status >= 400:
        await message.answer(
            f"❌ Не удалось сохранить: <code>{parse_backend_error(result)}</code>",
            reply_markup=admin_menu_keyboard(),
        )
        return
    price_keys = {"price_50_99", "price_100_4999", "price_5000_plus", "sale_price_per_star"}
    await message.answer(
        "✅ Настройка сохранена.\n\n" + format_settings_text((result or {}).get("settings") or {}),
        reply_markup=prices_keyboard() if key in price_keys else admin_menu_keyboard(),
    )


# --------------------------------------------------------------------------
# Пользователи
# --------------------------------------------------------------------------


@router.callback_query(F.data.in_({"panel:users", "panel:balance", "panel:blacklist"}))
async def panel_user_search_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.waiting_user_query)
    await state.update_data(user_mode=callback.data.split(":", 1)[1])
    await callback.message.answer("👤 Отправьте Telegram ID, username или имя пользователя.")
    await callback.answer()


@router.message(AdminStates.waiting_user_query)
async def user_query_handler(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip()
    await state.clear()
    status, result = await backend_get(f"/admin/users/search?q={query}&limit=5")
    if status >= 400:
        await message.answer(
            f"❌ Поиск не удался: <code>{parse_backend_error(result)}</code>",
            reply_markup=admin_menu_keyboard(),
        )
        return
    users = (result or {}).get("users") or []
    if not users:
        await message.answer("Пользователи не найдены.", reply_markup=admin_menu_keyboard())
        return
    first = users[0]
    await message.answer(format_user_card(first), reply_markup=user_actions_keyboard(int(first["telegram_user_id"])))
    if len(users) > 1:
        extra = "\n".join(
            f"• {username_text(user)} — <code>{esc(user.get('telegram_user_id'))}</code>" for user in users[1:]
        )
        await message.answer("<b>Другие совпадения:</b>\n" + extra)


@router.callback_query(F.data.startswith("user:balance:"))
async def user_balance_callback(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminStates.waiting_balance_amount)
    await state.update_data(target_user_id=user_id)
    await callback.message.answer(
        "💳 Введите сумму корректировки баланса.\n\n"
        "Пополнить: <code>500</code>\nСписать: <code>-500</code>"
    )
    await callback.answer()


@router.message(AdminStates.waiting_balance_amount)
async def balance_amount_handler(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(",", ".").strip()
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Введите число. Например: <code>500</code> или <code>-500</code>")
        return

    data = await state.get_data()
    user_id = int(data["target_user_id"])
    await state.clear()
    status, result = await backend_post(
        f"/admin/users/{user_id}/balance-adjust",
        {
            "amount": amount,
            "note": "Ручная корректировка из админ-панели",
            "processed_by": message.from_user.id,
        },
    )
    if status >= 400:
        await message.answer(
            f"❌ Не удалось изменить баланс: <code>{parse_backend_error(result)}</code>",
            reply_markup=admin_menu_keyboard(),
        )
        return
    await message.answer(
        "✅ Баланс изменён.\n\n" + format_user_card((result or {}).get("user") or {}),
        reply_markup=admin_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("user:blacklist:"))
async def user_blacklist_callback(callback: CallbackQuery) -> None:
    _, _, action, raw_user_id = callback.data.split(":", 3)
    user_id = int(raw_user_id)
    enabled = action == "on"
    status, result = await backend_post(
        f"/admin/users/{user_id}/blacklist",
        {
            "is_blacklisted": enabled,
            "reason": "Заблокирован админом" if enabled else "",
            "processed_by": callback.from_user.id,
        },
    )
    if status >= 400:
        await callback.answer(parse_backend_error(result)[:180], show_alert=True)
        return
    await callback.message.answer(
        "✅ Blacklist обновлён.\n\n" + format_user_card((result or {}).get("user") or {}),
        reply_markup=admin_menu_keyboard(),
    )
    await callback.answer()


# --------------------------------------------------------------------------
# Рассылка
# --------------------------------------------------------------------------


@router.callback_query(F.data == "panel:broadcast")
async def panel_broadcast_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("📣 <b>Рассылка</b>\n\nВыберите формат:", reply_markup=broadcast_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("broadcast:"))
async def broadcast_mode_callback(callback: CallbackQuery, state: FSMContext) -> None:
    mode = callback.data.split(":", 1)[1]
    if mode == "text":
        await state.set_state(AdminStates.waiting_broadcast_text)
        await callback.message.answer("📝 Отправьте текст рассылки.")
    elif mode == "photo":
        await state.set_state(AdminStates.waiting_broadcast_photo)
        await callback.message.answer("🖼 Отправьте фото с подписью.")
    else:
        await state.set_state(AdminStates.waiting_broadcast_video)
        await callback.message.answer("🎬 Отправьте видео с подписью.")
    await callback.answer()


@router.message(AdminStates.waiting_broadcast_text, F.text)
async def broadcast_text_state_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await _run_broadcast(message, bot, "text", message.text or "", "")


@router.message(AdminStates.waiting_broadcast_photo, F.photo)
async def broadcast_photo_state_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await _run_broadcast(message, bot, "photo", message.photo[-1].file_id, message.caption or "")


@router.message(AdminStates.waiting_broadcast_video, F.video)
async def broadcast_video_state_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    await _run_broadcast(message, bot, "video", message.video.file_id, message.caption or "")


@router.message(AdminStates.waiting_broadcast_photo)
@router.message(AdminStates.waiting_broadcast_video)
async def broadcast_wrong_media_handler(message: Message) -> None:
    await message.answer(
        "Нужно отправить медиафайл. Для отмены нажмите кнопку ниже.",
        reply_markup=back_admin_keyboard(),
    )


# --------------------------------------------------------------------------
# Ozon
# --------------------------------------------------------------------------


@router.callback_query(F.data.in_({"panel:ozon", "ozon:refresh"}))
async def panel_ozon_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    status, result = await backend_get("/payments/ozon/debug")
    if status >= 400:
        await callback.answer(parse_backend_error(result)[:180], show_alert=True)
        return
    await callback.message.edit_text(format_ozon_status(result), reply_markup=ozon_keyboard())
    await callback.answer("Обновлено")


@router.callback_query(F.data == "ozon:cookies")
async def ozon_cookies_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.waiting_ozon_cookies)
    await callback.message.edit_text(
        "🍪 Отправьте cookies Ozon следующим сообщением.\n\n"
        "Формат: <code>Cookie: name=value; ...</code>",
        reply_markup=back_admin_keyboard(),
    )
    await callback.answer()


@router.message(AdminStates.waiting_ozon_cookies, F.text)
async def ozon_cookies_state_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    raw_cookies = message.text or ""
    if not raw_cookies.strip():
        await message.answer("Пустые cookies.", reply_markup=ozon_keyboard())
        return

    progress = await message.answer("Обновляю cookies и профиль Selenium...")
    status, result = await backend_post("/admin/ozon/cookies", {"cookies": raw_cookies, "use_selenium": True})
    if status >= 400 or not isinstance(result, dict) or not result.get("ok"):
        await progress.edit_text(
            f"❌ Не удалось обновить cookies:\n<code>{parse_backend_error(result)}</code>",
            reply_markup=ozon_keyboard(),
        )
        return

    # Сообщение с секретами удаляем, чтобы cookies не висели в истории чата.
    try:
        await message.delete()
    except Exception:
        logger.debug("Не удалось удалить сообщение с cookies")

    await progress.edit_text(
        "✅ Cookies обновлены.\n"
        f"Режим: <code>{esc(result.get('mode'))}</code>\n"
        f"Cookies: <code>{esc(result.get('cookie_count'))}</code>\n"
        f"Видно операций: <code>{esc(result.get('operations_count'))}</code>",
        reply_markup=ozon_keyboard(),
    )


# --------------------------------------------------------------------------
# Мастер настройки доступов
# --------------------------------------------------------------------------


def secrets_keyboard(status: dict[str, bool]) -> InlineKeyboardMarkup:
    rows = []
    for key, meta in SECRET_FIELDS.items():
        mark = "✅" if status.get(key) else "⬜"
        rows.append([InlineKeyboardButton(text=f"{mark} {meta['title']}", callback_data=f"secret:{key}")])
    rows.append([InlineKeyboardButton(text="🏠 Админ-меню", callback_data="panel:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_secrets_menu(target: Message | CallbackQuery, edit: bool = True) -> None:
    status_code, result = await backend_get("/admin/secrets")
    if status_code >= 400:
        text = f"❌ Не удалось получить статус: <code>{parse_backend_error(result)}</code>"
        status: dict[str, bool] = {}
    else:
        status = (result or {}).get("status") or {}
        filled = sum(1 for key in SECRET_FIELDS if status.get(key))
        text = (
            "🔐 <b>Настройка доступов</b>\n\n"
            f"Заполнено: <b>{filled}</b> из <b>{len(SECRET_FIELDS)}</b>\n\n"
            "Нажмите на пункт и отправьте значение следующим сообщением — "
            "оно сразу запишется в конфигурацию, а сообщение будет удалено.\n\n"
            "✅ — задано, ⬜ — не задано. Сами значения не показываются никогда."
        )

    markup = secrets_keyboard(status)
    if isinstance(target, CallbackQuery) and edit and target.message:
        await target.message.edit_text(text, reply_markup=markup)
    else:
        message = target.message if isinstance(target, CallbackQuery) else target
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "panel:secrets")
async def panel_secrets_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render_secrets_menu(callback)
    await callback.answer()


@router.message(Command("setup"))
async def setup_command_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await render_secrets_menu(message)


@router.callback_query(F.data.startswith("secret:"))
async def secret_select_callback(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 1)[1]
    meta = SECRET_FIELDS.get(key)
    if not meta:
        await callback.answer("Неизвестный параметр", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_secret_value)
    await state.update_data(secret_key=key)

    text = f"<b>{esc(meta['title'])}</b>\n\n{meta['prompt']}"
    if meta.get("danger"):
        text += (
            "\n\n⚠️ <b>Важно.</b> Сообщения в Telegram не имеют сквозного шифрования "
            "и проходят через серверы Telegram. Я удалю ваше сообщение сразу, но "
            "если у вас есть возможность вписать фразу на сервере командой "
            "<code>python3 setup_env.py</code> — это безопаснее."
        )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="panel:secrets")]
        ]),
    )
    await callback.answer()


@router.message(AdminStates.waiting_secret_value, F.text)
async def secret_value_handler(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = str(data.get("secret_key") or "")
    meta = SECRET_FIELDS.get(key)
    await state.clear()

    if not meta:
        await message.answer("Сессия настройки истекла.", reply_markup=admin_menu_keyboard())
        return

    value = (message.text or "").strip()

    # Удаляем сообщение с секретом до любых сетевых запросов.
    deleted = True
    try:
        await message.delete()
    except Exception:
        deleted = False
        logger.warning("Не удалось удалить сообщение с секретом %s", key)

    status_code, result = await backend_post("/admin/secrets", {"secrets": {key: value}})

    if status_code >= 400:
        await message.answer(
            f"❌ <b>{esc(meta['title'])}</b> — не сохранено.\n\n"
            f"<code>{parse_backend_error(result)}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔁 Попробовать снова", callback_data=f"secret:{key}")],
                [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="panel:secrets")],
            ]),
        )
        return

    lines = [f"✅ <b>{esc(meta['title'])}</b> — сохранено."]

    wallet_address = (result or {}).get("wallet_address")
    if wallet_address:
        lines.append(
            f"\n💎 Кошелёк этой фразы:\n<code>{esc(wallet_address)}</code>\n"
            "\n⚠️ Сверьте адрес. Если он не ваш — немедленно замените фразу."
        )

    if not deleted:
        lines.append(
            "\n⚠️ Не удалось удалить ваше сообщение автоматически — удалите его вручную."
        )

    if key in {"CARD_NUMBER", "CARD_HOLDER", "PAYMENT_REQUISITES_TEXT", "SUPPORT_USERNAME"}:
        lines.append("\n♻️ Перезапустите бота, чтобы реквизиты обновились в клиентских экранах:")
        lines.append("<code>systemctl restart ara-client-bot</code>")

    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="panel:secrets")]
    ]))


@router.message(AdminStates.waiting_secret_value)
async def secret_wrong_type_handler(message: Message) -> None:
    await message.answer(
        "Отправьте значение обычным текстом.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="panel:secrets")]
        ]),
    )


# --------------------------------------------------------------------------
# Кнопки под уведомлениями о заявках
# --------------------------------------------------------------------------


@router.callback_query(F.data.startswith("admin:approve:"))
async def admin_approve_callback(callback: CallbackQuery) -> None:
    order_id = callback.data.split(":")[-1]
    await callback.answer("Подтверждаю заказ...")
    _, text, _ = await approve_order_by_id(order_id)
    if callback.message:
        await callback.message.answer(text)


@router.callback_query(F.data.startswith("admin:reject:"))
async def admin_reject_callback(callback: CallbackQuery) -> None:
    order_id = callback.data.split(":")[-1]
    await callback.answer("Отклоняю заказ...")
    _, text = await reject_order_by_id(order_id)
    if callback.message:
        await callback.message.answer(text)


@router.callback_query(F.data.startswith("admin:withdraw:"))
async def admin_withdraw_callback(callback: CallbackQuery, bot: Bot) -> None:
    _, _, action, withdraw_id = callback.data.split(":", 3)
    await callback.answer("Обрабатываю заявку...")
    _, text, result = await process_withdrawal(withdraw_id, action, callback.from_user.id)
    if callback.message:
        await callback.message.answer(text)

    if isinstance(result, dict):
        withdrawal = result.get("withdrawal") or {}
        user = result.get("user") or {}
        if action == "paid":
            user_text = (
                "✅ <b>Ваша заявка на вывод выплачена</b>\n\n"
                f"🆔 <code>{esc(withdrawal.get('id', '—'))}</code>\n"
                f"💸 <b>Сумма:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB"
            )
        else:
            user_text = (
                "❌ <b>Ваша заявка на вывод отклонена</b>\n\n"
                f"🆔 <code>{esc(withdrawal.get('id', '—'))}</code>\n"
                f"💸 <b>Сумма:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB\n"
                "Средства возвращены в доступный реферальный баланс."
            )
        await notify_user(bot, user.get("telegram_user_id"), user_text)


@router.callback_query(F.data.startswith("admin:topup:"))
async def admin_topup_callback(callback: CallbackQuery, bot: Bot) -> None:
    _, _, action, topup_id = callback.data.split(":", 3)
    await callback.answer("Обрабатываю пополнение...")
    _, text, result = await process_topup(topup_id, action, callback.from_user.id)
    if callback.message:
        await callback.message.answer(text)

    if isinstance(result, dict):
        topup = result.get("topup") or {}
        user = result.get("user") or {}
        if action == "approve":
            user_text = (
                "✅ <b>Пополнение баланса зачислено</b>\n\n"
                f"💸 <b>Сумма:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB\n"
                f"💳 <b>Текущий баланс:</b> <code>{money_text(user.get('balance', 0))}</code> RUB"
            )
        else:
            user_text = (
                "❌ <b>Пополнение баланса отклонено</b>\n\n"
                f"💸 <b>Сумма заявки:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB"
            )
        await notify_user(bot, user.get("telegram_user_id"), user_text)


@router.callback_query(F.data.startswith("admin:sale:"))
async def admin_sale_callback(callback: CallbackQuery, bot: Bot) -> None:
    _, _, action, request_id = callback.data.split(":", 3)
    await callback.answer("Обрабатываю заявку...")
    _, text, result = await process_sale_access(request_id, action, callback.from_user.id)
    if callback.message:
        await callback.message.answer(text)

    if isinstance(result, dict):
        request = result.get("request") or {}
        user = result.get("user") or {}
        if action == "approve":
            user_text = (
                "✅ <b>Доступ к продаже звёзд активирован</b>\n\n"
                f"📦 <b>Тариф:</b> <code>{esc(request.get('plan_title', '—'))}</code>\n"
                f"⭐ <b>Цена выкупа:</b> <code>{money_text(request.get('price_per_star', 0))}</code> ₽/шт\n"
                f"⏰ <b>Доступ до:</b> "
                f"{format_datetime_text(request.get('expires_at') or user.get('sale_access_until'))}"
            )
        else:
            user_text = (
                "❌ <b>Заявка на продажу звёзд отклонена</b>\n\n"
                f"📦 <b>Тариф:</b> <code>{esc(request.get('plan_title', '—'))}</code>\n"
                "Если перевод был отправлен, свяжитесь с поддержкой."
            )
        await notify_user(bot, user.get("telegram_user_id") or request.get("telegram_user_id"), user_text)
