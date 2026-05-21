import asyncio
import json
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote_plus

import aiohttp
import requests
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")
logging.basicConfig(level=logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WELCOME_IMAGE_PATH = os.path.join(BASE_DIR, "assets", "welcome.png")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").strip().rstrip("/")

CARD_NUMBER = os.getenv("CARD_NUMBER", "").strip()
CARD_DISPLAY = os.getenv("CARD_DISPLAY", "").strip() or CARD_NUMBER
CARD_HOLDER = os.getenv("CARD_HOLDER", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@AraStarsS_Manager").strip() or "@AraStarsS_Manager"
HELP_TELEGRAPH_URL = os.getenv("HELP_TELEGRAPH_URL", "https://telegra.ph/AraStars-05-12").strip() or "https://telegra.ph/AraStars-05-12"
FRAGMENT_ACCOUNT_ADDRESS = os.getenv("FRAGMENT_ACCOUNT_ADDRESS", "").strip()
PROJECT_TONVIEWER_URL = os.getenv(
    "TONVIEWER_URL",
    f"https://tonviewer.com/{FRAGMENT_ACCOUNT_ADDRESS}" if FRAGMENT_ACCOUNT_ADDRESS else "https://tonviewer.com/",
).strip() or (f"https://tonviewer.com/{FRAGMENT_ACCOUNT_ADDRESS}" if FRAGMENT_ACCOUNT_ADDRESS else "https://tonviewer.com/")
REFERRAL_INFO_URL = os.getenv("REFERRAL_INFO_URL", "https://telegra.ph/AraStars-05-10-2").strip() or "https://telegra.ph/AraStars-05-10-2"
SELL_STARS_INFO_URL = os.getenv("SELL_STARS_INFO_URL", "https://telegra.ph/Kak-prodavat-zvezdy-AraStars-05-10").strip() or "https://telegra.ph/Kak-prodavat-zvezdy-AraStars-05-10"
OWN_BOT_PRICE_RUB = os.getenv("OWN_BOT_PRICE_RUB", "5000").strip() or "5000"
PAYMENT_BANK_NAME = os.getenv("PAYMENT_BANK_NAME", "Ozon Bank").strip() or "Ozon Bank"

PRICE_50_99 = os.getenv("PRICE_50_99", "1.50").strip()
PRICE_100_4999 = os.getenv("PRICE_100_4999", "1.32").strip()
PRICE_5000_PLUS = os.getenv("PRICE_5000_PLUS", "1.24").strip()
SALE_PRICE_PER_STAR = os.getenv("SALE_PRICE_PER_STAR", "1.20").strip() or "1.20"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")
if not ADMIN_BOT_TOKEN:
    raise ValueError("ADMIN_BOT_TOKEN не найден в .env")
if not ADMIN_CHAT_ID:
    raise ValueError("ADMIN_CHAT_ID не найден в .env")


class OrderStates(StatesGroup):
    waiting_for_stars = State()
    waiting_for_recipient = State()
    waiting_for_payment_method = State()
    waiting_for_payment_proof = State()
    waiting_for_withdraw_requisites = State()
    waiting_for_balance_topup_amount = State()


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


LEVELS_TEXT = (
    "🏆 <b>Уровни и проценты</b>\n\n"
    "1️⃣ <b>1 уровень</b> — <code>0–999 ₽</code> → <code>15%</code>\n"
    "2️⃣ <b>2 уровень</b> — <code>1000–3999 ₽</code> → <code>25%</code>\n"
    "3️⃣ <b>3 уровень</b> — <code>4000–9999 ₽</code> → <code>45%</code>\n"
    "4️⃣ <b>4 уровень</b> — <code>10000–19999 ₽</code> → <code>65%</code>\n"
    "5️⃣ <b>5 уровень</b> — <code>20000+ ₽</code> → <code>90%</code>\n\n"
    "ℹ️ Процент начисляется от чистой прибыли заказа."
)



def to_decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Некорректная цена в .env: {value}") from exc


PRICE_50_99_D = to_decimal(PRICE_50_99)
PRICE_100_4999_D = to_decimal(PRICE_100_4999)
PRICE_5000_PLUS_D = to_decimal(PRICE_5000_PLUS)
SALE_PRICE_PER_STAR_D = to_decimal(SALE_PRICE_PER_STAR)
SALE_ACCESS_PLANS = {
    "sale_3d": {"title": "3 дня", "days": 3, "price": 79},
    "sale_7d": {"title": "7 дней", "days": 7, "price": 149},
    "sale_30d": {"title": "1 месяц", "days": 30, "price": 349},
    "sale_365d": {"title": "1 год", "days": 365, "price": 2699},
}

PRICE_CACHE_TTL_SECONDS = 20
_price_cache: dict[str, Any] = {"expires_at": 0.0, "prices": None}
TONVIEWER_STATS_CACHE_TTL_SECONDS = 600
TONVIEWER_REQUEST_TIMEOUT_SECONDS = 15
_tonviewer_stats_cache: dict[str, Any] = {"expires_at": 0.0, "stars": None}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Купить звезды", callback_data="buy_stars")],
            [
                InlineKeyboardButton(text="💳 Баланс", callback_data="balance_system"),
                InlineKeyboardButton(text="💰 Прайс", callback_data="show_prices"),
            ],
            [
                InlineKeyboardButton(text="🤝 Сотрудничество", callback_data="cooperation"),
                InlineKeyboardButton(text="📜 История заказов", callback_data="order_history"),
            ],
            [
                InlineKeyboardButton(text="🆘 Поддержка", callback_data="show_support"),
                InlineKeyboardButton(text="ℹ️ Помощь", url=HELP_TELEGRAPH_URL),
            ],
        ]
    )

def cooperation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Реферальная система", callback_data="ref_system"),
                InlineKeyboardButton(text="💸 Продажа звезд", callback_data="sell_stars"),
            ],
            [InlineKeyboardButton(text="🤖 Свой бот", callback_data="own_bot_menu")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )


def own_bot_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🪞 Зеркало бота", callback_data="bot_mirror")],
            [InlineKeyboardButton(text="🤖 Собственный бот", callback_data="custom_bot")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="cooperation")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )


def manager_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать менеджеру", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="own_bot_menu")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )


def buy_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🙋 Купить себе", callback_data="buy_self"),
                InlineKeyboardButton(text="🎁 Купить другому", callback_data="buy_other"),
            ],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )


def stars_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ 50 звёзд", callback_data="stars_amount:50"),
                InlineKeyboardButton(text="💫 100 звёзд", callback_data="stars_amount:100"),
            ],
            [InlineKeyboardButton(text="💠 Ввести своё количество", callback_data="stars_custom")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )



def payment_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"clientpaid:{order_id}")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )



def payment_choice_keyboard(can_use_balance: bool) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if can_use_balance:
        buttons.append([InlineKeyboardButton(text="💳 Оплатить с баланса", callback_data="pay_balance")])
    buttons.append([
        InlineKeyboardButton(text="🏦 По реквизитам", callback_data="pay_card"),
        InlineKeyboardButton(text="➕ Пополнить", callback_data="balance_topup"),
    ])
    buttons.append([InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)



def referral_keyboard(can_withdraw: bool, has_pending_withdrawal: bool) -> InlineKeyboardMarkup:
    withdraw_text = "💸 Запросить вывод" if can_withdraw else "🔒 Вывод от 500 ₽"
    if has_pending_withdrawal:
        withdraw_text = "⏳ Заявка уже создана"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📘 Как работает реферальная система", url=REFERRAL_INFO_URL)],
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="ref_system")],
            [InlineKeyboardButton(text="🏆 Уровни и проценты", callback_data="ref_levels")],
            [InlineKeyboardButton(text="👥 Мои рефералы", callback_data="ref_my_users")],
            [InlineKeyboardButton(text=withdraw_text, callback_data="ref_withdraw")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )



def balance_keyboard(has_pending_topup: bool) -> InlineKeyboardMarkup:
    topup_text = "➕ Пополнить баланс"
    if has_pending_topup:
        topup_text = "⏳ Заявка на пополнение уже создана"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="balance_system")],
            [InlineKeyboardButton(text=topup_text, callback_data="balance_topup")],
            [InlineKeyboardButton(text="⭐ Купить звезды", callback_data="buy_stars")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )



def sale_access_keyboard(profile: dict | None = None) -> InlineKeyboardMarkup:
    profile = profile or {}
    pending_request = profile.get("pending_request") or {}
    buttons: list[list[InlineKeyboardButton]] = []

    if pending_request.get("id"):
        buttons.append([
            InlineKeyboardButton(
                text=f"💳 Оплатить {pending_request.get('plan_title', 'тариф')}",
                callback_data=f"salepayment:{pending_request['id']}",
            )
        ])
    else:
        buttons.append([InlineKeyboardButton(text="📘 Как продавать звезды?", url=SELL_STARS_INFO_URL)])
        for plan_key, plan in SALE_ACCESS_PLANS.items():
            buttons.append([
                InlineKeyboardButton(
                    text=f"{plan['title']} — {plan['price']}₽",
                    callback_data=f"saleplan:{plan_key}",
                )
            ])

    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="sell_stars")])
    buttons.append([InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)



def sale_access_payment_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить статус", callback_data="sell_stars")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )



def normalize_username(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.startswith("@"):
        value = f"@{value}"
    return value



def is_valid_username(value: str) -> bool:
    value = normalize_username(value)
    if len(value) < 5:
        return False
    body = value[1:]
    return body.replace("_", "").isalnum()



def calc_price_per_star(stars: int) -> Decimal:
    if 50 <= stars < 100:
        return PRICE_50_99_D
    if 100 <= stars < 5000:
        return PRICE_100_4999_D
    if stars >= 5000:
        return PRICE_5000_PLUS_D
    raise ValueError("Минимальный заказ — 50 звёзд")



def calc_total(stars: int) -> Decimal:
    return (calc_price_per_star(stars) * Decimal(stars)).quantize(Decimal("0.01"))



def money_text(value: Decimal | float | str | int | None) -> str:
    return f"{Decimal(str(value or 0)).quantize(Decimal('0.01'))}"



def price_breakdown_text(stars: int) -> str:
    price = calc_price_per_star(stars)
    total = calc_total(stars)
    return (
        "✨ <b>Расчёт сформирован</b>\n\n"
        f"⭐ <b>Количество:</b> <code>{stars}</code>\n"
        f"💸 <b>Цена за 1 звезду:</b> <code>{price}</code> ₽\n"
        f"💰 <b>Итоговая сумма:</b> <code>{total}</code> RUB"
    )



def extract_order_id(data: Any) -> str:
    if isinstance(data, dict):
        if data.get("order_id"):
            return str(data["order_id"])
        if data.get("id"):
            return str(data["id"])
        if isinstance(data.get("order"), dict):
            order = data["order"]
            if order.get("id"):
                return str(order["id"])
            if order.get("order_id"):
                return str(order["order_id"])
    raise ValueError(f"Не удалось получить ID заказа из ответа backend: {data}")



def parse_backend_error(data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        if data.get("message"):
            return str(data["message"])
    return str(data)


def payment_requisites_text() -> str:
    return (
        f"Телефон: <code>{CARD_DISPLAY}</code>\n"
        f"Получатель: <b>{CARD_HOLDER}</b>\n"
        f"<b>ПЕРЕВОДИТЬ СТРОГО НА {PAYMENT_BANK_NAME.upper()}.</b>\n"
        "<b>НЕ ВЫБИРАЙТЕ ДРУГОЙ БАНК.</b>"
    )



def user_mention_or_id(user: dict | None) -> str:
    if not user:
        return "—"
    username = (user.get("telegram_username") or "").strip()
    if username:
        return f"@{username}"
    full_name = (user.get("telegram_full_name") or "").strip()
    if full_name:
        return full_name
    return f"ID {user.get('telegram_user_id', '—')}"



def build_ref_link(ref_code: str) -> str:
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?start=ref_{ref_code}"
    return f"ref_{ref_code}"



def format_balance_tx(item: dict) -> str:
    tx_type = item.get("type")
    amount = money_text(item.get("amount", 0))
    if tx_type == "deposit":
        return f"• ➕ Пополнение: <code>{amount}</code> ₽"
    if tx_type == "purchase":
        return f"• ➖ Покупка: <code>{amount}</code> ₽ ({item.get('status', '—')})"
    if tx_type == "refund":
        return f"• 🔄 Возврат: <code>{amount}</code> ₽"
    return f"• {tx_type}: <code>{amount}</code> ₽"



def format_datetime_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    try:
        return raw.replace("T", " ")[:16] + " UTC"
    except Exception:
        return raw


async def edit_purchase_screen(
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    fallback_message: Message | None = None,
) -> None:
    data = await state.get_data()
    chat_id = data.get("purchase_chat_id")
    message_id = data.get("purchase_message_id")
    if chat_id and message_id:
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            try:
                await bot.edit_message_caption(
                    chat_id=int(chat_id),
                    message_id=int(message_id),
                    caption=text,
                    reply_markup=reply_markup,
                )
                return
            except Exception:
                logging.exception("Не удалось отредактировать экран покупки")

    if fallback_message:
        sent = await fallback_message.answer(text, reply_markup=reply_markup)
        await state.update_data(purchase_chat_id=sent.chat.id, purchase_message_id=sent.message_id)



def format_sale_request_tx(item: dict) -> str:
    status = str(item.get("status") or "").strip()
    status_map = {
        "pending": "⏳ ожидает подтверждения",
        "active": "✅ активирован",
        "rejected": "❌ отклонён",
    }
    return (
        f"• {item.get('plan_title', 'Тариф')} — <code>{money_text(item.get('amount', 0))}</code> ₽ "
        f"({status_map.get(status, status or '—')})"
    )


def format_order_history_item(item: dict) -> str:
    status = str(item.get("status") or "").strip()
    status_map = {
        "payment_pending": "⏳ ожидает оплату",
        "paid": "✅ выполнен",
        "failed": "❌ отменён",
        "draft": "📝 черновик",
    }
    stars = item.get("stars_amount") or item.get("stars_count") or item.get("stars") or 0
    return (
        f"• <code>{str(item.get('id') or item.get('order_id') or '—')[:8]}</code> "
        f"— <code>{stars}</code>⭐ для <code>{item.get('recipient_username', '—')}</code>, "
        f"<code>{money_text(item.get('amount_rub') or item.get('amount') or 0)}</code> ₽ "
        f"({status_map.get(status, status or '—')})"
    )


async def http_request(method: str, url: str, json_body: dict[str, Any] | None = None) -> tuple[int, Any]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, json=json_body) as response:
            text = await response.text()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
            return response.status, data


async def create_order(
    tg_user_id: int,
    username: str,
    full_name: str,
    recipient_username: str,
    stars: int,
    amount_rub: Decimal,
) -> tuple[int, Any]:
    payload = {
        "telegram_user_id": tg_user_id,
        "telegram_username": username or "",
        "telegram_full_name": full_name or "",
        "recipient_username": recipient_username,
        "stars_amount": stars,
        "stars_count": stars,
        "stars": stars,
        "amount_rub": float(amount_rub),
        "amount": float(amount_rub),
        "status": "payment_pending",
    }
    return await http_request("POST", f"{BACKEND_URL}/orders/create", payload)


async def create_order_from_balance(
    tg_user_id: int,
    username: str,
    full_name: str,
    recipient_username: str,
    stars: int,
    amount_rub: Decimal,
) -> tuple[int, Any]:
    payload = {
        "telegram_user_id": tg_user_id,
        "telegram_username": username or "",
        "telegram_full_name": full_name or "",
        "recipient_username": recipient_username,
        "stars_amount": stars,
        "amount_rub": float(amount_rub),
    }
    return await http_request("POST", f"{BACKEND_URL}/orders/create-from-balance", payload)


async def get_order(order_id: str) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/orders/{order_id}")


async def get_order_history(telegram_user_id: int, limit: int = 10) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/orders/history/{telegram_user_id}?limit={limit}")


async def mark_client_paid(order_id: str) -> tuple[int, Any]:
    return await http_request("POST", f"{BACKEND_URL}/orders/{order_id}/client-paid", {})


async def submit_payment_proof(
    order_id: str,
    proof_file_id: str,
    proof_type: str,
    proof_note: str = "",
    proof_caption: str = "",
    proof_file_unique_id: str = "",
) -> tuple[int, Any]:
    return await http_request(
        "POST",
        f"{BACKEND_URL}/orders/{order_id}/submit-proof",
        {
            "proof_file_id": proof_file_id,
            "proof_file_unique_id": proof_file_unique_id or "",
            "proof_type": proof_type,
            "proof_note": proof_note or "",
            "proof_caption": proof_caption or "",
        },
    )


async def check_fragment_recipient(username: str) -> tuple[int, Any]:
    safe_username = quote_plus(username)
    return await http_request("GET", f"{BACKEND_URL}/fragment/check-recipient?username={safe_username}")


async def register_referral_user(message: Message) -> tuple[dict | None, dict | None, str | None, bool, bool, int]:
    start_args = ""
    raw_text = (message.text or "").strip()
    if raw_text.startswith("/start"):
        parts = raw_text.split(maxsplit=1)
        if len(parts) > 1:
            start_args = parts[1].strip()

    inviter_code = None
    if start_args.lower().startswith("ref_"):
        inviter_code = start_args[4:].strip().upper()

    payload = {
        "telegram_user_id": message.from_user.id,
        "telegram_username": message.from_user.username or "",
        "telegram_full_name": message.from_user.full_name or "",
        "inviter_code": inviter_code,
    }
    status_code, result = await http_request("POST", f"{BACKEND_URL}/referral/users/register", payload)
    if status_code >= 400:
        logging.warning("Не удалось зарегистрировать пользователя для рефералки: %s", result)
        return None, None, None, False, False, 0

    profile = result.get("profile") if isinstance(result, dict) else None
    inviter = result.get("inviter") if isinstance(result, dict) else None
    is_new_user = bool(result.get("is_new_user")) if isinstance(result, dict) else False
    referral_bound_now = bool(result.get("referral_bound_now")) if isinstance(result, dict) else False
    inviter_invite_count = int(result.get("inviter_invite_count") or 0) if isinstance(result, dict) else 0
    return profile, inviter, inviter_code, is_new_user, referral_bound_now, inviter_invite_count


async def get_referral_profile(telegram_user_id: int) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/referral/users/{telegram_user_id}")


async def get_balance_profile(telegram_user_id: int) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/balance/users/{telegram_user_id}")


async def get_sale_access_profile(telegram_user_id: int) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/sale-access/users/{telegram_user_id}")


async def get_current_price_settings(force: bool = False) -> dict[str, Decimal]:
    now = asyncio.get_event_loop().time()
    cached = _price_cache.get("prices")
    if cached and not force and float(_price_cache.get("expires_at") or 0) > now:
        return cached

    status_code, result = await http_request("GET", f"{BACKEND_URL}/settings/prices")
    prices = result.get("prices") if status_code < 400 and isinstance(result, dict) else {}
    clean = {
        "price_50_99": Decimal(str(prices.get("price_50_99", PRICE_50_99_D))).quantize(Decimal("0.01")),
        "price_100_4999": Decimal(str(prices.get("price_100_4999", PRICE_100_4999_D))).quantize(Decimal("0.01")),
        "price_5000_plus": Decimal(str(prices.get("price_5000_plus", PRICE_5000_PLUS_D))).quantize(Decimal("0.01")),
        "sale_price_per_star": Decimal(str(prices.get("sale_price_per_star", SALE_PRICE_PER_STAR_D))).quantize(Decimal("0.01")),
    }
    _price_cache["prices"] = clean
    _price_cache["expires_at"] = now + PRICE_CACHE_TTL_SECONDS
    return clean


async def get_stock_info() -> dict[str, Any]:
    status_code, result = await http_request("GET", f"{BACKEND_URL}/admin/stock")
    if status_code >= 400 or not isinstance(result, dict):
        return {"available_stars": 0, "settings": {}}
    return result


async def available_stars_text() -> str:
    return ""


def fetch_tonviewer_sold_stars_sync() -> int | None:
    if not FRAGMENT_ACCOUNT_ADDRESS:
        return None

    total_stars = 0
    before_lt: str | None = None
    with requests.Session() as session:
        while True:
            url = f"https://tonapi.io/v2/accounts/{FRAGMENT_ACCOUNT_ADDRESS}/events?limit=100"
            if before_lt:
                url += f"&before_lt={before_lt}"

            data: dict[str, Any] | None = None
            for attempt in range(3):
                try:
                    response = session.get(
                        url,
                        headers={"User-Agent": "AraStarsBot/1.0", "Accept-Encoding": "identity"},
                        timeout=TONVIEWER_REQUEST_TIMEOUT_SECONDS,
                    )
                except requests.RequestException as exc:
                    logging.warning("TonViewer stats request failed: %s", exc)
                    return None

                if response.status_code == 429 and attempt < 2:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = max(1.0, float(retry_after or 1.5))
                    except ValueError:
                        delay = 1.5
                    time.sleep(delay)
                    continue

                if response.status_code >= 400:
                    logging.warning("TonViewer stats request failed: %s", response.status_code)
                    return None

                try:
                    data = response.json()
                except ValueError as exc:
                    logging.warning("TonViewer stats request failed: %s", exc)
                    return None
                break

            if data is None:
                return None

            events = data.get("events") or []
            for event in events:
                for action in event.get("actions") or []:
                    transfer = action.get("TonTransfer") or {}
                    comment = str(transfer.get("comment") or "")
                    match = re.search(r"(\d+)\s+Telegram\s+Stars", comment, re.IGNORECASE)
                    if match:
                        total_stars += int(match.group(1))

            before_lt = str(data.get("next_from") or "")
            if not events or not before_lt or len(events) < 100:
                break
            time.sleep(0.4)

    return total_stars if total_stars > 0 else None


async def get_tonviewer_sold_stars() -> int | None:
    now = asyncio.get_event_loop().time()
    cached_stars = _tonviewer_stats_cache.get("stars")
    if cached_stars is not None and float(_tonviewer_stats_cache.get("expires_at") or 0) > now:
        return int(cached_stars)

    total_stars = await asyncio.to_thread(fetch_tonviewer_sold_stars_sync)
    if total_stars is None:
        return None

    _tonviewer_stats_cache["stars"] = total_stars
    _tonviewer_stats_cache["expires_at"] = now + TONVIEWER_STATS_CACHE_TTL_SECONDS
    return total_stars


async def main_menu_text() -> str:
    stats_line = "Куплено через сервис: данные обновляются"
    wallet_url = PROJECT_TONVIEWER_URL

    try:
        status_code, result = await http_request("GET", f"{BACKEND_URL}/admin/stats")
        if status_code < 400 and isinstance(result, dict):
            stats = result.get("stats") or {}
            periods = stats.get("periods") or {}
            all_time = periods.get("all_time") or {}
            settings = stats.get("settings") or {}
            wallet_url = str(settings.get("tonviewer_url") or wallet_url).strip() or wallet_url
            sold_stars = int(Decimal(str(all_time.get("turnover_stars") or 0)))
            sold_usd = money_text(all_time.get("turnover_usd") or 0)
            stats_line = f"Куплено через сервис: <code>{sold_stars}</code> ⭐ (<code>{sold_usd}</code> $)"
        try:
            tonviewer_stars = await asyncio.wait_for(get_tonviewer_sold_stars(), timeout=2.0)
            if tonviewer_stars is not None:
                tonviewer_usd = (Decimal(tonviewer_stars) / Decimal("100") * Decimal("1.5")).quantize(Decimal("0.01"))
                stats_line = f"Куплено через сервис: <code>{tonviewer_stars}</code> ⭐ (<code>{tonviewer_usd}</code> $)"
        except TimeoutError:
            logging.warning("Статистика TonViewer не успела загрузиться для стартового меню")
    except Exception:
        logging.exception("Не удалось получить статистику для стартового меню")

    return (
        "✨ <b>AraStars</b>\n"
        "Telegram Stars без лишних шагов: выберите объём, укажите получателя и оплатите удобным способом.\n\n"
        "Платёж проверяется автоматически, история заказов сохраняется, баланс всегда под рукой.\n\n"
        f"{stats_line}\n"
        f"Оборот можно проверить: <a href=\"{wallet_url}\">кошелек</a>\n\n"
        "👇 Выберите действие:"
    )


async def get_effective_price_per_star(telegram_user_id: int, stars: int) -> Decimal:
    prices = await get_current_price_settings()
    if 50 <= stars < 100:
        default_price = prices["price_50_99"]
    elif 100 <= stars < 5000:
        default_price = prices["price_100_4999"]
    elif stars >= 5000:
        default_price = prices["price_5000_plus"]
    else:
        raise ValueError("Минимальный заказ — 50 звёзд")

    status_code, result = await get_sale_access_profile(telegram_user_id)
    if status_code >= 400 or not isinstance(result, dict):
        return default_price

    profile = result.get("profile") or {}
    if not profile.get("sale_access_active"):
        return default_price

    user_data = profile.get("user") or {}
    raw_price = (
        user_data.get("sale_price_per_star")
        or profile.get("price_per_star")
        or (profile.get("current_plan") or {}).get("price_per_star")
        or prices["sale_price_per_star"]
    )
    try:
        return Decimal(str(raw_price)).quantize(Decimal("0.01"))
    except Exception:
        return prices["sale_price_per_star"]


async def get_effective_total(telegram_user_id: int, stars: int) -> Decimal:
    price = await get_effective_price_per_star(telegram_user_id, stars)
    return (price * Decimal(stars)).quantize(Decimal("0.01"))


async def price_breakdown_text_for_user(telegram_user_id: int, stars: int) -> str:
    price = await get_effective_price_per_star(telegram_user_id, stars)
    total = (price * Decimal(stars)).quantize(Decimal("0.01"))
    return (
        f"⭐ <b>Звёзды:</b> <code>{stars}</code>\n"
        f"💎 <b>Цена:</b> <code>{price}</code> ₽ за 1 ⭐\n"
        f"💰 <b>Итог:</b> <code>{total}</code> RUB"
    )

async def get_sale_access_request(request_id: str) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/sale-access/requests/{request_id}")


async def create_sale_access_request(
    telegram_user_id: int,
    telegram_username: str,
    telegram_full_name: str,
    plan_key: str,
) -> tuple[int, Any]:
    return await http_request(
        "POST",
        f"{BACKEND_URL}/sale-access/requests/create",
        {
            "telegram_user_id": telegram_user_id,
            "telegram_username": telegram_username or "",
            "telegram_full_name": telegram_full_name or "",
            "plan_key": plan_key,
        },
    )


async def create_withdrawal_request(telegram_user_id: int, requisites: str) -> tuple[int, Any]:
    return await http_request(
        "POST",
        f"{BACKEND_URL}/referral/withdrawals/create",
        {"telegram_user_id": telegram_user_id, "requisites": requisites},
    )


async def create_balance_topup_request(
    telegram_user_id: int,
    telegram_username: str,
    telegram_full_name: str,
    amount: Decimal,
) -> tuple[int, Any]:
    return await http_request(
        "POST",
        f"{BACKEND_URL}/balance/topups/create",
        {
            "telegram_user_id": telegram_user_id,
            "telegram_username": telegram_username or "",
            "telegram_full_name": telegram_full_name or "",
            "amount": float(amount),
        },
    )


async def forward_payment_proof_to_admin(message: Message) -> None:
    try:
        await bot.forward_message(chat_id=int(ADMIN_CHAT_ID), from_chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        logging.exception("Не удалось переслать чек админу")


async def send_admin_notification(order_id: str, order_data: dict[str, Any], customer: Message | CallbackQuery) -> None:
    from_user = customer.from_user
    client_username = f"@{from_user.username}" if from_user and from_user.username else "без username"

    recipient_username = order_data.get("recipient_username", "—")
    stars_amount = order_data.get("stars_amount") or order_data.get("stars_count") or order_data.get("stars") or 0
    amount_rub = order_data.get("amount_rub") or order_data.get("amount") or "0.00"
    payment_comment = order_data.get("payment_comment") or "—"
    auto_issue_allowed = bool(order_data.get("auto_issue_allowed"))
    proof_type = order_data.get("proof_file_type") or "—"

    text = (
        "🛒 <b>Новая покупка / заказ</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        f"👤 <b>Клиент:</b> {from_user.full_name if from_user else '—'}\n"
        f"🔹 <b>Telegram ID:</b> <code>{from_user.id if from_user else 0}</code>\n"
        f"📎 <b>Username клиента:</b> {client_username}\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Звёзд:</b> <code>{stars_amount}</code>\n"
        f"💰 <b>Сумма:</b> <code>{money_text(amount_rub)}</code> RUB\n"
        f"💬 <b>Комментарий к переводу:</b> <code>{payment_comment}</code>\n"
        f"🧾 <b>Подтверждение:</b> <code>{proof_type}</code>\n"
        f"⚙️ <b>Автовыдача для заказа:</b> <code>{'yes' if auto_issue_allowed else 'no'}</code>"
    )

    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Подтвердить", "callback_data": f"admin:approve:{order_id}"},
                {"text": "❌ Отклонить", "callback_data": f"admin:reject:{order_id}"},
            ]]
        },
    }
    status_code, result = await http_request("POST", f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage", payload)
    if status_code >= 400:
        raise RuntimeError(f"Не удалось отправить уведомление админу: {result}")


async def send_withdraw_admin_notification(message: Message, withdrawal: dict, user: dict) -> None:
    username = message.from_user.username or user.get("telegram_username") or ""
    display_username = f"@{username}" if username else "без username"

    text = (
        "🏧 <b>Новая заявка на вывод</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{withdrawal['id']}</code>\n"
        f"👤 <b>Пользователь:</b> {message.from_user.full_name}\n"
        f"🔹 <b>Telegram ID:</b> <code>{message.from_user.id}</code>\n"
        f"📎 <b>Username:</b> {display_username}\n"
        f"💸 <b>Сумма:</b> <code>{money_text(withdrawal['amount'])}</code> RUB\n"
        f"💳 <b>Реквизиты:</b> <code>{withdrawal['requisites']}</code>\n"
        f"💼 <b>Осталось доступно:</b> <code>{money_text(user['ref_balance_available'])}</code> RUB\n"
        f"🔒 <b>Заморожено:</b> <code>{money_text(user['ref_balance_frozen'])}</code> RUB"
    )

    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "✅ Отметить выплаченным", "callback_data": f"admin:withdraw:paid:{withdrawal['id']}"}],
                [{"text": "❌ Отклонить", "callback_data": f"admin:withdraw:reject:{withdrawal['id']}"}],
            ]
        },
    }
    status_code, result = await http_request("POST", f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage", payload)
    if status_code >= 400:
        raise RuntimeError(f"Не удалось отправить уведомление о выводе: {result}")


async def send_topup_admin_notification(message: Message, topup: dict) -> None:
    username = message.from_user.username or ""
    display_username = f"@{username}" if username else "без username"
    text = (
        "💳 <b>Новая заявка на пополнение баланса</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{topup['id']}</code>\n"
        f"👤 <b>Пользователь:</b> {message.from_user.full_name}\n"
        f"🔹 <b>Telegram ID:</b> <code>{message.from_user.id}</code>\n"
        f"📎 <b>Username:</b> {display_username}\n"
        f"💸 <b>Сумма:</b> <code>{money_text(topup['amount'])}</code> RUB"
    )
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "✅ Зачислить на баланс", "callback_data": f"admin:topup:approve:{topup['id']}"}],
                [{"text": "❌ Отклонить", "callback_data": f"admin:topup:reject:{topup['id']}"}],
            ]
        },
    }
    status_code, result = await http_request("POST", f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage", payload)
    if status_code >= 400:
        raise RuntimeError(f"Не удалось отправить уведомление о пополнении: {result}")


async def send_sale_access_admin_notification(request_id: str, request_data: dict[str, Any], customer: Message | CallbackQuery) -> None:
    from_user = customer.from_user
    client_username = f"@{from_user.username}" if from_user and from_user.username else "без username"

    text = (
        "💸 <b>Новая заявка на продажу звёзд</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
        f"👤 <b>Клиент:</b> {from_user.full_name if from_user else '—'}\n"
        f"🔹 <b>Telegram ID:</b> <code>{from_user.id if from_user else 0}</code>\n"
        f"📎 <b>Username клиента:</b> {client_username}\n"
        f"📦 <b>Тариф:</b> <code>{request_data.get('plan_title', '—')}</code>\n"
        f"🗓 <b>Срок:</b> <code>{request_data.get('days', 0)}</code> дн.\n"
        f"💰 <b>Сумма:</b> <code>{money_text(request_data.get('amount', 0))}</code> RUB\n"
        f"⭐ <b>Цена выкупа:</b> <code>{money_text(request_data.get('price_per_star', SALE_PRICE_PER_STAR_D))}</code> ₽/шт"
    )

    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "✅ Активировать", "callback_data": f"admin:sale:approve:{request_id}"}],
                [{"text": "❌ Отклонить", "callback_data": f"admin:sale:reject:{request_id}"}],
            ]
        },
    }
    status_code, result = await http_request("POST", f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage", payload)
    if status_code >= 400:
        raise RuntimeError(f"Не удалось отправить уведомление о продаже звёзд: {result}")


async def send_admin_new_referral_notification(new_user: Message, inviter: dict, invite_count: int) -> None:
    text = (
        "👥 <b>Новый реферал</b>\n\n"
        f"🤝 <b>Партнёр:</b> {user_mention_or_id(inviter)}\n"
        f"🆕 <b>Новый пользователь:</b> {new_user.from_user.full_name}\n"
        f"🔹 <b>ID нового пользователя:</b> <code>{new_user.from_user.id}</code>\n"
        f"👥 <b>Всего приглашено у партнёра:</b> <code>{invite_count}</code>"
    )
    payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML"}
    await http_request("POST", f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage", payload)


async def send_admin_new_user_notification(new_user: Message, inviter: dict | None, inviter_code: str | None) -> None:
    username = f"@{new_user.from_user.username}" if new_user.from_user and new_user.from_user.username else "без username"
    inviter_text = "без реферальной ссылки"
    if inviter:
        inviter_text = user_mention_or_id(inviter)
    elif inviter_code:
        inviter_text = f"код не применился: <code>{inviter_code}</code>"

    text = (
        "🆕 <b>Новый пользователь</b>\n\n"
        f"👤 <b>Имя:</b> {new_user.from_user.full_name}\n"
        f"🔹 <b>Telegram ID:</b> <code>{new_user.from_user.id}</code>\n"
        f"📎 <b>Username:</b> {username}\n"
        f"🤝 <b>Рефералка:</b> {inviter_text}"
    )
    payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML"}
    await http_request("POST", f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage", payload)


async def send_partner_new_referral_notification(inviter: dict, new_user: Message, invite_count: int) -> None:
    text = (
        "🎉 <b>У вас новый реферал</b>\n\n"
        f"👤 <b>Пользователь:</b> {new_user.from_user.full_name}\n"
        f"👥 <b>Всего приглашено:</b> <code>{invite_count}</code>\n\n"
        "Приглашайте больше людей и увеличивайте оборот, чтобы повышать уровень и получать более высокий процент."
    )
    try:
        await bot.send_message(chat_id=int(inviter["telegram_user_id"]), text=text)
    except Exception:
        logging.exception("Не удалось уведомить партнёра о новом реферале")



def build_levels_message(profile: dict) -> str:
    profile_user = profile.get("user") or {}
    level = profile.get("level") or {}
    text = (
        "📈 <b>Ваш рост в реферальной системе</b>\n\n"
        f"🏅 <b>Текущий уровень:</b> <code>{level.get('name', '1 уровень')}</code>\n"
        f"💸 <b>Текущий процент:</b> <code>{level.get('percent', 15)}%</code>\n"
        f"💰 <b>Оборот рефералов:</b> <code>{money_text(profile_user.get('ref_total_turnover', 0))}</code> ₽\n\n"
    )
    if level.get("next_turnover"):
        text += f"🎯 <b>До следующего уровня осталось:</b> <code>{money_text(level.get('remaining_to_next_level', 0))}</code> ₽ оборота\n\n"
    else:
        text += "👑 <b>У вас уже максимальный уровень.</b>\n\n"
    text += LEVELS_TEXT
    return text



def build_my_referrals_message(profile: dict) -> str:
    profile_user = profile.get("user") or {}
    total_earned = Decimal(str(profile_user.get("ref_balance_available", 0))) + Decimal(str(profile_user.get("ref_balance_frozen", 0))) + Decimal(str(profile_user.get("ref_total_paid", 0)))
    recent_lines = []
    for item in (profile.get("recent_transactions") or [])[:5]:
        recent_lines.append(
            f"• ID <code>{item['referral_user_id']}</code> — заказ <code>{money_text(item['order_amount'])}</code> ₽, начислено <code>{money_text(item['reward_amount'])}</code> ₽"
        )
    if not recent_lines:
        recent_lines.append("• Пока нет оплаченных заказов от приглашённых пользователей")
    return (
        "👥 <b>Мои рефералы</b>\n\n"
        f"👤 <b>Приглашено:</b> <code>{profile.get('invite_count', 0)}</code>\n"
        f"💰 <b>Оборот рефералов:</b> <code>{money_text(profile_user.get('ref_total_turnover', 0))}</code> ₽\n"
        f"🪙 <b>Заработано чистыми:</b> <code>{money_text(total_earned)}</code> ₽\n\n"
        "<b>Последние начисления по рефералам:</b>\n" + "\n".join(recent_lines)
    )


async def render_referral_profile_message(target: Message | CallbackQuery) -> None:
    user = target.from_user
    status_code, result = await get_referral_profile(user.id)
    if status_code >= 400:
        text = "❌ <b>Не удалось загрузить реферальный раздел</b>\n\n" f"<code>{parse_backend_error(result)}</code>"
        if isinstance(target, CallbackQuery):
            try:
                await target.message.edit_text(text, reply_markup=main_menu_keyboard())
            except Exception:
                await target.message.answer(text, reply_markup=main_menu_keyboard())
            await target.answer()
        else:
            await target.answer(text, reply_markup=main_menu_keyboard())
        return

    profile = result.get("profile") if isinstance(result, dict) else {}
    profile_user = profile.get("user") or {}
    inviter = profile.get("inviter") or {}
    level = profile.get("level") or {}
    pending = profile.get("pending_withdrawal") or {}
    ref_code = profile_user.get("ref_code") or "—"
    ref_link = build_ref_link(ref_code)

    inviter_text = "не назначен"
    inviter_username = inviter.get("telegram_username") or ""
    if inviter:
        inviter_text = f"@{inviter_username}" if inviter_username else f"ID {inviter.get('telegram_user_id')}"

    recent_lines = []
    for item in (profile.get("recent_transactions") or [])[:3]:
        recent_lines.append(
            f"• +<code>{money_text(item['reward_amount'])}</code> ₽ c заказа <code>{money_text(item['order_amount'])}</code> ₽ по ставке <code>{item['percent_used']}</code>%"
        )
    if not recent_lines:
        recent_lines.append("• Начислений пока нет")

    pending_text = "нет"
    if pending:
        pending_text = f"есть заявка на <code>{money_text(pending['amount'])}</code> ₽"

    total_earned = Decimal(str(profile_user.get("ref_balance_available", 0))) + Decimal(str(profile_user.get("ref_balance_frozen", 0))) + Decimal(str(profile_user.get("ref_total_paid", 0)))

    text = (
        "👥 <b>Реферальная система</b>\n\n"
        "Приглашайте друзей в бот и получайте <b>пассивный доход</b> с их покупок.\n"
        "Чем выше ваш уровень, тем больше процент и выше заработок.\n"
        f"📘 <a href=\"{REFERRAL_INFO_URL}\">Как работает реферальная система</a>\n\n"
        f"🔗 <b>Ваша ссылка:</b> <code>{ref_link}</code>\n"
        f"🏷 <b>Ваш код:</b> <code>{ref_code}</code>\n"
        f"🤝 <b>Ваш пригласивший:</b> {inviter_text}\n\n"
        f"🏅 <b>Уровень:</b> <code>{level.get('name', '1 уровень')}</code>\n"
        f"📈 <b>Процент:</b> <code>{level.get('percent', 15)}</code>%\n"
        f"👤 <b>Приглашено:</b> <code>{profile.get('invite_count', 0)}</code>\n"
        f"💰 <b>Оборот рефералов:</b> <code>{money_text(profile_user.get('ref_total_turnover', 0))}</code> ₽\n"
        f"🪙 <b>Заработано чистыми:</b> <code>{money_text(total_earned)}</code> ₽\n\n"
        f"💼 <b>Доступно:</b> <code>{money_text(profile_user.get('ref_balance_available', 0))}</code> ₽\n"
        f"🔒 <b>Заморожено:</b> <code>{money_text(profile_user.get('ref_balance_frozen', 0))}</code> ₽\n"
        f"💸 <b>Выплачено:</b> <code>{money_text(profile_user.get('ref_total_paid', 0))}</code> ₽\n"
        f"🏧 <b>Минимальный вывод:</b> <code>{money_text(profile.get('min_withdraw', 500))}</code> ₽\n"
        f"📋 <b>Статус вывода:</b> {pending_text}\n"
    )
    if level.get("next_turnover"):
        text += f"🎯 <b>До следующего уровня:</b> <code>{money_text(level.get('remaining_to_next_level', 0))}</code> ₽ оборота\n"
    else:
        text += "👑 <b>У вас максимальный уровень.</b>\n"
    text += "\n<b>Последние начисления:</b>\n" + "\n".join(recent_lines)

    keyboard = referral_keyboard(
        can_withdraw=bool(profile.get("can_withdraw")),
        has_pending_withdrawal=bool(profile.get("has_pending_withdrawal")),
    )
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await target.message.answer(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


async def render_balance_profile_message(target: Message | CallbackQuery) -> None:
    user = target.from_user
    status_code, result = await get_balance_profile(user.id)
    if status_code >= 400:
        text = "❌ <b>Не удалось загрузить баланс</b>\n\n" f"<code>{parse_backend_error(result)}</code>"
        if isinstance(target, CallbackQuery):
            try:
                await target.message.edit_text(text, reply_markup=main_menu_keyboard())
            except Exception:
                await target.message.answer(text, reply_markup=main_menu_keyboard())
            await target.answer()
        else:
            await target.answer(text, reply_markup=main_menu_keyboard())
        return

    profile = result.get("profile") if isinstance(result, dict) else {}
    profile_user = profile.get("user") or {}
    pending_topup = profile.get("pending_topup") or {}
    recent_lines = [format_balance_tx(item) for item in (profile.get("recent_transactions") or [])[:5]]
    if not recent_lines:
        recent_lines.append("• Пока операций нет")

    pending_text = "нет"
    if pending_topup:
        pending_text = f"заявка на <code>{money_text(pending_topup.get('amount', 0))}</code> ₽"

    text = (
        "💳 <b>Баланс пользователя</b>\n\n"
        "Пополните баланс один раз и оплачивайте звёзды в пару нажатий.\n\n"
        f"💰 <b>Текущий баланс:</b> <code>{money_text(profile_user.get('balance', 0))}</code> ₽\n"
        f"➕ <b>Пополнено всего:</b> <code>{money_text(profile_user.get('total_deposited', 0))}</code> ₽\n"
        f"➖ <b>Потрачено всего:</b> <code>{money_text(profile_user.get('total_spent', 0))}</code> ₽\n"
        f"📋 <b>Статус пополнения:</b> {pending_text}\n\n"
        "<b>Последние операции:</b>\n" + "\n".join(recent_lines)
    )
    keyboard = balance_keyboard(has_pending_topup=bool(profile.get("has_pending_topup")))
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await target.message.answer(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


async def render_sale_access_message(target: Message | CallbackQuery) -> None:
    user = target.from_user
    status_code, result = await get_sale_access_profile(user.id)
    if status_code >= 400:
        text = "❌ <b>Не удалось загрузить продажу звёзд</b>\n\n" f"<code>{parse_backend_error(result)}</code>"
        if isinstance(target, CallbackQuery):
            try:
                await target.message.edit_text(text, reply_markup=main_menu_keyboard())
            except Exception:
                await target.message.answer(text, reply_markup=main_menu_keyboard())
            await target.answer()
        else:
            await target.answer(text, reply_markup=main_menu_keyboard())
        return

    profile = result.get("profile") if isinstance(result, dict) else {}
    profile_user = profile.get("user") or {}
    pending_request = profile.get("pending_request") or {}
    current_plan = profile.get("current_plan") or {}
    recent_lines = [format_sale_request_tx(item) for item in (profile.get("recent_requests") or [])[:3]]
    if not recent_lines:
        recent_lines.append("• Пока тарифов не было")

    status_lines: list[str] = []
    if profile.get("sale_access_active"):
        status_lines.append("✅ <b>Доступ уже активен</b>")
        if current_plan.get("title"):
            status_lines.append(f"📦 <b>Текущий тариф:</b> <code>{current_plan['title']}</code>")
        status_lines.append(
            f"⏰ <b>Доступ до:</b> <code>{format_datetime_text(profile_user.get('sale_access_until'))}</code>"
        )
    else:
        status_lines.append("🔒 <b>Доступ пока не активирован</b>")

    if pending_request.get("id"):
        status_lines.append(
            f"⏳ <b>Есть заявка в обработке:</b> <code>{pending_request.get('plan_title', 'тариф')}</code> "
            f"за <code>{money_text(pending_request.get('amount', 0))}</code> ₽"
        )

    text = (
        "💸 <b>Продажа звёзд</b>\n\n"
        "Этот раздел для тех, кто хочет не просто покупать Stars, а зарабатывать на обороте.\n"
        f"Выкупаем звёзды по <code>{money_text(profile.get('price_per_star', SALE_PRICE_PER_STAR_D))}</code> ₽ за 1 штуку: вы продаёте, мы быстро принимаем и оплачиваем.\n"
        f"📘 <a href=\"{SELL_STARS_INFO_URL}\">Как продавать звезды?</a>\n\n"
        + "\n".join(status_lines)
        + "\n\n"
        + "<b>Тарифы:</b>\n"
        + "\n".join(
            f"• {plan['title']} — <code>{plan['price']}</code> ₽"
            for plan in SALE_ACCESS_PLANS.values()
        )
        + "\n\n<b>Последние заявки:</b>\n"
        + "\n".join(recent_lines)
    )

    keyboard = sale_access_keyboard(profile)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await target.message.answer(text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


async def send_sale_access_payment_message(target_message: Message, request_data: dict[str, Any]) -> None:
    request_id = str(request_data.get("id") or "")
    await target_message.answer(
        "✅ <b>Заявка на продажу звёзд создана</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
        f"📦 <b>Тариф:</b> <code>{request_data.get('plan_title', '—')}</code>\n"
        f"🗓 <b>Срок:</b> <code>{request_data.get('days', 0)}</code> дн.\n"
        f"💰 <b>Сумма к оплате:</b> <code>{money_text(request_data.get('amount', 0))}</code> RUB\n"
        f"⭐ <b>Цена выкупа:</b> <code>{money_text(request_data.get('price_per_star', SALE_PRICE_PER_STAR_D))}</code> ₽/шт\n\n"
        "💳 <b>Реквизиты для оплаты</b>\n"
        f"{payment_requisites_text()}\n\n"
        "После оплаты заявка проверится автоматически. Когда платёж будет найден, доступ включится без дополнительных действий.",
        reply_markup=sale_access_payment_keyboard(request_id),
    )


async def create_and_send_order_message(
    target_message: Message,
    sender: Message | CallbackQuery,
    state: FSMContext,
    recipient_username: str,
    stars: int,
) -> None:
    user = sender.from_user
    price = await get_effective_price_per_star(user.id, stars)
    total = (price * Decimal(stars)).quantize(Decimal("0.01"))
    status_code, result = await create_order(
        tg_user_id=user.id,
        username=user.username or "",
        full_name=user.full_name or "",
        recipient_username=recipient_username,
        stars=stars,
        amount_rub=total,
    )
    if status_code >= 400:
        await state.clear()
        await target_message.answer(
            "❌ <b>Не удалось создать заказ</b>\n\nПопробуйте ещё раз немного позже.",
            reply_markup=main_menu_keyboard(),
        )
        return

    balance = Decimal("0")
    balance_status, balance_result = await get_balance_profile(user.id)
    if balance_status < 400 and isinstance(balance_result, dict):
        profile = balance_result.get("profile") or {}
        balance = Decimal(str((profile.get("user") or {}).get("balance", 0)))

    order = result.get("order") if isinstance(result, dict) else {}
    if not isinstance(order, dict):
        order = {}
    order_id = extract_order_id(result)
    payment_comment = order.get("payment_comment") or "—"
    await state.clear()
    text = (
        "✅ <b>Заказ сформирован</b>\n\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Звёзды:</b> <code>{stars}</code>\n"
        f"💎 <b>Цена:</b> <code>{money_text(price)}</code> ₽ за 1 ⭐\n"
        f"💰 <b>Итог:</b> <code>{money_text(total)}</code> RUB\n"
        f"💳 <b>Ваш баланс:</b> <code>{money_text(balance)}</code> RUB\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        f"💬 <b>Комментарий к переводу:</b> <code>{payment_comment}</code>\n\n"
        "💳 <b>Реквизиты для оплаты</b>\n"
        f"{payment_requisites_text()}\n\n"
        "ВАЖНО: комментарий к переводу нужно скопировать и вставить в приложении банка. "
        "После оплаты нажмите <b>Я оплатил</b>. Чек отправлять не нужно — бот сам найдёт платёж."
    )
    try:
        await target_message.edit_text(text, reply_markup=payment_keyboard(order_id))
    except Exception:
        try:
            await target_message.edit_caption(caption=text, reply_markup=payment_keyboard(order_id))
        except Exception:
            await target_message.answer(text, reply_markup=payment_keyboard(order_id))
    try:
        await send_admin_notification(order_id, order, sender)
    except Exception:
        logging.exception("Не удалось отправить админу уведомление о новом заказе")


async def create_and_send_balance_order_message(
    target_message: Message,
    sender: Message | CallbackQuery,
    state: FSMContext,
    recipient_username: str,
    stars: int,
) -> None:
    user = sender.from_user
    total = await get_effective_total(user.id, stars)
    status_code, result = await create_order_from_balance(
        tg_user_id=user.id,
        username=user.username or "",
        full_name=user.full_name or "",
        recipient_username=recipient_username,
        stars=stars,
        amount_rub=total,
    )
    if status_code >= 400:
        await state.clear()
        await target_message.answer(
            "❌ <b>Не удалось оплатить заказ с баланса</b>\n\n"
            f"<code>{parse_backend_error(result)}</code>",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.clear()
    order = result.get("order") if isinstance(result, dict) else {}
    profile_user = result.get("user") if isinstance(result, dict) else {}
    text = (
        "✅ <b>Покупка с баланса выполнена</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order.get('id', '—')}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Количество звёзд:</b> <code>{stars}</code>\n"
        f"💸 <b>Списано с баланса:</b> <code>{money_text(total)}</code> RUB\n"
        f"💰 <b>Остаток баланса:</b> <code>{money_text(profile_user.get('balance', 0))}</code> RUB\n\n"
        "Звёзды отправлены автоматически."
    )
    try:
        await target_message.edit_text(text, reply_markup=main_menu_keyboard())
    except Exception:
        try:
            await target_message.edit_caption(caption=text, reply_markup=main_menu_keyboard())
        except Exception:
            await target_message.answer(text, reply_markup=main_menu_keyboard())

    try:
        await send_admin_notification(str(order.get("id", "—")), order if isinstance(order, dict) else {}, sender)
    except Exception:
        logging.exception("Не удалось отправить админу уведомление о покупке с баланса")


async def show_payment_options_message(target_message: Message, state: FSMContext, telegram_user_id: int, recipient_username: str, stars: int) -> None:
    total = await get_effective_total(telegram_user_id, stars)
    price = await get_effective_price_per_star(telegram_user_id, stars)
    status_code, result = await get_balance_profile(telegram_user_id)
    balance = Decimal("0")
    if status_code < 400 and isinstance(result, dict):
        profile = result.get("profile") or {}
        balance = Decimal(str((profile.get("user") or {}).get("balance", 0)))

    await state.update_data(recipient_username=recipient_username, stars=stars)
    await state.set_state(OrderStates.waiting_for_payment_method)

    can_use_balance = balance >= total
    text = (
        "✅ <b>Заказ сформирован</b>\n\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Звёзды:</b> <code>{stars}</code>\n"
        f"💎 <b>Цена:</b> <code>{money_text(price)}</code> ₽ за 1 ⭐\n"
        f"💰 <b>Итог:</b> <code>{money_text(total)}</code> RUB\n"
        f"💳 <b>Ваш баланс:</b> <code>{money_text(balance)}</code> RUB\n\n"
        "👇 Выберите способ оплаты:"
    )

    await edit_purchase_screen(
        state,
        text,
        reply_markup=payment_choice_keyboard(can_use_balance=can_use_balance),
        fallback_message=target_message,
    )


@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    profile, inviter, inviter_code, is_new_user, referral_bound_now, inviter_invite_count = await register_referral_user(message)

    text = await main_menu_text()
    if inviter_code:
        if inviter:
            inviter_username = inviter.get("telegram_username") or ""
            inviter_name = f"@{inviter_username}" if inviter_username else f"ID {inviter.get('telegram_user_id')}"
            text += f"\n\n🤝 Вы зашли по реферальной ссылке партнёра {inviter_name}."
        else:
            text += "\n\n⚠️ Реферальный код не удалось применить."

    if os.path.exists(WELCOME_IMAGE_PATH):
        try:
            await message.answer_photo(
                photo=FSInputFile(WELCOME_IMAGE_PATH),
                caption=text,
                reply_markup=main_menu_keyboard(),
            )
        except Exception:
            logging.exception("Не удалось отправить стартовое фото")
            await message.answer(text, reply_markup=main_menu_keyboard(), disable_web_page_preview=True)
    else:
        await message.answer(text, reply_markup=main_menu_keyboard(), disable_web_page_preview=True)

    if is_new_user:
        try:
            await send_admin_new_user_notification(message, inviter, inviter_code)
        except Exception:
            logging.exception("Не удалось отправить уведомление о новом пользователе")

    if referral_bound_now and inviter and profile:
        try:
            await send_partner_new_referral_notification(inviter, message, inviter_invite_count)
            await send_admin_new_referral_notification(message, inviter, inviter_invite_count)
        except Exception:
            logging.exception("Не удалось отправить уведомление о новом реферале")


@dp.message(Command("menu"))
async def menu_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(await main_menu_text(), reply_markup=main_menu_keyboard(), disable_web_page_preview=True)


@dp.callback_query(F.data == "back_menu")
async def back_menu_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text(await main_menu_text(), reply_markup=main_menu_keyboard(), disable_web_page_preview=True)
    except Exception:
        await callback.message.answer(await main_menu_text(), reply_markup=main_menu_keyboard(), disable_web_page_preview=True)
    await callback.answer()


@dp.callback_query(F.data == "show_prices")
async def show_prices_handler(callback: CallbackQuery) -> None:
    prices = await get_current_price_settings(force=True)
    text = (
        "💰 <b>Актуальный прайс</b>\n\n"
        f"• от 50 до 99 звёзд — <b>{prices['price_50_99']}</b> ₽ за 1 звезду\n"
        f"• от 100 до 4999 звёзд — <b>{prices['price_100_4999']}</b> ₽ за 1 звезду\n"
        f"• от 5000 звёзд — <b>{prices['price_5000_plus']}</b> ₽ за 1 звезду\n\n"
        f"💸 Для активных продавцов: <b>{prices['sale_price_per_star']}</b> ₽ за 1 звезду\n\n"
        "Минимальный заказ: <b>50</b> звёзд"
    )
    try:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=main_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "show_help")
async def show_help_handler(callback: CallbackQuery) -> None:
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "Как проходит покупка:\n"
        "1. Вы выбираете количество звёзд\n"
        "2. Бот показывает цену и итоговую сумму\n"
        "3. Вы отправляете username получателя\n"
        "4. Выбираете: с баланса или по реквизитам\n"
        "5. При оплате по реквизитам бот сам найдёт платёж по сумме и комментарию\n"
        "6. При оплате с баланса звёзды отправляются сразу\n\n"
        "По сотрудничеству:\n"
        "• открываете раздел «Сотрудничество»\n"
        "• выбираете реферальную систему, продажу звёзд или свой бот\n"
        "• по продаже звёзд выбираете тариф и оплачиваете его\n\n"
        "По реферальной системе:\n"
        "• вы получаете процент с оплаченных заказов приглашённых пользователей\n"
        "• процент растёт по карьерной лестнице\n"
        "• вывод доступен от 500 ₽ через заявку"
    )
    try:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=main_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "show_support")
async def show_support_handler(callback: CallbackQuery) -> None:
    text = f"🆘 <b>Поддержка</b>\n\nПо всем вопросам обращайтесь:\n<code>{SUPPORT_USERNAME}</code>"
    try:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=main_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "cooperation")
async def cooperation_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "🤝 <b>Сотрудничество с AraStars</b>\n\n"
        "Если вы уже приводите людей, продаёте звёзды или хотите запустить свой поток заказов, здесь собраны все варианты заработка.\n\n"
        "Можно начать с простой реферальной ссылки, подключиться к продаже звёзд или обсудить собственный бот под ваш трафик."
    )
    try:
        await callback.message.edit_text(text, reply_markup=cooperation_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=cooperation_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "own_bot_menu")
async def own_bot_menu_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "🤖 <b>Свой бот</b>\n\n"
        "Если у вас есть аудитория, канал, чат или поток клиентов, отдельный бот помогает принимать заказы аккуратно и без лишней ручной работы.\n\n"
        "Выберите формат ниже."
    )
    try:
        await callback.message.edit_text(text, reply_markup=own_bot_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=own_bot_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "bot_mirror")
async def bot_mirror_handler(callback: CallbackQuery) -> None:
    await callback.answer("Зеркало бота скоро появится.", show_alert=True)


@dp.callback_query(F.data == "custom_bot")
async def custom_bot_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "🤖 <b>Собственный бот под вас</b>\n\n"
        "Это вариант для тех, кто хочет принимать заказы в своём стиле: отдельное название, оформление, реквизиты и поток клиентов без смешивания с общим ботом.\n\n"
        f"Стоимость запуска: <code>{money_text(OWN_BOT_PRICE_RUB)}</code> ₽.\n"
        f"Если хотите купить или обсудить детали, напишите менеджеру: <code>{SUPPORT_USERNAME}</code>."
    )
    try:
        await callback.message.edit_text(text, reply_markup=manager_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=manager_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "order_history")
async def order_history_handler(callback: CallbackQuery) -> None:
    status_code, result = await get_order_history(callback.from_user.id, limit=10)
    if status_code >= 400 or not isinstance(result, dict):
        await callback.answer("Не удалось загрузить историю", show_alert=True)
        return
    history = result.get("history") or {}
    orders = history.get("orders") or []
    lines = [format_order_history_item(order) for order in orders[:10]]
    if not lines:
        lines.append("• Заказов пока нет")
    text = (
        "📜 <b>История заказов</b>\n\n"
        f"Всего заказов: <code>{history.get('orders_count', 0)}</code>\n"
        f"Выполнено звёзд: <code>{history.get('paid_stars', 0)}</code>⭐\n"
        f"Оплачено: <code>{money_text(history.get('paid_rub', 0))}</code> ₽\n\n"
        + "\n".join(lines)
    )
    try:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard())
    except Exception:
        await callback.message.answer(text, reply_markup=main_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "sell_stars")
async def sell_stars_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render_sale_access_message(callback)


@dp.callback_query(F.data.startswith("saleplan:"))
async def sale_plan_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    plan_key = callback.data.split(":", 1)[1]
    status_code, result = await create_sale_access_request(
        telegram_user_id=callback.from_user.id,
        telegram_username=callback.from_user.username or "",
        telegram_full_name=callback.from_user.full_name or "",
        plan_key=plan_key,
    )
    if status_code >= 400:
        await callback.answer(parse_backend_error(result), show_alert=True)
        return

    request_data = result.get("request") if isinstance(result, dict) else {}
    await send_sale_access_payment_message(callback.message, request_data or {})
    await callback.answer()


@dp.callback_query(F.data.startswith("salepayment:"))
async def sale_payment_handler(callback: CallbackQuery) -> None:
    request_id = callback.data.split(":", 1)[1]
    status_code, result = await get_sale_access_request(request_id)
    if status_code >= 400:
        await callback.answer("Не удалось найти заявку", show_alert=True)
        return
    request_data = result.get("request") if isinstance(result, dict) else {}
    await send_sale_access_payment_message(callback.message, request_data or {})
    await callback.answer()


@dp.callback_query(F.data.startswith("salepaid:"))
async def sale_paid_callback(callback: CallbackQuery) -> None:
    request_id = callback.data.split(":", 1)[1]
    status_code, result = await get_sale_access_request(request_id)
    if status_code >= 400:
        await callback.message.answer(
            "❌ <b>Не удалось получить заявку</b>\n\nПопробуйте ещё раз немного позже."
        )
        await callback.answer()
        return

    request_data = result.get("request") if isinstance(result, dict) else {}
    if request_data.get("status") != "pending":
        await callback.message.answer(
            "ℹ️ <b>Заявка уже обработана</b>\n\n"
            f"📦 <b>Тариф:</b> <code>{request_data.get('plan_title', '—')}</code>\n"
            f"📋 <b>Статус:</b> <code>{request_data.get('status', '—')}</code>"
        )
        await callback.answer()
        return

    try:
        await send_sale_access_admin_notification(request_id, request_data, callback)
        await callback.message.answer(
            "⏳ <b>Платёж отправлен на проверку</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
            f"📦 <b>Тариф:</b> <code>{request_data.get('plan_title', '—')}</code>\n\n"
            "Как только оплата будет подтверждена, доступ к продаже звёзд включится автоматически."
        )
    except Exception:
        logging.exception("Ошибка при отправке заявки на продажу звёзд админу")
        await callback.message.answer(
            "❌ <b>Не удалось отправить платёж на проверку</b>\n\nПопробуйте ещё раз немного позже."
        )
    await callback.answer("Передаю заявку на проверку...")


@dp.callback_query(F.data == "balance_system")
async def balance_system_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render_balance_profile_message(callback)


@dp.callback_query(F.data == "balance_topup")
async def balance_topup_handler(callback: CallbackQuery, state: FSMContext) -> None:
    status_code, result = await get_balance_profile(callback.from_user.id)
    if status_code >= 400:
        await callback.answer("Не удалось проверить баланс", show_alert=True)
        return
    profile = result.get("profile") if isinstance(result, dict) else {}
    if profile.get("has_pending_topup"):
        await callback.answer("У вас уже есть заявка на пополнение", show_alert=True)
        return

    await state.set_state(OrderStates.waiting_for_balance_topup_amount)
    await callback.message.answer(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Введите сумму, на которую хотите пополнить баланс.\n\n"
        "Пример: <code>500</code>"
    )
    await callback.answer()


@dp.callback_query(F.data == "ref_system")
async def ref_system_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render_referral_profile_message(callback)


@dp.callback_query(F.data == "ref_levels")
async def ref_levels_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    status_code, result = await get_referral_profile(callback.from_user.id)
    if status_code >= 400:
        await callback.answer("Не удалось загрузить уровни", show_alert=True)
        return
    profile = result.get("profile") if isinstance(result, dict) else {}
    keyboard = referral_keyboard(bool(profile.get("can_withdraw")), bool(profile.get("has_pending_withdrawal")))
    try:
        await callback.message.edit_text(build_levels_message(profile), reply_markup=keyboard)
    except Exception:
        await callback.message.answer(build_levels_message(profile), reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "ref_my_users")
async def ref_my_users_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    status_code, result = await get_referral_profile(callback.from_user.id)
    if status_code >= 400:
        await callback.answer("Не удалось загрузить рефералов", show_alert=True)
        return
    profile = result.get("profile") if isinstance(result, dict) else {}
    keyboard = referral_keyboard(bool(profile.get("can_withdraw")), bool(profile.get("has_pending_withdrawal")))
    try:
        await callback.message.edit_text(build_my_referrals_message(profile), reply_markup=keyboard)
    except Exception:
        await callback.message.answer(build_my_referrals_message(profile), reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "ref_withdraw")
async def ref_withdraw_handler(callback: CallbackQuery, state: FSMContext) -> None:
    status_code, result = await get_referral_profile(callback.from_user.id)
    if status_code >= 400:
        await callback.answer("Не удалось проверить баланс", show_alert=True)
        return

    profile = result.get("profile") if isinstance(result, dict) else {}
    if profile.get("has_pending_withdrawal"):
        await callback.answer("У вас уже есть активная заявка на вывод", show_alert=True)
        return
    if not profile.get("can_withdraw"):
        min_withdraw = profile.get("min_withdraw", 500)
        await callback.answer(f"Вывод доступен от {money_text(min_withdraw)} ₽", show_alert=True)
        return

    await state.set_state(OrderStates.waiting_for_withdraw_requisites)
    await callback.message.answer(
        "🏧 <b>Создание заявки на вывод</b>\n\n"
        f"Доступный баланс: <code>{money_text(profile.get('user', {}).get('ref_balance_available', 0))}</code> ₽\n"
        "Отправьте реквизиты для выплаты одним сообщением.\n\n"
        "Пример: <code>2200 7000 1234 5678 Иван Иванов</code>"
    )
    await callback.answer()


@dp.callback_query(F.data == "buy_stars")
async def buy_stars_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(
        purchase_chat_id=callback.message.chat.id,
        purchase_message_id=callback.message.message_id,
    )
    text = "Покупка Telegram Stars ✨\n\nВыберите, что хотите сделать:"
    await edit_purchase_screen(state, text, reply_markup=buy_type_keyboard(), fallback_message=callback.message)
    await callback.answer()


@dp.callback_query(F.data == "buy_self")
async def buy_self_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(
        mode="self",
        purchase_chat_id=callback.message.chat.id,
        purchase_message_id=callback.message.message_id,
    )
    await state.set_state(OrderStates.waiting_for_stars)
    await edit_purchase_screen(
        state,
        "✨ <b>Покупка Telegram Stars</b>\n\n"
        "Выберите количество звёзд или введите своё значение.",
        reply_markup=stars_choice_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "buy_other")
async def buy_other_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(
        mode="other",
        purchase_chat_id=callback.message.chat.id,
        purchase_message_id=callback.message.message_id,
    )
    await state.set_state(OrderStates.waiting_for_stars)
    await edit_purchase_screen(
        state,
        "✨ <b>Покупка Telegram Stars</b>\n\n"
        "Выберите количество звёзд или введите своё значение.",
        reply_markup=stars_choice_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "pay_card")
async def pay_card_handler(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    recipient_username = str(data.get("recipient_username") or "")
    stars = int(data.get("stars") or 0)
    if not recipient_username or stars < 50:
        await state.clear()
        await callback.answer("Сессия покупки устарела, начните заново", show_alert=True)
        return
    await create_and_send_order_message(callback.message, callback, state, recipient_username, stars)
    await callback.answer()


@dp.callback_query(F.data == "pay_balance")
async def pay_balance_handler(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    recipient_username = str(data.get("recipient_username") or "")
    stars = int(data.get("stars") or 0)
    if not recipient_username or stars < 50:
        await state.clear()
        await callback.answer("Сессия покупки устарела, начните заново", show_alert=True)
        return
    await create_and_send_balance_order_message(callback.message, callback, state, recipient_username, stars)
    await callback.answer()


@dp.callback_query(F.data == "stars_custom")
async def stars_custom_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderStates.waiting_for_stars)
    await state.update_data(
        purchase_chat_id=callback.message.chat.id,
        purchase_message_id=callback.message.message_id,
    )
    await edit_purchase_screen(
        state,
        "💠 <b>Своё количество</b>\n\n"
        "Введите количество звёзд одним сообщением.\n"
        "Минимальный заказ: <b>50</b> ⭐\n\n"
        "Пример: <code>250</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="↩️ Назад", callback_data="buy_stars")],
                [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
            ]
        ),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("stars_amount:"))
async def stars_preset_handler(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        stars = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Не удалось выбрать количество", show_alert=True)
        return
    await process_stars_amount(callback.from_user.id, callback.from_user.username or "", stars, state, callback.message)
    await callback.answer()


@dp.message(OrderStates.waiting_for_stars)
async def stars_amount_handler(message: Message, state: FSMContext) -> None:
    raw_value = (message.text or "").strip()
    try:
        await message.delete()
    except Exception:
        pass
    if not raw_value.isdigit():
        await edit_purchase_screen(
            state,
            "❌ <b>Некорректное количество</b>\n\nВведите число от <b>50</b> и выше.\nПример: <code>100</code>",
            reply_markup=stars_choice_keyboard(),
            fallback_message=message,
        )
        return
    stars = int(raw_value)
    if stars < 50:
        await edit_purchase_screen(
            state,
            "❌ <b>Некорректное количество</b>\n\nВведите число от <b>50</b> и выше.\nПример: <code>100</code>",
            reply_markup=stars_choice_keyboard(),
            fallback_message=message,
        )
        return

    await process_stars_amount(message.from_user.id, message.from_user.username or "", stars, state, message)


async def process_stars_amount(
    telegram_user_id: int,
    telegram_username: str,
    stars: int,
    state: FSMContext,
    fallback_message: Message,
) -> None:
    data = await state.get_data()
    mode = str(data.get("mode", "")).strip()
    await state.update_data(stars=stars)
    breakdown = await price_breakdown_text_for_user(telegram_user_id, stars)

    if mode == "self":
        own_username = normalize_username(telegram_username)
        if own_username:
            status_code, result = await check_fragment_recipient(own_username)
            if status_code >= 400:
                await state.set_state(OrderStates.waiting_for_recipient)
                await edit_purchase_screen(
                    state,
                    f"{breakdown}\n\n❌ <b>Не удалось проверить username</b>\n\nУкажите username получателя вручную:\n<code>@username</code>",
                    fallback_message=fallback_message,
                )
                return
            if isinstance(result, dict) and result.get("found"):
                await show_payment_options_message(fallback_message, state, telegram_user_id, own_username, stars)
                return

            await state.set_state(OrderStates.waiting_for_recipient)
            await edit_purchase_screen(
                state,
                f"{breakdown}\n\n❌ <b>Ваш username не найден</b>\n\nFragment не смог найти ваш аккаунт для покупки звёзд.\nУкажите другой username вручную:\n<code>@username</code>",
                fallback_message=fallback_message,
            )
            return

        await state.set_state(OrderStates.waiting_for_recipient)
        await edit_purchase_screen(
            state,
            f"{breakdown}\n\n🎁 <b>Получатель</b>\n\nОтправьте username получателя:\n<code>@username</code>",
            fallback_message=fallback_message,
        )
        return

    await state.set_state(OrderStates.waiting_for_recipient)
    await edit_purchase_screen(
        state,
        f"{breakdown}\n\n🎁 <b>Получатель</b>\n\nОтправьте username получателя:\n<code>@username</code>",
        fallback_message=fallback_message,
    )


@dp.message(OrderStates.waiting_for_recipient)
async def recipient_handler(message: Message, state: FSMContext) -> None:
    recipient_username = normalize_username(message.text or "")
    try:
        await message.delete()
    except Exception:
        pass
    if not is_valid_username(recipient_username):
        await edit_purchase_screen(
            state,
            "❌ <b>Некорректный username</b>\n\nОтправьте username в формате:\n<code>@username</code>",
            fallback_message=message,
        )
        return

    data = await state.get_data()
    stars = int(data.get("stars") or 0)
    if stars < 50:
        await state.set_state(OrderStates.waiting_for_stars)
        await edit_purchase_screen(
            state,
            "✨ <b>Покупка Telegram Stars</b>\n\nВыберите количество звёзд или введите своё значение.",
            reply_markup=stars_choice_keyboard(),
            fallback_message=message,
        )
        return

    status_code, result = await check_fragment_recipient(recipient_username)
    if status_code >= 400:
        await edit_purchase_screen(
            state,
            "❌ <b>Не удалось проверить username</b>\n\nПопробуйте ещё раз через пару секунд.",
            fallback_message=message,
        )
        return
    if not isinstance(result, dict) or not result.get("found"):
        await edit_purchase_screen(
            state,
            "❌ <b>Получатель не найден</b>\n\nFragment не находит такой username.\nПроверьте правильность написания и отправьте другой username.",
            fallback_message=message,
        )
        return

    await show_payment_options_message(message, state, message.from_user.id, recipient_username, stars)


@dp.message(OrderStates.waiting_for_withdraw_requisites)
async def withdraw_requisites_handler(message: Message, state: FSMContext) -> None:
    requisites = (message.text or "").strip()
    if len(requisites) < 5:
        await message.answer("❌ <b>Слишком короткие реквизиты</b>\n\nОтправьте реквизиты одним сообщением.")
        return

    status_code, result = await create_withdrawal_request(message.from_user.id, requisites)
    if status_code >= 400:
        await state.clear()
        await message.answer(
            "❌ <b>Не удалось создать заявку</b>\n\n" f"<code>{parse_backend_error(result)}</code>",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.clear()
    withdrawal = result.get("withdrawal") if isinstance(result, dict) else {}
    user = result.get("user") if isinstance(result, dict) else {}
    try:
        await send_withdraw_admin_notification(message, withdrawal, user)
    except Exception:
        logging.exception("Ошибка при отправке заявки на вывод админу")

    await message.answer(
        "✅ <b>Заявка на вывод создана</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{withdrawal.get('id', '—')}</code>\n"
        f"💸 <b>Сумма:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB\n"
        "Заявка отправлена админу на обработку.",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(OrderStates.waiting_for_balance_topup_amount)
async def balance_topup_amount_handler(message: Message, state: FSMContext) -> None:
    raw_value = (message.text or "").replace(",", ".").strip()
    try:
        amount = Decimal(raw_value).quantize(Decimal("0.01"))
    except Exception:
        await message.answer("❌ <b>Некорректная сумма</b>\n\nВведите сумму числом. Например: <code>500</code>")
        return
    if amount <= 0:
        await message.answer("❌ <b>Сумма должна быть больше 0</b>")
        return

    status_code, result = await create_balance_topup_request(
        telegram_user_id=message.from_user.id,
        telegram_username=message.from_user.username or "",
        telegram_full_name=message.from_user.full_name or "",
        amount=amount,
    )
    if status_code >= 400:
        await state.clear()
        await message.answer(
            "❌ <b>Не удалось создать заявку на пополнение</b>\n\n"
            f"<code>{parse_backend_error(result)}</code>",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.clear()
    topup = result.get("topup") if isinstance(result, dict) else {}
    try:
        await send_topup_admin_notification(message, topup)
    except Exception:
        logging.exception("Ошибка при отправке уведомления о пополнении")

    await message.answer(
        "✅ <b>Заявка на пополнение создана</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{topup.get('id', '—')}</code>\n"
        f"💸 <b>Сумма:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB\n\n"
        "💳 <b>Реквизиты для оплаты:</b>\n"
        f"{payment_requisites_text()}\n\n"
        "После поступления перевода админ зачислит деньги на ваш баланс.",
        reply_markup=main_menu_keyboard(),
    )


@dp.callback_query(F.data.startswith("clientpaid:"))
async def client_paid_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Оплата отмечена")
    try:
        order_id = callback.data.split(":", 1)[1]
        status_code, result = await mark_client_paid(order_id)
        if status_code >= 400:
            await callback.message.answer(
                "❌ <b>Не удалось отметить оплату</b>\n\nПопробуйте ещё раз немного позже.",
                reply_markup=main_menu_keyboard(),
            )
            return

        order = result.get("order") if isinstance(result, dict) else {}
        if not isinstance(order, dict):
            order = {}
        if order:
            try:
                await send_admin_notification(order_id, order, callback)
            except Exception:
                logging.exception("Не удалось отправить админу уведомление после кнопки Я оплатил")

        await state.clear()
        await callback.message.answer(
            "⏳ <b>Оплата отмечена</b>\n\n"
            f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
            "Чек отправлять не нужно. Как только перевод будет найден, заказ уйдёт в обработку автоматически.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception:
        logging.exception("Ошибка при отметке оплаты клиентом")
        await callback.message.answer(
            "❌ <b>Не удалось отметить оплату</b>\n\nПопробуйте ещё раз немного позже.",
            reply_markup=main_menu_keyboard(),
        )


@dp.message(OrderStates.waiting_for_payment_proof, F.photo)
async def payment_proof_photo_handler(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = str(data.get("payment_proof_order_id") or "")
    if not order_id:
        await state.clear()
        await message.answer("⚠️ Сессия оплаты устарела. Начните заново через меню.", reply_markup=main_menu_keyboard())
        return

    photo = message.photo[-1]
    caption = (message.caption or "").strip()
    status_code, result = await submit_payment_proof(
        order_id=order_id,
        proof_file_id=photo.file_id,
        proof_file_unique_id=photo.file_unique_id,
        proof_type="photo",
        proof_note=caption,
        proof_caption=caption,
    )
    if status_code >= 400:
        await state.clear()
        await message.answer(
            "❌ <b>Не удалось принять подтверждение оплаты</b>\n\n"
            f"<code>{parse_backend_error(result)}</code>",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.clear()
    order = result.get("order") if isinstance(result, dict) else {}
    if not isinstance(order, dict):
        order = {}

    if result.get("auto_approved"):
        await message.answer(
            "✅ <b>Оплата подтверждена автоматически</b>\n\n"
            f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
            "Заказ уже передан в обработку, звёзды отправляются автоматически.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await forward_payment_proof_to_admin(message)
    await send_admin_notification(order_id, order, message)
    await message.answer(
        "⏳ <b>Скриншот получен</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        "Мы передали подтверждение админу. Как только проверка завершится, вы получите уведомление.",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(OrderStates.waiting_for_payment_proof, F.document)
async def payment_proof_document_handler(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = str(data.get("payment_proof_order_id") or "")
    if not order_id:
        await state.clear()
        await message.answer("⚠️ Сессия оплаты устарела. Начните заново через меню.", reply_markup=main_menu_keyboard())
        return

    document = message.document
    mime_type = (document.mime_type or "").lower()
    if not (mime_type.startswith("image/") or mime_type == "application/pdf"):
        await message.answer("Отправьте изображение чека или PDF-файл.")
        return

    caption = (message.caption or "").strip()
    proof_type = "pdf" if mime_type == "application/pdf" else "document"
    status_code, result = await submit_payment_proof(
        order_id=order_id,
        proof_file_id=document.file_id,
        proof_file_unique_id=document.file_unique_id,
        proof_type=proof_type,
        proof_note=caption,
        proof_caption=caption,
    )
    if status_code >= 400:
        await state.clear()
        await message.answer(
            "❌ <b>Не удалось принять подтверждение оплаты</b>\n\n"
            f"<code>{parse_backend_error(result)}</code>",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.clear()
    order = result.get("order") if isinstance(result, dict) else {}
    if not isinstance(order, dict):
        order = {}

    if result.get("auto_approved"):
        await message.answer(
            "✅ <b>Оплата подтверждена автоматически</b>\n\n"
            f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
            "Заказ уже передан в обработку, звёзды отправляются автоматически.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await forward_payment_proof_to_admin(message)
    await send_admin_notification(order_id, order, message)
    await message.answer(
        "⏳ <b>Чек получен</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        "Мы передали подтверждение админу. Как только проверка завершится, вы получите уведомление.",
        reply_markup=main_menu_keyboard(),
    )


@dp.message(OrderStates.waiting_for_payment_proof)
async def payment_proof_fallback_handler(message: Message) -> None:
    await message.answer("Пожалуйста, отправьте скриншот чека как фото или PDF-файл одним сообщением.")


@dp.callback_query()
async def unknown_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка устарела. Нажмите /start", show_alert=True)


@dp.message()
async def fallback_handler(message: Message) -> None:
    await message.answer("Нажмите <b>/start</b>, чтобы открыть главное меню.", reply_markup=main_menu_keyboard())


async def main() -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
