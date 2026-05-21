import asyncio
import json
import logging
import os
import sqlite3
from typing import Any

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0").strip() or 0)
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").strip().rstrip("/")
BACKEND_ADMIN_TOKEN = os.getenv("BACKEND_ADMIN_TOKEN", ADMIN_BOT_TOKEN).strip()
ORDERS_DB_PATH = os.getenv("ORDERS_DB_PATH", "orders.db").strip() or "orders.db"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")
if not ADMIN_BOT_TOKEN:
    raise ValueError("ADMIN_BOT_TOKEN не найден в .env")
if not ADMIN_CHAT_ID:
    raise ValueError("ADMIN_CHAT_ID не найден в .env")

admin_bot = Bot(token=ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
client_sender_bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


class AdminStates(StatesGroup):
    waiting_setting_value = State()
    waiting_user_query = State()
    waiting_balance_amount = State()
    waiting_broadcast_text = State()
    waiting_broadcast_photo = State()
    waiting_broadcast_video = State()
    waiting_ozon_cookies = State()



def is_admin_user(user_id: int | None) -> bool:
    return bool(user_id and user_id == ADMIN_CHAT_ID)



def money_text(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return str(value)


def format_datetime_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    return raw.replace("T", " ")[:16] + " UTC"



def username_text(data: dict | None) -> str:
    if not data:
        return "без username"
    username = (data.get("telegram_username") or "").strip()
    return f"@{username}" if username else f"ID {data.get('telegram_user_id', '—')}"


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
            [InlineKeyboardButton(text="Ozon", callback_data="panel:ozon")],
        ]
    )


def back_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 Админ-меню", callback_data="panel:menu")]])


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
                InlineKeyboardButton(text="Обновить статус", callback_data="ozon:refresh"),
                InlineKeyboardButton(text="Обновить cookies", callback_data="ozon:cookies"),
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


def format_user_card(user: dict) -> str:
    return (
        "👤 <b>Пользователь</b>\n\n"
        f"ID: <code>{user.get('telegram_user_id', '—')}</code>\n"
        f"Username: {username_text(user)}\n"
        f"Имя: <code>{user.get('telegram_full_name') or '—'}</code>\n"
        f"Баланс: <code>{money_text(user.get('balance', 0))}</code> RUB\n"
        f"Реф. доступно: <code>{money_text(user.get('ref_balance_available', 0))}</code> RUB\n"
        f"Оборот рефералов: <code>{money_text(user.get('ref_total_turnover', 0))}</code> RUB\n"
        f"Blacklist: <code>{'да' if user.get('is_blacklisted') else 'нет'}</code>\n"
        f"Причина: <code>{user.get('blacklist_reason') or '—'}</code>"
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
    lines = [
        "📊 <b>Статистика AraStars</b>\n",
        f"👥 Пользователей всего: <code>{stats.get('users_total', 0)}</code>",
        f"🚫 В blacklist: <code>{stats.get('blacklisted_users', 0)}</code>",
        f"🆕 Новые: день <code>{(stats.get('new_users') or {}).get('day', 0)}</code> / 3 дня <code>{(stats.get('new_users') or {}).get('three_days', 0)}</code> / неделя <code>{(stats.get('new_users') or {}).get('week', 0)}</code> / месяц <code>{(stats.get('new_users') or {}).get('month', 0)}</code>",
        f"🧾 Заказов сегодня: <code>{stats.get('orders_today', 0)}</code>",
    ]
    statuses = stats.get("order_statuses") or {}
    lines.append(
        f"📦 Статусы: paid <code>{statuses.get('paid', 0)}</code>, pending <code>{statuses.get('payment_pending', 0)}</code>, failed <code>{statuses.get('failed', 0)}</code>"
    )
    for key, label in labels:
        item = periods.get(key) or {}
        lines.append(
            "\n"
            f"<b>{label}</b>\n"
            f"⭐ Оборот: <code>{item.get('turnover_stars', 0)}</code> ⭐\n"
            f"₽ Оборот: <code>{money_text(item.get('turnover_rub', 0))}</code>\n"
            f"$ Оборот: <code>{money_text(item.get('turnover_usd', 0))}</code>\n"
            f"💎 Прибыль до реф.: <code>{money_text(item.get('profit_before_referrals', 0))}</code>\n"
            f"🤝 Реф. выплаты: <code>{money_text(item.get('referral_rewards', 0))}</code>\n"
            f"✅ Чистая прибыль: <code>{money_text(item.get('net_profit', 0))}</code>\n"
            f"🧾 Заказов: <code>{item.get('orders_count', 0)}</code>, средний чек <code>{money_text(item.get('average_order_rub', 0))}</code>"
        )
    return "\n".join(lines)


def format_settings_text(settings: dict) -> str:
    return (
        "⚙️ <b>Настройки</b>\n\n"
        f"💵 Курс доллара: <code>{settings.get('usd_rate', '—')}</code> ₽\n"
        f"50-99 ⭐: <code>{settings.get('price_50_99', '—')}</code> ₽/⭐\n"
        f"100-4999 ⭐: <code>{settings.get('price_100_4999', '—')}</code> ₽/⭐\n"
        f"5000+ ⭐: <code>{settings.get('price_5000_plus', '—')}</code> ₽/⭐\n"
        f"Выкуп ⭐: <code>{settings.get('sale_price_per_star', '—')}</code> ₽/⭐"
    )



def get_broadcast_user_ids() -> list[int]:
    if not os.path.exists(ORDERS_DB_PATH):
        return []

    conn = sqlite3.connect(ORDERS_DB_PATH)
    try:
        cur = conn.cursor()
        rows = cur.execute(
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
            except Exception:
                continue
        return result
    finally:
        conn.close()


def get_top_referrers(limit: int = 10) -> list[dict[str, Any]]:
    if not os.path.exists(ORDERS_DB_PATH):
        return []

    conn = sqlite3.connect(ORDERS_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                u.telegram_user_id,
                u.telegram_username,
                u.telegram_full_name,
                u.ref_code,
                u.ref_balance_available,
                u.ref_total_paid,
                u.ref_total_turnover,
                u.ref_level,
                u.ref_percent,
                COUNT(inv.telegram_user_id) AS invite_count,
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
        username = username_text(row)
        lines.append(
            "\n"
            f"<b>{index}. {username}</b>\n"
            f"👥 Приглашено: <code>{int(row.get('invite_count') or 0)}</code>\n"
            f"💰 Оборот: <code>{money_text(row.get('ref_total_turnover', 0))}</code> RUB\n"
            f"🪙 Начислено: <code>{money_text(row.get('total_reward', 0))}</code> RUB\n"
            f"💼 Доступно: <code>{money_text(row.get('ref_balance_available', 0))}</code> RUB\n"
            f"🏅 Уровень: <code>{row.get('ref_level') or '—'}</code> / <code>{row.get('ref_percent') or 0}%</code>"
        )
    return "\n".join(lines)


def extract_text_after_command(message: Message, command_names: set[str]) -> str:
    raw_text = message.text or message.caption or ""
    text = raw_text.strip()
    if not text:
        return ""

    first, _, rest = text.partition(" ")
    command = first.lstrip("/").split("@", 1)[0].lower()
    if command in command_names:
        return rest.strip()

    lowered = text.lower()
    for name in command_names:
        if lowered.startswith(name):
            return text[len(name):].strip(" :-\n\t")
    return ""


async def run_text_broadcast(message: Message, broadcast_text: str) -> None:
    if not broadcast_text:
        await message.answer("Использование:\n<code>/broadcast Ваш текст рассылки</code>")
        return

    user_ids = get_broadcast_user_ids()
    if not user_ids:
        await message.answer("Получатели для рассылки не найдены.")
        return

    progress = await message.answer(f"📣 <b>Запускаю рассылку</b>\n\nПолучателей: <code>{len(user_ids)}</code>")
    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await client_sender_bot.send_message(chat_id=user_id, text=broadcast_text, parse_mode=None)
            sent += 1
            await asyncio.sleep(0.05)
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1

    await progress.edit_text(
        "✅ <b>Рассылка завершена</b>\n\n"
        f"Успешно отправлено: <code>{sent}</code>\n"
        f"Не доставлено: <code>{failed}</code>"
    )


async def run_photo_broadcast(message: Message, caption: str) -> None:
    photo_id = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    elif message.reply_to_message and message.reply_to_message.photo:
        photo_id = message.reply_to_message.photo[-1].file_id

    if not photo_id:
        await message.answer(
            "Использование:\n"
            "1. Отправьте фото с подписью <code>/broadcast_photo Текст</code>\n"
            "2. Или ответьте на фото командой <code>/broadcast_photo Текст</code>"
        )
        return

    user_ids = get_broadcast_user_ids()
    if not user_ids:
        await message.answer("Получатели для рассылки не найдены.")
        return

    progress = await message.answer(f"🖼 <b>Запускаю рассылку с фото</b>\n\nПолучателей: <code>{len(user_ids)}</code>")
    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await client_sender_bot.send_photo(chat_id=user_id, photo=photo_id, caption=caption or None, parse_mode=None)
            sent += 1
            await asyncio.sleep(0.05)
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1

    await progress.edit_text(
        "✅ <b>Рассылка с фото завершена</b>\n\n"
        f"Успешно отправлено: <code>{sent}</code>\n"
        f"Не доставлено: <code>{failed}</code>"
    )


async def run_video_broadcast(message: Message, caption: str) -> None:
    video_id = None
    if message.video:
        video_id = message.video.file_id
    elif message.reply_to_message and message.reply_to_message.video:
        video_id = message.reply_to_message.video.file_id

    if not video_id:
        await message.answer(
            "Отправьте видео с подписью или ответьте на видео после выбора рассылки видео."
        )
        return

    user_ids = get_broadcast_user_ids()
    if not user_ids:
        await message.answer("Получатели для рассылки не найдены.")
        return

    progress = await message.answer(f"🎬 <b>Запускаю рассылку с видео</b>\n\nПолучателей: <code>{len(user_ids)}</code>")
    sent = 0
    failed = 0
    for user_id in user_ids:
        try:
            await client_sender_bot.send_video(chat_id=user_id, video=video_id, caption=caption or None, parse_mode=None)
            sent += 1
            await asyncio.sleep(0.05)
        except (TelegramForbiddenError, TelegramBadRequest):
            failed += 1
        except Exception:
            failed += 1

    await progress.edit_text(
        "✅ <b>Рассылка с видео завершена</b>\n\n"
        f"Успешно отправлено: <code>{sent}</code>\n"
        f"Не доставлено: <code>{failed}</code>"
    )


async def http_request(method: str, url: str, json_body: dict[str, Any] | None = None) -> tuple[int, Any]:
    timeout = aiohttp.ClientTimeout(total=120)
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
        return str(data["detail"])
    return str(data)


def format_ozon_status(data: Any) -> str:
    if not isinstance(data, dict):
        return f"Ozon status error:\n<code>{data}</code>"
    health = data.get("health") if isinstance(data.get("health"), dict) else data
    last_fetch = data.get("last_fetch") if isinstance(data.get("last_fetch"), dict) else {}
    ok_icon = "OK" if not health.get("last_error") else "ERROR"
    lines = [
        f"<b>Ozon status: {ok_icon}</b>",
        f"Mode: <code>{health.get('mode', 'unknown')}</code>",
        f"Last success: <code>{health.get('last_success_at') or '-'}</code>",
        f"Last checked: <code>{health.get('last_checked_at') or '-'}</code>",
        f"Operations: <code>{health.get('last_operations_count', 0)}</code>",
        f"Pending: <code>{data.get('pending_count', health.get('last_pending_count', 0))}</code>",
        f"Matched last run: <code>{health.get('last_matched_count', 0)}</code>",
        f"Fetch: <code>{last_fetch.get('mode', health.get('mode', '-'))}</code> / <code>{last_fetch.get('status', health.get('last_fetch_status', '-'))}</code>",
        f"Cookie refresh: <code>{health.get('last_cookie_refresh_at') or '-'}</code>",
        f"Cookies saved: <code>{health.get('last_cookie_count', 0)}</code>",
        f"Keepalive: <code>{health.get('last_keepalive_at') or '-'}</code>",
        f"Keepalive interval: <code>{health.get('keepalive_interval_seconds', '-')}</code>s",
    ]
    if health.get("last_keepalive_error"):
        lines.append(f"Keepalive error: <code>{str(health.get('last_keepalive_error'))[:700]}</code>")
    if health.get("last_error"):
        lines.append(f"Last error: <code>{str(health.get('last_error'))[:900]}</code>")
    lines.append("\nДля обновления cookies нажмите кнопку ниже.")
    return "\n".join(lines)


async def show_ozon_status(message: Message) -> None:
    status, result = await backend_get("/payments/ozon/debug")
    if status >= 400:
        await message.answer(f"Ozon status failed: <code>{parse_backend_error(result)}</code>")
        return
    await message.answer(format_ozon_status(result), reply_markup=ozon_keyboard())


async def update_ozon_cookies(message: Message, raw_cookies: str) -> None:
    if not raw_cookies.strip():
        await message.answer("Send cookies like:\n<code>/ozon_cookies Cookie: name=value; ...</code>")
        return
    progress = await message.answer("Updating Ozon cookies and Selenium profile...")
    status, result = await backend_post("/admin/ozon/cookies", {"cookies": raw_cookies, "use_selenium": True})
    if status >= 400 or not isinstance(result, dict) or not result.get("ok"):
        await progress.edit_text(f"Ozon cookies update failed:\n<code>{parse_backend_error(result)}</code>")
        return
    await progress.edit_text(
        "Ozon cookies updated.\n"
        f"Mode: <code>{result.get('mode')}</code>\n"
        f"Cookies: <code>{result.get('cookie_count')}</code>\n"
        f"Operations visible: <code>{result.get('operations_count')}</code>",
        reply_markup=ozon_keyboard(),
    )


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
            f"✅ <b>Чистая после реф. выплат:</b> <code>{money_text(referral_result.get('final_net_profit', 0))}</code> RUB",
        ]
        for item in transactions[:5]:
            lines.append(
                f"• L{item.get('chain_depth', 1)} {item.get('reward_kind', 'direct')}: "
                f"<code>{item.get('percent_used', 0)}</code>% = <code>{money_text(item.get('reward_amount', 0))}</code> RUB"
            )
        if referral_result.get("level_up"):
            after_level = referral_result.get("level_after") or {}
            lines.append(
                f"🚀 <b>Повышение:</b> теперь {after_level.get('name', 'новый уровень')} под <code>{after_level.get('percent', '—')}</code>%"
            )
        return "\n".join(lines)
    if referral_result.get("already_exists"):
        return "\n\nℹ️ <b>Реферальное начисление по этому заказу уже было учтено ранее.</b>"
    return ""


async def approve_order_by_id(order_id: str) -> tuple[bool, str, dict | None]:
    status_code, result = await http_request("POST", f"{BACKEND_URL}/orders/{order_id}/approve", {})
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось подтвердить заказ</b>\n\n"
            f"🆔 ID: <code>{order_id}</code>\n"
            f"<code>{result}</code>"
        ), None

    order = result.get("order") if isinstance(result, dict) else {}
    fragment_result = result.get("fragment_result") if isinstance(result, dict) else {}
    referral_result = result.get("referral_result") if isinstance(result, dict) else None
    already_processed = bool(result.get("already_processed")) if isinstance(result, dict) else False

    stars = order.get("stars_amount") or order.get("stars_count") or order.get("stars") or "—"
    amount = order.get("amount_rub") or order.get("amount") or "—"
    recipient = order.get("recipient_username") or "—"
    req_id = "—"
    confirm_status = "—"

    if isinstance(fragment_result, dict):
        req_id = fragment_result.get("req_id", "—")
        confirm_result = fragment_result.get("confirm_result")
        if isinstance(confirm_result, dict):
            confirm_status = "ok" if confirm_result.get("ok") else str(confirm_result)

    prefix = "✅ <b>Оплата уже была подтверждена ранее</b>" if already_processed else "✅ <b>Оплата подтверждена</b>"
    text = (
        f"{prefix}\n"
        "🚀 <b>Автовыдача выполнена</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{recipient}</code>\n"
        f"⭐ <b>Звёзд:</b> <code>{stars}</code>\n"
        f"💰 <b>Сумма:</b> <code>{amount}</code> RUB\n"
        f"📦 <b>Статус:</b> <code>{order.get('status', '—')}</code>\n"
        f"🧩 <b>Fragment req_id:</b> <code>{req_id}</code>\n"
        f"📡 <b>Fragment confirmReq:</b> <code>{confirm_status}</code>"
    )
    text += format_referral_result(referral_result)
    return True, text, result if isinstance(result, dict) else None


async def reject_order_by_id(order_id: str) -> tuple[bool, str]:
    status_code, result = await http_request("POST", f"{BACKEND_URL}/orders/{order_id}/reject", {})
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось отклонить заказ</b>\n\n"
            f"🆔 ID: <code>{order_id}</code>\n"
            f"<code>{result}</code>"
        )
    order = result.get("order") if isinstance(result, dict) else {}
    stars = order.get("stars_amount") or order.get("stars_count") or order.get("stars") or "—"
    recipient = order.get("recipient_username") or "—"
    return True, (
        "❌ <b>Оплата отклонена</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{recipient}</code>\n"
        f"⭐ <b>Звёзд:</b> <code>{stars}</code>"
    )


async def process_withdrawal(withdraw_id: str, action: str, processed_by: int | None) -> tuple[bool, str, dict | None]:
    if action not in {"paid", "reject"}:
        return False, "❌ Неизвестное действие по выводу.", None
    endpoint = "mark-paid" if action == "paid" else "reject"
    status_code, result = await http_request(
        "POST",
        f"{BACKEND_URL}/referral/withdrawals/{withdraw_id}/{endpoint}",
        {"processed_by": processed_by},
    )
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось обработать заявку на вывод</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{withdraw_id}</code>\n"
            f"<code>{result}</code>"
        ), None

    withdrawal = result.get("withdrawal") if isinstance(result, dict) else {}
    user = result.get("user") if isinstance(result, dict) else {}
    already_processed = bool(result.get("already_processed")) if isinstance(result, dict) else False

    if action == "paid":
        text = (
            "✅ <b>Заявка на вывод отмечена как выплаченная</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{withdraw_id}</code>\n"
            f"👤 <b>Пользователь:</b> {username_text(user)}\n"
            f"💸 <b>Сумма:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB\n"
            f"💼 <b>Доступно:</b> <code>{money_text(user.get('ref_balance_available', 0))}</code> RUB\n"
            f"🔒 <b>Заморожено:</b> <code>{money_text(user.get('ref_balance_frozen', 0))}</code> RUB\n"
            f"💰 <b>Выплачено всего:</b> <code>{money_text(user.get('ref_total_paid', 0))}</code> RUB"
        )
    else:
        text = (
            "❌ <b>Заявка на вывод отклонена</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{withdraw_id}</code>\n"
            f"👤 <b>Пользователь:</b> {username_text(user)}\n"
            f"💸 <b>Сумма возвращена в доступный баланс:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB\n"
            f"💼 <b>Доступно:</b> <code>{money_text(user.get('ref_balance_available', 0))}</code> RUB\n"
            f"🔒 <b>Заморожено:</b> <code>{money_text(user.get('ref_balance_frozen', 0))}</code> RUB"
        )
    if already_processed:
        text += "\n\nℹ️ <b>Эта заявка уже была обработана ранее.</b>"
    return True, text, result if isinstance(result, dict) else None


async def process_topup(topup_id: str, action: str, processed_by: int | None) -> tuple[bool, str, dict | None]:
    if action not in {"approve", "reject"}:
        return False, "❌ Неизвестное действие по пополнению.", None
    endpoint = "approve" if action == "approve" else "reject"
    status_code, result = await http_request(
        "POST",
        f"{BACKEND_URL}/balance/topups/{topup_id}/{endpoint}",
        {"processed_by": processed_by},
    )
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось обработать заявку на пополнение</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{topup_id}</code>\n"
            f"<code>{result}</code>"
        ), None

    topup = result.get("topup") if isinstance(result, dict) else {}
    user = result.get("user") if isinstance(result, dict) else {}
    already_processed = bool(result.get("already_processed")) if isinstance(result, dict) else False

    if action == "approve":
        text = (
            "✅ <b>Пополнение зачислено на баланс</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{topup_id}</code>\n"
            f"👤 <b>Пользователь:</b> {username_text(user)}\n"
            f"💸 <b>Зачислено:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB\n"
            f"💳 <b>Текущий баланс:</b> <code>{money_text(user.get('balance', 0))}</code> RUB\n"
            f"➕ <b>Пополнено всего:</b> <code>{money_text(user.get('total_deposited', 0))}</code> RUB"
        )
    else:
        text = (
            "❌ <b>Заявка на пополнение отклонена</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{topup_id}</code>\n"
            f"👤 <b>Пользователь:</b> {username_text(user)}\n"
            f"💸 <b>Сумма заявки:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB"
        )
    if already_processed:
        text += "\n\nℹ️ <b>Эта заявка уже была обработана ранее.</b>"
    return True, text, result if isinstance(result, dict) else None


async def process_sale_access(request_id: str, action: str, processed_by: int | None) -> tuple[bool, str, dict | None]:
    if action not in {"approve", "reject"}:
        return False, "❌ Неизвестное действие по продаже звёзд.", None
    endpoint = "approve" if action == "approve" else "reject"
    status_code, result = await http_request(
        "POST",
        f"{BACKEND_URL}/sale-access/requests/{request_id}/{endpoint}",
        {"processed_by": processed_by},
    )
    if status_code >= 400:
        return False, (
            "❌ <b>Не удалось обработать заявку на продажу звёзд</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
            f"<code>{result}</code>"
        ), None

    request = result.get("request") if isinstance(result, dict) else {}
    user = result.get("user") if isinstance(result, dict) else {}
    already_processed = bool(result.get("already_processed")) if isinstance(result, dict) else False

    if action == "approve":
        text = (
            "✅ <b>Продажа звёзд активирована</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
            f"👤 <b>Пользователь:</b> {username_text(user)}\n"
            f"📦 <b>Тариф:</b> <code>{request.get('plan_title', '—')}</code>\n"
            f"💸 <b>Сумма:</b> <code>{money_text(request.get('amount', 0))}</code> RUB\n"
            f"⭐ <b>Цена выкупа:</b> <code>{money_text(request.get('price_per_star', 0))}</code> ₽/шт\n"
            f"⏰ <b>Доступ до:</b> <code>{format_datetime_text(request.get('expires_at') or user.get('sale_access_until'))}</code>"
        )
    else:
        text = (
            "❌ <b>Заявка на продажу звёзд отклонена</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
            f"👤 <b>Пользователь:</b> {username_text(user)}\n"
            f"📦 <b>Тариф:</b> <code>{request.get('plan_title', '—')}</code>\n"
            f"💸 <b>Сумма заявки:</b> <code>{money_text(request.get('amount', 0))}</code> RUB"
        )
    if already_processed:
        text += "\n\nℹ️ <b>Эта заявка уже была обработана ранее.</b>"
    return True, text, result if isinstance(result, dict) else None


async def notify_user_about_sale_access(result: dict | None, action: str) -> None:
    if not isinstance(result, dict):
        return
    user = result.get("user") or {}
    request = result.get("request") or {}
    telegram_user_id = user.get("telegram_user_id") or request.get("telegram_user_id")
    if not telegram_user_id:
        return

    if action == "approve":
        text = (
            "✅ <b>Доступ к продаже звёзд активирован</b>\n\n"
            f"📦 <b>Тариф:</b> <code>{request.get('plan_title', '—')}</code>\n"
            f"⭐ <b>Цена выкупа:</b> <code>{money_text(request.get('price_per_star', 0))}</code> ₽/шт\n"
            f"⏰ <b>Доступ до:</b> <code>{format_datetime_text(request.get('expires_at') or user.get('sale_access_until'))}</code>\n\n"
            "Теперь раздел продажи звёзд доступен в клиентском боте."
        )
    else:
        text = (
            "❌ <b>Заявка на продажу звёзд отклонена</b>\n\n"
            f"📦 <b>Тариф:</b> <code>{request.get('plan_title', '—')}</code>\n"
            f"💸 <b>Сумма:</b> <code>{money_text(request.get('amount', 0))}</code> RUB\n\n"
            "Если перевод был отправлен, свяжитесь с поддержкой для уточнения."
        )

    try:
        await client_sender_bot.send_message(chat_id=int(telegram_user_id), text=text)
    except Exception:
        logging.exception("Не удалось уведомить пользователя о продаже звёзд")


async def notify_user_about_withdrawal(result: dict | None, action: str) -> None:
    if not isinstance(result, dict):
        return
    withdrawal = result.get("withdrawal") or {}
    user = result.get("user") or {}
    user_id = user.get("telegram_user_id")
    if not user_id:
        return

    if action == "paid":
        text = (
            "✅ <b>Ваша заявка на вывод выплачена</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{withdrawal.get('id', '—')}</code>\n"
            f"💸 <b>Сумма:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB"
        )
    else:
        text = (
            "❌ <b>Ваша заявка на вывод отклонена</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{withdrawal.get('id', '—')}</code>\n"
            f"💸 <b>Сумма:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB\n"
            "Средства возвращены в доступный реферальный баланс."
        )
    try:
        await client_sender_bot.send_message(chat_id=int(user_id), text=text)
    except Exception:
        logging.exception("Не удалось уведомить пользователя о выводе")


async def notify_user_about_topup(result: dict | None, action: str) -> None:
    if not isinstance(result, dict):
        return
    topup = result.get("topup") or {}
    user = result.get("user") or {}
    user_id = user.get("telegram_user_id")
    if not user_id:
        return

    if action == "approve":
        text = (
            "✅ <b>Пополнение баланса зачислено</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{topup.get('id', '—')}</code>\n"
            f"💸 <b>Сумма:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB\n"
            f"💳 <b>Текущий баланс:</b> <code>{money_text(user.get('balance', 0))}</code> RUB"
        )
    else:
        text = (
            "❌ <b>Пополнение баланса отклонено</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{topup.get('id', '—')}</code>\n"
            f"💸 <b>Сумма заявки:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB"
        )
    try:
        await client_sender_bot.send_message(chat_id=int(user_id), text=text)
    except Exception:
        logging.exception("Не удалось уведомить пользователя о пополнении")


@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return
    await state.clear()
    await message.answer(
        "👋 <b>Админ-панель AraStars</b>\n\nВыберите раздел:",
        reply_markup=admin_menu_keyboard(),
    )


@dp.message(Command("approve"))
async def approve_handler(message: Message) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n<code>/approve ORDER_ID</code>")
        return
    order_id = parts[1].strip()
    await message.answer(f"Подтверждаю заказ <code>{order_id}</code> и запускаю автовыдачу...")
    _, text, _ = await approve_order_by_id(order_id)
    await message.answer(text)


@dp.message(Command("reject"))
async def reject_handler(message: Message) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование:\n<code>/reject ORDER_ID</code>")
        return
    order_id = parts[1].strip()
    await message.answer(f"Отклоняю заказ <code>{order_id}</code>...")
    _, text = await reject_order_by_id(order_id)
    await message.answer(text)


@dp.message(Command("top_referrals", "toprefs", "refs_top"))
async def top_referrals_handler(message: Message) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    await message.answer(build_top_referrers_text())


@dp.message(Command("broadcast"))
async def broadcast_handler(message: Message) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    broadcast_text = extract_text_after_command(message, {"broadcast"})
    await run_text_broadcast(message, broadcast_text)


@dp.message(Command("broadcast_photo", "broadcastphoto"))
async def broadcast_photo_handler(message: Message) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    caption = extract_text_after_command(message, {"broadcast_photo", "broadcastphoto"})
    await run_photo_broadcast(message, caption)


@dp.message(Command("ozon_status", "ozon"))
async def ozon_status_handler(message: Message) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        await message.answer("Нет доступа.")
        return
    await show_ozon_status(message)


@dp.message(Command("ozon_cookies", "ozoncookies"))
async def ozon_cookies_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        await message.answer("Нет доступа.")
        return
    raw_cookies = extract_text_after_command(message, {"ozon_cookies", "ozoncookies"})
    if raw_cookies:
        await update_ozon_cookies(message, raw_cookies)
        return
    await state.set_state(AdminStates.waiting_ozon_cookies)
    await message.answer("Send Ozon cookies in next message. Format: <code>Cookie: name=value; ...</code>")


@dp.message(AdminStates.waiting_ozon_cookies)
async def ozon_cookies_state_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
    await state.clear()
    await update_ozon_cookies(message, message.text or "")


@dp.callback_query(F.data == "panel:ozon")
async def panel_ozon_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    status, result = await backend_get("/payments/ozon/debug")
    if status >= 400:
        await callback.answer(parse_backend_error(result), show_alert=True)
        return
    await callback.message.edit_text(format_ozon_status(result), reply_markup=ozon_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "ozon:refresh")
async def ozon_refresh_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    status, result = await backend_get("/payments/ozon/debug")
    if status >= 400:
        await callback.answer(parse_backend_error(result), show_alert=True)
        return
    await callback.message.edit_text(format_ozon_status(result), reply_markup=ozon_keyboard())
    await callback.answer("Статус обновлен")


@dp.callback_query(F.data == "ozon:cookies")
async def ozon_cookies_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_ozon_cookies)
    await callback.message.edit_text(
        "Отправьте cookies Ozon следующим сообщением.\n\n"
        "Формат: <code>Cookie: name=value; ...</code>",
        reply_markup=back_admin_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "panel:menu")
async def panel_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("👋 <b>Админ-панель AraStars</b>\n\nВыберите раздел:", reply_markup=admin_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "panel:stats")
async def panel_stats_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    status, result = await backend_get("/admin/stats")
    if status >= 400:
        await callback.answer(parse_backend_error(result), show_alert=True)
        return
    stats = result.get("stats") if isinstance(result, dict) else {}
    await callback.message.edit_text(format_stats_text(stats or {}), reply_markup=back_admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "panel:prices")
async def panel_prices_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    status, result = await backend_get("/admin/settings")
    if status >= 400:
        await callback.answer(parse_backend_error(result), show_alert=True)
        return
    await callback.message.edit_text(format_settings_text((result or {}).get("settings") or {}), reply_markup=prices_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "panel:usd")
async def panel_usd_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_setting_value)
    await state.update_data(setting_key="usd_rate")
    await callback.message.answer("💵 Введите новый курс доллара числом. Например: <code>92.50</code>")
    await callback.answer()


@dp.callback_query(F.data.startswith("setting:"))
async def setting_select_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    key = callback.data.split(":", 1)[1]
    names = {
        "price_50_99": "цену для 50-99 ⭐",
        "price_100_4999": "цену для 100-4999 ⭐",
        "price_5000_plus": "цену для 5000+ ⭐",
        "sale_price_per_star": "цену выкупа ⭐",
    }
    await state.set_state(AdminStates.waiting_setting_value)
    await state.update_data(setting_key=key)
    await callback.message.answer(f"💰 Введите {names.get(key, key)} числом. Например: <code>1.32</code>")
    await callback.answer()


@dp.message(AdminStates.waiting_setting_value)
async def setting_value_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
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
    key = data.get("setting_key")
    status, result = await backend_post("/admin/settings", {"settings": {str(key): value}})
    await state.clear()
    if status >= 400:
        await message.answer(f"❌ Не удалось сохранить: <code>{parse_backend_error(result)}</code>")
        return
    price_setting_keys = {"price_50_99", "price_100_4999", "price_5000_plus", "sale_price_per_star"}
    reply_markup = prices_keyboard() if str(key) in price_setting_keys else admin_menu_keyboard()
    await message.answer("✅ Настройка сохранена.\n\n" + format_settings_text((result or {}).get("settings") or {}), reply_markup=reply_markup)


@dp.callback_query(F.data.in_({"panel:users", "panel:balance", "panel:blacklist"}))
async def panel_user_search_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mode = callback.data.split(":", 1)[1]
    await state.set_state(AdminStates.waiting_user_query)
    await state.update_data(user_mode=mode)
    await callback.message.answer("👤 Отправьте Telegram ID, username или имя пользователя.")
    await callback.answer()


@dp.message(AdminStates.waiting_user_query)
async def user_query_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
    query = (message.text or "").strip()
    status, result = await backend_get(f"/admin/users/search?q={query}&limit=5")
    if status >= 400:
        await state.clear()
        await message.answer(f"❌ Поиск не удался: <code>{parse_backend_error(result)}</code>", reply_markup=admin_menu_keyboard())
        return
    users = (result or {}).get("users") or []
    await state.clear()
    if not users:
        await message.answer("Пользователи не найдены.", reply_markup=admin_menu_keyboard())
        return
    first = users[0]
    await message.answer(format_user_card(first), reply_markup=user_actions_keyboard(int(first["telegram_user_id"])))
    if len(users) > 1:
        extra = "\n".join(f"• {username_text(user)} — <code>{user.get('telegram_user_id')}</code>" for user in users[1:])
        await message.answer("<b>Другие совпадения:</b>\n" + extra)


@dp.callback_query(F.data.startswith("user:balance:"))
async def user_balance_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split(":")[-1])
    await state.set_state(AdminStates.waiting_balance_amount)
    await state.update_data(target_user_id=user_id)
    await callback.message.answer(
        "💳 Введите сумму корректировки баланса.\n\n"
        "Для пополнения: <code>500</code>\n"
        "Для списания: <code>-500</code>"
    )
    await callback.answer()


@dp.message(AdminStates.waiting_balance_amount)
async def balance_amount_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
    raw = (message.text or "").replace(",", ".").strip()
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Введите число. Например: <code>500</code> или <code>-500</code>")
        return
    data = await state.get_data()
    user_id = int(data["target_user_id"])
    status, result = await backend_post(
        f"/admin/users/{user_id}/balance-adjust",
        {"amount": amount, "note": "Ручная корректировка из админ-бота", "processed_by": message.from_user.id},
    )
    await state.clear()
    if status >= 400:
        await message.answer(f"❌ Не удалось изменить баланс: <code>{parse_backend_error(result)}</code>", reply_markup=admin_menu_keyboard())
        return
    await message.answer("✅ Баланс изменён.\n\n" + format_user_card((result or {}).get("user") or {}), reply_markup=admin_menu_keyboard())


@dp.callback_query(F.data.startswith("user:blacklist:"))
async def user_blacklist_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, _, action, raw_user_id = callback.data.split(":", 3)
    user_id = int(raw_user_id)
    enabled = action == "on"
    status, result = await backend_post(
        f"/admin/users/{user_id}/blacklist",
        {"is_blacklisted": enabled, "reason": "Заблокирован админом" if enabled else "", "processed_by": callback.from_user.id},
    )
    if status >= 400:
        await callback.answer(parse_backend_error(result), show_alert=True)
        return
    await callback.message.answer("✅ Blacklist обновлён.\n\n" + format_user_card((result or {}).get("user") or {}), reply_markup=admin_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "panel:broadcast")
async def panel_broadcast_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("📣 <b>Рассылка</b>\n\nВыберите формат:", reply_markup=broadcast_keyboard())
    await callback.answer()


@dp.callback_query(F.data.startswith("broadcast:"))
async def broadcast_mode_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    mode = callback.data.split(":", 1)[1]
    if mode == "text":
        await state.set_state(AdminStates.waiting_broadcast_text)
        await callback.message.answer("📝 Отправьте текст рассылки.")
    elif mode == "photo":
        await state.set_state(AdminStates.waiting_broadcast_photo)
        await callback.message.answer("🖼 Отправьте фото с подписью. Подпись можно оставить пустой.\n\nЧтобы отправить только текст, напишите <code>пропустить</code>.")
    else:
        await state.set_state(AdminStates.waiting_broadcast_video)
        await callback.message.answer("🎬 Отправьте видео с подписью. Подпись можно оставить пустой.\n\nЧтобы отправить только текст, напишите <code>пропустить</code>.")
    await callback.answer()


@dp.message(AdminStates.waiting_broadcast_text)
async def broadcast_text_state_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
    await state.clear()
    await run_text_broadcast(message, message.text or "")


@dp.message(AdminStates.waiting_broadcast_photo, F.photo)
async def broadcast_photo_state_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
    await state.clear()
    await run_photo_broadcast(message, message.caption or "")


@dp.message(AdminStates.waiting_broadcast_photo, F.text)
async def broadcast_photo_skip_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
    if (message.text or "").strip().lower() == "пропустить":
        await state.set_state(AdminStates.waiting_broadcast_text)
        await message.answer("📝 Хорошо, отправьте текст рассылки.")
        return
    await message.answer("Отправьте фото с подписью или напишите <code>пропустить</code>.")


@dp.message(AdminStates.waiting_broadcast_video, F.video)
async def broadcast_video_state_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
    await state.clear()
    await run_video_broadcast(message, message.caption or "")


@dp.message(AdminStates.waiting_broadcast_video, F.text)
async def broadcast_video_skip_handler(message: Message, state: FSMContext) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return
    if (message.text or "").strip().lower() == "пропустить":
        await state.set_state(AdminStates.waiting_broadcast_text)
        await message.answer("📝 Хорошо, отправьте текст рассылки.")
        return
    await message.answer("Отправьте видео с подписью или напишите <code>пропустить</code>.")


@dp.callback_query(F.data == "panel:toprefs")
async def panel_toprefs_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(build_top_referrers_text(), reply_markup=back_admin_keyboard())
    await callback.answer()


@dp.message(F.photo)
async def photo_command_handler(message: Message) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return

    caption = (message.caption or "").strip()
    lowered = caption.lower()
    if lowered.startswith("/broadcast_photo") or lowered.startswith("/broadcastphoto") or lowered.startswith("рассылка с фото"):
        broadcast_caption = extract_text_after_command(message, {"broadcast_photo", "broadcastphoto", "рассылка с фото"})
        await run_photo_broadcast(message, broadcast_caption)


@dp.message(F.text)
async def russian_admin_text_handler(message: Message) -> None:
    if not is_admin_user(message.from_user.id if message.from_user else None):
        return

    text = (message.text or "").strip()
    lowered = text.lower()
    if lowered in {"топ рефералов", "топ рефералы", "топ партнеров", "топ партнёров"}:
        await message.answer(build_top_referrers_text())
        return

    if lowered.startswith("рассылка с фото"):
        caption = extract_text_after_command(message, {"рассылка с фото"})
        await run_photo_broadcast(message, caption)
        return

    if lowered.startswith("рассылка"):
        broadcast_text = extract_text_after_command(message, {"рассылка"})
        await run_text_broadcast(message, broadcast_text)
        return


@dp.callback_query(F.data.startswith("admin:approve:"))
async def admin_approve_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    order_id = callback.data.split(":")[-1]
    await callback.answer("Подтверждаю заказ...")
    _, text, _ = await approve_order_by_id(order_id)
    if callback.message:
        await callback.message.answer(text)


@dp.callback_query(F.data.startswith("admin:reject:"))
async def admin_reject_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    order_id = callback.data.split(":")[-1]
    await callback.answer("Отклоняю заказ...")
    _, text = await reject_order_by_id(order_id)
    if callback.message:
        await callback.message.answer(text)


@dp.callback_query(F.data.startswith("admin:withdraw:paid:"))
async def admin_withdraw_paid_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    withdraw_id = callback.data.split(":")[-1]
    await callback.answer("Отмечаю заявку как выплаченную...")
    _, text, result = await process_withdrawal(withdraw_id, action="paid", processed_by=callback.from_user.id if callback.from_user else None)
    if callback.message:
        await callback.message.answer(text)
    await notify_user_about_withdrawal(result, action="paid")


@dp.callback_query(F.data.startswith("admin:withdraw:reject:"))
async def admin_withdraw_reject_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    withdraw_id = callback.data.split(":")[-1]
    await callback.answer("Отклоняю заявку...")
    _, text, result = await process_withdrawal(withdraw_id, action="reject", processed_by=callback.from_user.id if callback.from_user else None)
    if callback.message:
        await callback.message.answer(text)
    await notify_user_about_withdrawal(result, action="reject")


@dp.callback_query(F.data.startswith("admin:topup:approve:"))
async def admin_topup_approve_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    topup_id = callback.data.split(":")[-1]
    await callback.answer("Зачисляю баланс...")
    _, text, result = await process_topup(topup_id, action="approve", processed_by=callback.from_user.id if callback.from_user else None)
    if callback.message:
        await callback.message.answer(text)
    await notify_user_about_topup(result, action="approve")


@dp.callback_query(F.data.startswith("admin:topup:reject:"))
async def admin_topup_reject_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    topup_id = callback.data.split(":")[-1]
    await callback.answer("Отклоняю пополнение...")
    _, text, result = await process_topup(topup_id, action="reject", processed_by=callback.from_user.id if callback.from_user else None)
    if callback.message:
        await callback.message.answer(text)
    await notify_user_about_topup(result, action="reject")


@dp.callback_query(F.data.startswith("admin:sale:approve:"))
async def admin_sale_approve_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    request_id = callback.data.split(":")[-1]
    await callback.answer("Активирую продажу звёзд...")
    _, text, result = await process_sale_access(request_id, action="approve", processed_by=callback.from_user.id if callback.from_user else None)
    if callback.message:
        await callback.message.answer(text)
    await notify_user_about_sale_access(result, action="approve")


@dp.callback_query(F.data.startswith("admin:sale:reject:"))
async def admin_sale_reject_callback(callback: CallbackQuery) -> None:
    if not is_admin_user(callback.from_user.id if callback.from_user else None):
        await callback.answer("Нет доступа", show_alert=True)
        return
    request_id = callback.data.split(":")[-1]
    await callback.answer("Отклоняю заявку...")
    _, text, result = await process_sale_access(request_id, action="reject", processed_by=callback.from_user.id if callback.from_user else None)
    if callback.message:
        await callback.message.answer(text)
    await notify_user_about_sale_access(result, action="reject")


async def main() -> None:
    await admin_bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(admin_bot)
    finally:
        await admin_bot.session.close()
        await client_sender_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
