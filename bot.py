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
from dotenv import dotenv_values, load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=_ENV_PATH)

# A key written in .env but left blank (as .env.example ships them) would make
# os.getenv() return "" instead of the default spelled out at the call site.
# Treat blank as absent so those defaults apply.
for _key in dotenv_values(_ENV_PATH) or {}:
    if not os.environ.get(_key, "").strip():
        os.environ.pop(_key, None)
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").strip().rstrip("/")
BACKEND_ADMIN_TOKEN = os.getenv("BACKEND_ADMIN_TOKEN", ADMIN_BOT_TOKEN).strip()

CARD_NUMBER = os.getenv("CARD_NUMBER", "").strip()
CARD_DISPLAY = os.getenv("CARD_DISPLAY", "").strip() or CARD_NUMBER
CARD_HOLDER = os.getenv("CARD_HOLDER", "").strip()
PAYMENT_REQUISITES_TEXT = os.getenv("PAYMENT_REQUISITES_TEXT", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@AraStarsS_Manager").strip() or "@AraStarsS_Manager"
WELCOME_PHOTO_PATH = os.getenv("WELCOME_PHOTO_PATH", "assets/welcome.png").strip() or "assets/welcome.png"
WELCOME_PHOTO_FILE_ID = os.getenv("WELCOME_PHOTO_FILE_ID", "").strip()
WELCOME_PHOTO_FILE_ID_PATH = (
    os.getenv("WELCOME_PHOTO_FILE_ID_PATH", "welcome_photo_file_id.txt").strip()
    or "welcome_photo_file_id.txt"
)
HELP_TELEGRAPH_URL = os.getenv("HELP_TELEGRAPH_URL", "https://telegra.ph/AraStars-05-12").strip() or "https://telegra.ph/AraStars-05-12"

LEGAL_DIR = os.path.join(BASE_DIR, "legal")
LEGAL_SELLER_NAME = os.getenv("LEGAL_SELLER_NAME", "").strip()
LEGAL_SELLER_INN = os.getenv("LEGAL_SELLER_INN", "").strip()
LEGAL_SELLER_EMAIL = os.getenv("LEGAL_SELLER_EMAIL", "").strip()
LEGAL_DOC_DATE = os.getenv("LEGAL_DOC_DATE", "").strip()

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
    waiting_for_withdraw_requisites = State()
    waiting_for_balance_topup_amount = State()


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


LEVELS_TEXT = (
    "🏆 <b>Уровни и проценты</b>\n\n"
    "1️⃣ <b>1 уровень</b> — <code>0–999 ₽</code> → <code>15%</code>\n"
    "2️⃣ <b>2 уровень</b> — <code>1000–6999 ₽</code> → <code>25%</code>\n"
    "3️⃣ <b>3 уровень</b> — <code>7000–14999 ₽</code> → <code>40%</code>\n"
    "4️⃣ <b>4 уровень</b> — <code>15000–24999 ₽</code> → <code>65%</code>\n"
    "5️⃣ <b>5 уровень</b> — <code>25000–39000 ₽</code> → <code>80%</code>\n"
    "👑 <b>MAX</b> — <code>40000+ ₽</code> → <code>90%</code>\n\n"
    "Многоуровневая прибыль начисляется с чистой прибыли до 5 уровней вверх."
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
FRAGMENT_ACCOUNT_ADDRESS = os.getenv("FRAGMENT_ACCOUNT_ADDRESS", "").strip()
PROJECT_TONVIEWER_URL = f"https://tonviewer.com/{FRAGMENT_ACCOUNT_ADDRESS}" if FRAGMENT_ACCOUNT_ADDRESS else "https://tonviewer.com/"
SETTINGS: dict[str, str] = {
    "price_50_99": str(PRICE_50_99_D),
    "price_100_4999": str(PRICE_100_4999_D),
    "price_5000_plus": str(PRICE_5000_PLUS_D),
    "sale_price_per_star": str(SALE_PRICE_PER_STAR_D),
    "usd_rate": "90",
    "tonviewer_url": PROJECT_TONVIEWER_URL,
    "referral_info_url": "https://telegra.ph/Referalnaya-programma-AraStars-05-07",
}
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)
HTTP_SESSION: aiohttp.ClientSession | None = None
SETTINGS_CACHE_TTL_SECONDS = 30
SETTINGS_CACHE_EXPIRES_AT = 0.0
TONVIEWER_STATS_CACHE_TTL_SECONDS = 600
TONVIEWER_REQUEST_TIMEOUT_SECONDS = 15
_tonviewer_stats_cache: dict[str, Any] = {"expires_at": 0.0, "stars": None}
SALE_ACCESS_PLANS = {
    "sale_3d": {"title": "3 дня", "days": 3, "price": 79},
    "sale_7d": {"title": "7 дней", "days": 7, "price": 149},
    "sale_30d": {"title": "1 месяц", "days": 30, "price": 349},
    "sale_365d": {"title": "1 год", "days": 365, "price": 2699},
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Купить звезды", callback_data="buy_stars")],
            [
                InlineKeyboardButton(text="💳 Баланс", callback_data="balance_system"),
                InlineKeyboardButton(text="💰 Прайс", callback_data="show_prices"),
            ],
            [
                InlineKeyboardButton(text="👥 Реферальная система", callback_data="ref_system"),
                InlineKeyboardButton(text="💼 Стать продавцом", callback_data="sell_stars"),
            ],
            [
                InlineKeyboardButton(text="📜 История заказов", callback_data="order_history"),
                InlineKeyboardButton(text="🆘 Поддержка", callback_data="show_support"),
            ],
            [InlineKeyboardButton(text="ℹ️ Помощь", url=HELP_TELEGRAPH_URL)],
        ]
    )


def stars_amount_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"50 ⭐ · {money_text(calc_total(50))} ₽", callback_data="starsquick:50"),
                InlineKeyboardButton(text=f"100 ⭐ · {money_text(calc_total(100))} ₽", callback_data="starsquick:100"),
            ],
            [InlineKeyboardButton(text="💠 Свое количество", callback_data="stars_custom")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )


def seller_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Реферальная программа", callback_data="seller_referral"),
                InlineKeyboardButton(text="💸 Продажа звезд", callback_data="seller_sale_access"),
            ],
            [InlineKeyboardButton(text="🤖 Свой бот", callback_data="seller_own_bot")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )



def buy_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🙋 Себе", callback_data="buy_self"),
                InlineKeyboardButton(text="🎁 Другому", callback_data="buy_other"),
            ],
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Оплатить с баланса", callback_data="pay_balance"),
                InlineKeyboardButton(text="🏦 Оплатить по реквизитам", callback_data="pay_card"),
            ],
        ]
    )



def referral_keyboard(can_withdraw: bool, has_pending_withdrawal: bool) -> InlineKeyboardMarkup:
    withdraw_text = "💸 Запросить вывод" if can_withdraw else "🔒 Вывод от 500 ₽"
    if has_pending_withdrawal:
        withdraw_text = "⏳ Заявка уже создана"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📘 Как работает реферальная программа",
                    url=SETTINGS.get("referral_info_url", "https://telegra.ph/Referalnaya-programma-AraStars-05-07"),
                )
            ],
            [
                InlineKeyboardButton(text="🏆 Уровни", callback_data="ref_levels"),
                InlineKeyboardButton(text="👥 Мои рефералы", callback_data="ref_my_users"),
            ],
            [
                InlineKeyboardButton(text=withdraw_text, callback_data="ref_withdraw"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="ref_system"),
            ],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")],
        ]
    )



def balance_keyboard(has_pending_topup: bool) -> InlineKeyboardMarkup:
    topup_text = "➕ Пополнить баланс"
    if has_pending_topup:
        topup_text = "⏳ Заявка на пополнение уже создана"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=topup_text, callback_data="balance_topup")],
            [
                InlineKeyboardButton(text="⭐ Купить звезды", callback_data="buy_stars"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="balance_system"),
            ],
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
        for plan_key, plan in SALE_ACCESS_PLANS.items():
            buttons.append([
                InlineKeyboardButton(
                    text=f"{plan['title']} — {plan['price']}₽",
                    callback_data=f"saleplan:{plan_key}",
                )
            ])

    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="seller_sale_access")])
    buttons.append([InlineKeyboardButton(text="🏠 В меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)



def sale_access_payment_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
        return to_decimal(SETTINGS.get("price_50_99", str(PRICE_50_99_D)))
    if 100 <= stars < 5000:
        return to_decimal(SETTINGS.get("price_100_4999", str(PRICE_100_4999_D)))
    if stars >= 5000:
        return to_decimal(SETTINGS.get("price_5000_plus", str(PRICE_5000_PLUS_D)))
    raise ValueError("Минимальный заказ — 50 звёзд")


def sale_price_per_star() -> Decimal:
    return to_decimal(SETTINGS.get("sale_price_per_star", str(SALE_PRICE_PER_STAR_D)))



def calc_total(stars: int) -> Decimal:
    return (calc_price_per_star(stars) * Decimal(stars)).quantize(Decimal("0.01"))



def money_text(value: Decimal | float | str | int | None) -> str:
    return f"{Decimal(str(value or 0)).quantize(Decimal('0.01'))}"


def payment_requisites_text() -> str:
    if PAYMENT_REQUISITES_TEXT:
        return PAYMENT_REQUISITES_TEXT.replace("\\n", "\n")
    return f"Карта: <code>{CARD_DISPLAY}</code>\nПолучатель: <b>{CARD_HOLDER}</b>"


def payment_comment_text(data: dict[str, Any]) -> str:
    comment = str(data.get("payment_comment") or "").strip()
    if not comment:
        return ""
    return (
        "⚠️ <b>Комментарий к переводу:</b>\n"
        f"<code>{comment}</code>\n"
        "Скопируйте без изменений."
    )


def support_text() -> str:
    return f"Если понадобится помощь, напишите в поддержку: <code>{SUPPORT_USERNAME}</code>"



def price_breakdown_text(stars: int) -> str:
    price = calc_price_per_star(stars)
    total = calc_total(stars)
    return (
        f"⭐ <b>Звёзды:</b> <code>{stars}</code>\n"
        f"💎 <b>Цена:</b> <code>{price}</code> ₽ за 1 ⭐\n"
        f"💰 <b>Итого:</b> <code>{total}</code> RUB"
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


async def present_bot_screen(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
        return
    except Exception:
        pass

    try:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
        return
    except Exception:
        pass

    sent_message = await message.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)
    try:
        await message.delete()
    except Exception:
        pass
    return sent_message


async def present_order_screen(
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    delete_user_message: bool = False,
) -> None:
    data = await state.get_data()
    screen_message_id = data.get("order_screen_message_id")
    if screen_message_id:
        try:
            await bot.edit_message_text(
                text=text,
                chat_id=message.chat.id,
                message_id=int(screen_message_id),
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            if delete_user_message:
                try:
                    await message.delete()
                except Exception:
                    pass
            return
        except Exception:
            pass

    sent_message = None
    try:
        await message.edit_text(text, reply_markup=reply_markup, disable_web_page_preview=True)
        sent_message = message
    except Exception:
        try:
            await message.edit_caption(caption=text, reply_markup=reply_markup)
            sent_message = message
        except Exception:
            sent_message = await message.answer(text, reply_markup=reply_markup, disable_web_page_preview=True)
            try:
                await message.delete()
            except Exception:
                pass

    await state.update_data(order_screen_message_id=sent_message.message_id)
    if delete_user_message:
        try:
            await message.delete()
        except Exception:
            pass


async def get_http_session() -> aiohttp.ClientSession:
    global HTTP_SESSION
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = aiohttp.ClientSession(timeout=HTTP_TIMEOUT)
    return HTTP_SESSION


async def close_http_session() -> None:
    global HTTP_SESSION
    if HTTP_SESSION is not None and not HTTP_SESSION.closed:
        await HTTP_SESSION.close()
    HTTP_SESSION = None


def load_welcome_photo_file_id() -> str:
    if WELCOME_PHOTO_FILE_ID:
        return WELCOME_PHOTO_FILE_ID
    try:
        with open(WELCOME_PHOTO_FILE_ID_PATH, "r", encoding="utf-8") as file:
            return file.read().strip()
    except OSError:
        return ""


def save_welcome_photo_file_id(file_id: str) -> None:
    if not file_id or WELCOME_PHOTO_FILE_ID:
        return
    try:
        with open(WELCOME_PHOTO_FILE_ID_PATH, "w", encoding="utf-8") as file:
            file.write(file_id)
    except OSError:
        logging.exception("Не удалось сохранить file_id приветственного фото")


async def send_welcome_message(message: Message, text: str) -> Message:
    cached_file_id = load_welcome_photo_file_id()
    if cached_file_id:
        try:
            return await message.answer_photo(
                photo=cached_file_id,
                caption=text,
                reply_markup=main_menu_keyboard(),
            )
        except Exception:
            logging.exception("Не удалось отправить приветственное фото по file_id")

    if WELCOME_PHOTO_PATH and os.path.exists(WELCOME_PHOTO_PATH):
        try:
            sent_message = await message.answer_photo(
                photo=FSInputFile(WELCOME_PHOTO_PATH),
                caption=text,
                reply_markup=main_menu_keyboard(),
            )
            if sent_message.photo:
                save_welcome_photo_file_id(sent_message.photo[-1].file_id)
            return sent_message
        except Exception:
            logging.exception("Не удалось отправить приветственное фото")

    return await message.answer(text, reply_markup=main_menu_keyboard(), disable_web_page_preview=True)


async def http_request(method: str, url: str, json_body: dict[str, Any] | None = None) -> tuple[int, Any]:
    headers: dict[str, str] = {}
    if url.startswith(BACKEND_URL) and BACKEND_ADMIN_TOKEN:
        headers["X-Backend-Token"] = BACKEND_ADMIN_TOKEN
    session = await get_http_session()
    async with session.request(method, url, json=json_body, headers=headers) as response:
        text = await response.text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = text
        return response.status, data


async def refresh_settings(force: bool = False) -> dict[str, str]:
    global SETTINGS_CACHE_EXPIRES_AT
    now = asyncio.get_running_loop().time()
    if not force and now < SETTINGS_CACHE_EXPIRES_AT:
        return SETTINGS

    status_code, result = await http_request("GET", f"{BACKEND_URL}/admin/settings")
    if status_code < 400 and isinstance(result, dict) and isinstance(result.get("settings"), dict):
        SETTINGS.update({str(key): str(value) for key, value in result["settings"].items()})
        SETTINGS_CACHE_EXPIRES_AT = now + SETTINGS_CACHE_TTL_SECONDS
    return SETTINGS


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
    now = asyncio.get_running_loop().time()
    cached_stars = _tonviewer_stats_cache.get("stars")
    if cached_stars is not None and float(_tonviewer_stats_cache.get("expires_at") or 0) > now:
        return int(cached_stars)

    total_stars = await asyncio.to_thread(fetch_tonviewer_sold_stars_sync)
    if total_stars is None:
        return None

    _tonviewer_stats_cache["stars"] = total_stars
    _tonviewer_stats_cache["expires_at"] = now + TONVIEWER_STATS_CACHE_TTL_SECONDS
    return total_stars


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


async def check_fragment_recipient(username: str, quantity: int = 50) -> tuple[int, Any]:
    safe_username = quote_plus(username)
    return await http_request("GET", f"{BACKEND_URL}/fragment/check-recipient?username={safe_username}&quantity={quantity}")


async def register_referral_user(message: Message) -> tuple[dict | None, dict | None, str | None, bool]:
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
        return None, None, None, False

    profile = result.get("profile") if isinstance(result, dict) else None
    inviter = result.get("inviter") if isinstance(result, dict) else None
    referral_bound_now = bool(result.get("referral_bound_now")) if isinstance(result, dict) else False
    return profile, inviter, inviter_code, referral_bound_now


async def get_referral_profile(telegram_user_id: int) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/referral/users/{telegram_user_id}")


async def get_balance_profile(telegram_user_id: int) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/balance/users/{telegram_user_id}")


async def get_sale_access_profile(telegram_user_id: int) -> tuple[int, Any]:
    return await http_request("GET", f"{BACKEND_URL}/sale-access/users/{telegram_user_id}")


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


async def send_admin_notification(order_id: str, order_data: dict[str, Any], customer: Message | CallbackQuery) -> None:
    from_user = customer.from_user
    client_username = f"@{from_user.username}" if from_user and from_user.username else "без username"

    recipient_username = order_data.get("recipient_username", "—")
    stars_amount = order_data.get("stars_amount") or order_data.get("stars_count") or order_data.get("stars") or 0
    amount_rub = order_data.get("amount_rub") or order_data.get("amount") or "0.00"

    text = (
        "🕊 <b>Новая заявка на проверку оплаты</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        f"👤 <b>Клиент:</b> {from_user.full_name if from_user else '—'}\n"
        f"🔹 <b>Telegram ID:</b> <code>{from_user.id if from_user else 0}</code>\n"
        f"📎 <b>Username клиента:</b> {client_username}\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Звёзд:</b> <code>{stars_amount}</code>\n"
        f"💰 <b>Сумма:</b> <code>{money_text(amount_rub)}</code> RUB"
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
        f"⭐ <b>Цена выкупа:</b> <code>{money_text(request_data.get('price_per_star', sale_price_per_star()))}</code> ₽/шт"
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
    new_username = f"@{new_user.from_user.username}" if new_user.from_user and new_user.from_user.username else new_user.from_user.full_name
    text = (
        "🎉<b>Новый пользователь!</b>\n\n"
        f"🤝 <b>Партнёр:</b> {user_mention_or_id(inviter)}\n"
        f"🆕 <b>Новый пользователь:</b> {new_username}\n"
        f"🔹 <b>ID нового пользователя:</b> <code>{new_user.from_user.id}</code>\n"
        f"👥 <b>Всего приглашено у партнёра:</b> <code>{invite_count}</code>"
    )
    payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML"}
    await http_request("POST", f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage", payload)


async def send_partner_new_referral_notification(inviter: dict, new_user: Message, invite_count: int) -> None:
    status_code, result = await get_referral_profile(int(inviter["telegram_user_id"]))
    profile = result.get("profile") if status_code < 400 and isinstance(result, dict) else {}
    profile_user = profile.get("user") or inviter
    total_earned = (
        Decimal(str(profile_user.get("ref_balance_available", 0)))
        + Decimal(str(profile_user.get("ref_balance_frozen", 0)))
        + Decimal(str(profile_user.get("ref_total_paid", 0)))
    )
    new_username = f"@{new_user.from_user.username}" if new_user.from_user and new_user.from_user.username else new_user.from_user.full_name
    text = (
        "🎉  <b>У вас новый реферал!</b>\n\n"
        f"🤝 <b>Реферал:</b>{new_username}\n"
        f"🆕 <b>Новый пользователь:</b>{new_user.from_user.full_name}\n"
        f"😎 <b>Всего пригласил:</b> <code>{invite_count}</code>\n"
        f"💸 <b>Ваш оборот:</b> <code>{money_text(profile_user.get('ref_total_turnover', 0))}</code> ₽\n"
        f"💰 <b>Общий заработок:</b> <code>{money_text(total_earned)}</code> ₽"
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
    await refresh_settings()
    user = target.from_user
    status_code, result = await get_referral_profile(user.id)
    if status_code >= 400:
        text = "❌ <b>Не удалось загрузить реферальный раздел</b>\n\n" f"<code>{parse_backend_error(result)}</code>"
        if isinstance(target, CallbackQuery):
            await present_bot_screen(target.message, text, reply_markup=main_menu_keyboard())
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
        "Приглашайте людей и получайте процент с чистой прибыли их покупок.\n\n"
        f"🔗 <b>Ссылка:</b> <code>{ref_link}</code>\n"
        f"🏷 <b>Код:</b> <code>{ref_code}</code>\n"
        f"🤝 <b>Пригласил вас:</b> {inviter_text}\n\n"
        f"🏅 <b>Уровень:</b> <code>{level.get('name', '1 уровень')}</code> · <code>{level.get('percent', 15)}</code>%\n"
        f"👤 <b>Приглашено:</b> <code>{profile.get('invite_count', 0)}</code>\n"
        f"💰 <b>Оборот:</b> <code>{money_text(profile_user.get('ref_total_turnover', 0))}</code> ₽\n"
        f"🪙 <b>Заработано:</b> <code>{money_text(total_earned)}</code> ₽\n\n"
        f"💼 <b>Доступно:</b> <code>{money_text(profile_user.get('ref_balance_available', 0))}</code> ₽\n"
        f"🔒 <b>Заморожено:</b> <code>{money_text(profile_user.get('ref_balance_frozen', 0))}</code> ₽\n"
        f"💸 <b>Выплачено:</b> <code>{money_text(profile_user.get('ref_total_paid', 0))}</code> ₽\n"
        f"🏧 <b>Вывод от:</b> <code>{money_text(profile.get('min_withdraw', 500))}</code> ₽\n"
        f"📋 <b>Заявка:</b> {pending_text}\n"
    )
    if level.get("next_turnover"):
        text += f"🎯 <b>До следующего уровня:</b> <code>{money_text(level.get('remaining_to_next_level', 0))}</code> ₽ оборота\n"
    else:
        text += "👑 <b>У вас максимальный уровень.</b>\n"
    text += "\n🧾 <b>Последние начисления</b>\n" + "\n".join(recent_lines)

    keyboard = referral_keyboard(
        can_withdraw=bool(profile.get("can_withdraw")),
        has_pending_withdrawal=bool(profile.get("has_pending_withdrawal")),
    )
    if isinstance(target, CallbackQuery):
        await present_bot_screen(target.message, text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


async def render_balance_profile_message(target: Message | CallbackQuery) -> None:
    user = target.from_user
    status_code, result = await get_balance_profile(user.id)
    if status_code >= 400:
        text = "❌ <b>Не удалось загрузить баланс</b>\n\n" f"<code>{parse_backend_error(result)}</code>"
        if isinstance(target, CallbackQuery):
            await present_bot_screen(target.message, text, reply_markup=main_menu_keyboard())
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
        "💳 <b>Баланс AraStars</b>\n"
        "Быстрая оплата без повторного ввода реквизитов.\n\n"
        f"💎 <b>Доступно:</b> <code>{money_text(profile_user.get('balance', 0))}</code> ₽\n"
        f"📥 <b>Пополнено:</b> <code>{money_text(profile_user.get('total_deposited', 0))}</code> ₽\n"
        f"📤 <b>Потрачено:</b> <code>{money_text(profile_user.get('total_spent', 0))}</code> ₽\n"
        f"⏳ <b>Пополнение:</b> {pending_text}\n\n"
        "🧾 <b>Последние операции</b>\n" + "\n".join(recent_lines)
    )
    keyboard = balance_keyboard(has_pending_topup=bool(profile.get("has_pending_topup")))
    if isinstance(target, CallbackQuery):
        await present_bot_screen(target.message, text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


async def render_order_history_message(target: Message | CallbackQuery) -> None:
    user_id = target.from_user.id
    status_code, result = await get_order_history(user_id, 10)
    if status_code >= 400 or not isinstance(result, dict):
        text = "❌ <b>Не удалось загрузить историю заказов</b>"
        if isinstance(target, CallbackQuery):
            await present_bot_screen(target.message, text, reply_markup=main_menu_keyboard())
            await target.answer()
        else:
            await target.answer(text, reply_markup=main_menu_keyboard())
        return

    history = result.get("history") or {}
    orders = history.get("orders") or []
    lines = []
    for order in orders[:10]:
        status_map = {
            "payment_pending": "⏳ ожидает оплаты",
            "paid": "✅ выполнен",
            "failed": "❌ отменён",
            "draft": "📝 ожидает перевода",
        }
        lines.append(
            f"• <code>{format_datetime_text(order.get('created_at'))}</code> — "
            f"<code>{order.get('stars_amount', 0)}</code> ⭐ / <code>{money_text(order.get('amount_rub', 0))}</code> ₽ "
            f"({status_map.get(order.get('status'), order.get('status', '—'))})"
        )
    if not lines:
        lines.append("• Заказов пока нет")

    text = (
        "📜 <b>История заказов</b>\n"
        "Ваши покупки в одном аккуратном списке.\n\n"
        f"⭐ <b>Звёзд куплено:</b> <code>{history.get('paid_stars', 0)}</code>\n"
        f"💰 <b>Оплачено:</b> <code>{money_text(history.get('paid_rub', 0))}</code> ₽\n"
        f"🧾 <b>Заказов:</b> <code>{history.get('orders_count', 0)}</code>\n\n"
        "🪄 <b>Последние заказы</b>\n"
        + "\n".join(lines)
    )
    if isinstance(target, CallbackQuery):
        await present_bot_screen(target.message, text, reply_markup=main_menu_keyboard())
        await target.answer()
    else:
        await target.answer(text, reply_markup=main_menu_keyboard())


async def render_sale_access_message(target: Message | CallbackQuery) -> None:
    user = target.from_user
    status_code, result = await get_sale_access_profile(user.id)
    if status_code >= 400:
        text = "❌ <b>Не удалось загрузить продажу звёзд</b>\n\n" f"<code>{parse_backend_error(result)}</code>"
        if isinstance(target, CallbackQuery):
            await present_bot_screen(target.message, text, reply_markup=main_menu_keyboard())
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
        "💸 <b>Продажа звёзд</b>\n"
        "Раздел для тех, кто хочет работать с оборотом, а не просто покупать.\n\n"
        f"💎 <b>Выкуп:</b> <code>{money_text(profile.get('price_per_star', sale_price_per_star()))}</code> ₽ за 1 ⭐\n"
        "Доступ открывается по тарифу и активируется после подтверждения оплаты.\n\n"
        + "\n".join(status_lines)
        + "\n\n"
        + "📦 <b>Тарифы</b>\n"
        + "\n".join(
            f"• {plan['title']} — <code>{plan['price']}</code> ₽"
            for plan in SALE_ACCESS_PLANS.values()
        )
        + "\n\n🧾 <b>Последние заявки</b>\n"
        + "\n".join(recent_lines)
    )

    keyboard = sale_access_keyboard(profile)
    if isinstance(target, CallbackQuery):
        await present_bot_screen(target.message, text, reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard)


async def send_sale_access_payment_message(target_message: Message, request_data: dict[str, Any]) -> None:
    request_id = str(request_data.get("id") or "")
    await present_bot_screen(
        target_message,
        "✅ <b>Заявка на продажу звёзд создана</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
        f"📦 <b>Тариф:</b> <code>{request_data.get('plan_title', '—')}</code>\n"
        f"🗓 <b>Срок:</b> <code>{request_data.get('days', 0)}</code> дн.\n"
        f"💰 <b>Сумма к оплате:</b> <code>{money_text(request_data.get('amount', 0))}</code> RUB\n"
        f"⭐ <b>Цена выкупа:</b> <code>{money_text(request_data.get('price_per_star', sale_price_per_star()))}</code> ₽/шт\n\n"
        "💳 <b>Реквизиты для оплаты</b>\n"
        f"{payment_requisites_text()}\n\n"
        f"{payment_comment_text(request_data)}\n\n"
        "После перевода ничего нажимать не нужно: бот сам найдёт оплату и активирует доступ.\n\n"
        f"{support_text()}",
        reply_markup=sale_access_payment_keyboard(request_id),
    )
    return
    await target_message.answer(
        "✅ <b>Заявка на продажу звёзд создана</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
        f"📦 <b>Тариф:</b> <code>{request_data.get('plan_title', '—')}</code>\n"
        f"🗓 <b>Срок:</b> <code>{request_data.get('days', 0)}</code> дн.\n"
        f"💰 <b>Сумма к оплате:</b> <code>{money_text(request_data.get('amount', 0))}</code> RUB\n"
        f"⭐ <b>Цена выкупа:</b> <code>{money_text(request_data.get('price_per_star', sale_price_per_star()))}</code> ₽/шт\n\n"
        "💳 <b>Оплата на карту</b>\n"
        f"Карта: <code>{CARD_DISPLAY}</code>\n"
        f"Получатель: <b>{CARD_HOLDER}</b>\n\n"
        "После перевода ничего нажимать не нужно: бот сам найдёт оплату и активирует доступ.",
        reply_markup=sale_access_payment_keyboard(request_id),
    )


async def create_and_send_order_message(
    target_message: Message,
    sender: Message | CallbackQuery,
    state: FSMContext,
    recipient_username: str,
    stars: int,
    edit_existing: bool = False,
) -> None:
    total = calc_total(stars)
    user = sender.from_user
    status_code, result = await create_order(
        tg_user_id=user.id,
        username=user.username or "",
        full_name=user.full_name or "",
        recipient_username=recipient_username,
        stars=stars,
        amount_rub=total,
    )
    if status_code >= 400:
        await present_order_screen(
            target_message,
            state,
            "❌ <b>Не удалось создать заказ</b>\n\nПопробуйте ещё раз немного позже.",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    order_id = extract_order_id(result)
    order = result.get("order") if isinstance(result, dict) else {}
    text = (
        "✅ <b>Заказ сформирован</b>\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Звёзды:</b> <code>{stars}</code>\n"
        f"💰 <b>К оплате:</b> <code>{money_text(total)}</code> RUB\n\n"
        "💳 <b>Реквизиты</b>\n"
        f"{payment_requisites_text()}\n\n"
        f"{payment_comment_text(order)}\n\n"
        "После перевода нажмите «Я оплатил», чтобы отправить заказ в проверку."
    )
    if edit_existing:
        await present_order_screen(target_message, state, text, reply_markup=payment_keyboard(order_id))
    else:
        await target_message.answer(text, reply_markup=payment_keyboard(order_id), disable_web_page_preview=True)
    await state.clear()
    return
    await target_message.answer(
        "✅ <b>Заказ сформирован</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Количество звёзд:</b> <code>{stars}</code>\n"
        f"💰 <b>Сумма к оплате:</b> <code>{money_text(total)}</code> RUB\n\n"
        "💳 <b>Оплата на карту</b>\n"
        f"Карта: <code>{CARD_DISPLAY}</code>\n"
        f"Получатель: <b>{CARD_HOLDER}</b>\n\n"
        "После перевода ничего нажимать не нужно: бот сам найдёт оплату и приступит к выполнению заказа.",
        reply_markup=payment_keyboard(order_id),
    )


async def create_and_send_balance_order_message(
    target_message: Message,
    sender: Message | CallbackQuery,
    state: FSMContext,
    recipient_username: str,
    stars: int,
    edit_existing: bool = False,
) -> None:
    total = calc_total(stars)
    user = sender.from_user
    status_code, result = await create_order_from_balance(
        tg_user_id=user.id,
        username=user.username or "",
        full_name=user.full_name or "",
        recipient_username=recipient_username,
        stars=stars,
        amount_rub=total,
    )
    if status_code >= 400:
        await present_order_screen(
            target_message,
            state,
            "❌ <b>Не удалось оплатить заказ с баланса</b>\n\n"
            f"<code>{parse_backend_error(result)}</code>",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    order = result.get("order") if isinstance(result, dict) else {}
    profile_user = result.get("user") if isinstance(result, dict) else {}
    text = (
        "✅ <b>Покупка с баланса выполнена</b>\n"
        "Заказ уже передан в автоматическую выдачу.\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order.get('id', '—')}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Звёзды:</b> <code>{stars}</code>\n"
        f"💸 <b>Списано:</b> <code>{money_text(total)}</code> RUB\n"
        f"💰 <b>Остаток:</b> <code>{money_text(profile_user.get('balance', 0))}</code> RUB"
    )
    if edit_existing:
        await present_order_screen(target_message, state, text, reply_markup=main_menu_keyboard())
    else:
        await target_message.answer(text, reply_markup=main_menu_keyboard())
    await state.clear()


async def show_payment_options_message(
    target_message: Message,
    state: FSMContext,
    telegram_user_id: int,
    recipient_username: str,
    stars: int,
    edit_existing: bool = False,
    delete_user_message: bool = False,
) -> None:
    total = calc_total(stars)
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
        "📋 <b>Данные заказа</b>\n"
        f"🎁 <b>Получатель:</b> <code>{recipient_username}</code>\n"
        f"⭐ <b>Звёзды:</b> <code>{stars}</code>\n"
        f"💎 <b>Цена:</b> <code>{calc_price_per_star(stars)}</code> ₽ за 1 ⭐\n"
        f"💰 <b>Итого:</b> <code>{money_text(total)}</code> RUB\n"
        f"💳 <b>Ваш баланс:</b> <code>{money_text(balance)}</code> RUB\n\n"
    )
    if can_use_balance:
        text += "Выберите способ оплаты:"
    else:
        text += "На балансе недостаточно средств, но можно оплатить по реквизитам."

    if edit_existing:
        await present_order_screen(
            target_message,
            state,
            text,
            reply_markup=payment_choice_keyboard(can_use_balance=can_use_balance),
            delete_user_message=delete_user_message,
        )
    else:
        await present_order_screen(
            target_message,
            state,
            text,
            reply_markup=payment_choice_keyboard(can_use_balance=can_use_balance),
            delete_user_message=delete_user_message,
        )


@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    settings_task = asyncio.create_task(refresh_settings())
    register_task = asyncio.create_task(register_referral_user(message))
    stats_task = asyncio.create_task(http_request("GET", f"{BACKEND_URL}/admin/stats"))
    tonviewer_task = asyncio.create_task(get_tonviewer_sold_stars())

    try:
        await settings_task
    except Exception:
        logging.exception("Не удалось обновить настройки для стартового меню")

    profile = inviter = inviter_code = None
    referral_bound_now = False
    try:
        profile, inviter, inviter_code, referral_bound_now = await register_task
    except Exception:
        logging.exception("Не удалось зарегистрировать пользователя для стартового меню")

    stats_text = "Пока статистика сервиса загружается."
    stats_status = 0
    stats_result = None
    try:
        stats_status, stats_result = await asyncio.wait_for(stats_task, timeout=2.5)
    except TimeoutError:
        stats_task.cancel()
        logging.warning("Статистика для стартового меню не успела загрузиться")
    except Exception:
        logging.exception("Не удалось загрузить статистику для стартового меню")

    if stats_status < 400 and isinstance(stats_result, dict):
        stats = stats_result.get("stats") or {}
        all_time = (stats.get("periods") or {}).get("all_time") or {}
        bought_stars = int(all_time.get("turnover_stars") or 0)
        bought_usd = money_text(all_time.get("turnover_usd") or 0)
        tonviewer_url = SETTINGS.get("tonviewer_url", PROJECT_TONVIEWER_URL)
        stats_text = (
            f"Куплено через сервис: <code>{bought_stars}</code> ⭐ (<code>{bought_usd}</code> $)\n"
            f'Оборот можно проверить: <a href="{tonviewer_url}">кошелек</a>'
        )

    try:
        tonviewer_stars = await asyncio.wait_for(tonviewer_task, timeout=2.0)
        if tonviewer_stars is not None:
            tonviewer_usd = (Decimal(tonviewer_stars) / Decimal("100") * Decimal("1.5")).quantize(Decimal("0.01"))
            tonviewer_url = SETTINGS.get("tonviewer_url", PROJECT_TONVIEWER_URL)
            stats_text = (
                f"Куплено через сервис: <code>{tonviewer_stars}</code> ⭐ (<code>{money_text(tonviewer_usd)}</code> $)\n"
                f'Оборот можно проверить: <a href="{tonviewer_url}">кошелек</a>'
            )
    except TimeoutError:
        tonviewer_task.cancel()
        logging.warning("Статистика TonViewer не успела загрузиться для /start")
    except Exception:
        logging.exception("Не удалось получить статистику TonViewer")

    text = (
        "✨ <b>AraStars</b>\n"
        "Telegram Stars — быстро, аккуратно, без лишних шагов.\n\n"
        "Выберите количество, укажите получателя и оплатите удобным способом. Проверку платежа бот возьмёт на себя.\n\n"
        f"{stats_text}\n\n"
        "👇 Выберите действие:"
    )
    if inviter_code:
        if inviter:
            inviter_username = inviter.get("telegram_username") or ""
            inviter_name = f"@{inviter_username}" if inviter_username else f"ID {inviter.get('telegram_user_id')}"
            text += f"\n\n🤝 Вы зашли по реферальной ссылке партнёра {inviter_name}."
        else:
            text += "\n\n⚠️ Реферальный код не удалось применить."

    sent_message = await send_welcome_message(message, text)

    if sent_message:
        await state.update_data(order_screen_message_id=sent_message.message_id)
    try:
        await message.delete()
    except Exception:
        pass

    if referral_bound_now and inviter and profile:
        try:
            await send_partner_new_referral_notification(inviter, message, int(profile.get("invite_count", 0)))
            await send_admin_new_referral_notification(message, inviter, int(profile.get("invite_count", 0)))
        except Exception:
            logging.exception("Не удалось отправить уведомление о новом реферале")


@dp.message(Command("menu"))
async def menu_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await present_order_screen(
        message,
        state,
        "Главное меню:",
        reply_markup=main_menu_keyboard(),
        delete_user_message=True,
    )


@dp.callback_query(F.data == "back_menu")
async def back_menu_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await present_bot_screen(callback.message, "Главное меню:", reply_markup=main_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "show_prices")
async def show_prices_handler(callback: CallbackQuery) -> None:
    await refresh_settings()
    text = (
        "💰 <b>Актуальный прайс</b>\n\n"
        f"• от 50 до 99 звёзд — <b>{calc_price_per_star(50)}</b> ₽ за 1 звезду\n"
        f"• от 100 до 4999 звёзд — <b>{calc_price_per_star(100)}</b> ₽ за 1 звезду\n"
        f"• от 5000 звёзд — <b>{calc_price_per_star(5000)}</b> ₽ за 1 звезду\n\n"
        f"💸 Для активных продавцов: <b>{sale_price_per_star()}</b> ₽ за 1 звезду\n\n"
        f"Курс доллара для экономики: <b>{SETTINGS.get('usd_rate', '90')}</b> ₽\n"
        "Минимальный заказ: <b>50</b> звёзд"
    )
    await present_bot_screen(callback.message, text, reply_markup=main_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "show_help")
async def show_help_handler(callback: CallbackQuery) -> None:
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "Как проходит покупка:\n"
        "1. Вы выбираете количество звёзд\n"
        "2. Указываете получателя\n"
        "3. Выбираете оплату с баланса или по реквизитам\n"
        "4. При оплате по реквизитам копируете комментарий из бота и вставляете его в перевод\n"
        "5. После перевода нажимаете «Я оплатил», и заказ уходит в проверку\n\n"
        "Если возникнет вопрос, обратитесь в поддержку."
    )
    await present_bot_screen(callback.message, text, reply_markup=main_menu_keyboard())
    await callback.answer()
    return
    text = (
        "ℹ️ <b>Помощь</b>\n\n"
        "Как проходит покупка:\n"
        "1. Вы выбираете количество звёзд\n"
        "2. Бот показывает цену и итоговую сумму\n"
        "3. Вы отправляете username получателя\n"
        "4. Выбираете: с баланса или по реквизитам\n"
        "5. При оплате по реквизитам копируете комментарий и переводите сумму\n"
        "6. При оплате с баланса звёзды отправляются сразу\n\n"
        "По продаже звёзд:\n"
        "• открываете раздел «Продать звёзды»\n"
        "• выбираете тариф и оплачиваете его\n"
        "• после подтверждения оплаты доступ активируется автоматически\n\n"
        "По реферальной системе:\n"
        "• вы получаете процент с оплаченных заказов приглашённых пользователей\n"
        "• процент растёт по карьерной лестнице\n"
        "• вывод доступен от 500 ₽ через заявку"
    )
    await present_bot_screen(callback.message, text, reply_markup=main_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "show_support")
async def show_support_handler(callback: CallbackQuery) -> None:
    text = "🆘 <b>Поддержка</b>\n\nПо всем вопросам обращайтесь:\n<code>@AraStarsS_Manager</code>"
    await present_bot_screen(callback.message, text, reply_markup=main_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "sell_stars")
async def sell_stars_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "💼 <b>Стать продавцом звезд</b>\n\n"
        "Выберите направление:"
    )
    await present_bot_screen(callback.message, text, reply_markup=seller_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "seller_own_bot")
async def seller_own_bot_handler(callback: CallbackQuery) -> None:
    text = (
        "🤖 <b>Свой бот</b>\n\n"
        "Данный функционал пока что будет в разработке. Добавим позже, когда у нас уже будет немалый оборот."
    )
    await present_bot_screen(callback.message, text, reply_markup=seller_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "seller_referral")
async def seller_referral_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await ref_system_handler(callback, state)


@dp.callback_query(F.data == "seller_sale_access")
async def seller_sale_access_handler(callback: CallbackQuery, state: FSMContext) -> None:
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
        await present_bot_screen(
            callback.message,
            "❌ <b>Не удалось получить заявку</b>\n\nПопробуйте ещё раз немного позже."
        )
        await callback.answer()
        return

    request_data = result.get("request") if isinstance(result, dict) else {}
    if request_data.get("status") != "pending":
        await present_bot_screen(
            callback.message,
            "ℹ️ <b>Заявка уже обработана</b>\n\n"
            f"📦 <b>Тариф:</b> <code>{request_data.get('plan_title', '—')}</code>\n"
            f"📋 <b>Статус:</b> <code>{request_data.get('status', '—')}</code>"
        )
        await callback.answer()
        return

    try:
        await send_sale_access_admin_notification(request_id, request_data, callback)
        await present_bot_screen(
            callback.message,
            "⏳ <b>Платёж отправлен на проверку</b>\n\n"
            f"🆔 <b>ID заявки:</b> <code>{request_id}</code>\n"
            f"📦 <b>Тариф:</b> <code>{request_data.get('plan_title', '—')}</code>\n\n"
            "Как только оплата будет подтверждена, доступ к продаже звёзд включится автоматически."
        )
    except Exception:
        logging.exception("Ошибка при отправке заявки на продажу звёзд админу")
        await present_bot_screen(
            callback.message,
            "❌ <b>Не удалось отправить платёж на проверку</b>\n\nПопробуйте ещё раз немного позже."
        )
    await callback.answer("Передаю заявку на проверку...")


@dp.callback_query(F.data == "balance_system")
async def balance_system_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render_balance_profile_message(callback)


@dp.callback_query(F.data == "order_history")
async def order_history_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render_order_history_message(callback)


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
    await present_order_screen(
        callback.message,
        state,
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
    await present_bot_screen(callback.message, build_levels_message(profile), reply_markup=keyboard)
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
    await present_bot_screen(callback.message, build_my_referrals_message(profile), reply_markup=keyboard)
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
    await present_order_screen(
        callback.message,
        state,
        "🏧 <b>Создание заявки на вывод</b>\n\n"
        f"Доступный баланс: <code>{money_text(profile.get('user', {}).get('ref_balance_available', 0))}</code> ₽\n"
        "Отправьте реквизиты для выплаты одним сообщением.\n\n"
        "Пример: <code>2200 7000 1234 5678 Иван Иванов</code>"
    )
    await callback.answer()


@dp.callback_query(F.data == "buy_stars")
async def buy_stars_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "⭐ <b>Покупка Telegram Stars</b>\n"
        "Сначала выберите получателя. Дальше бот покажет сумму и оплату в одном экране.\n\n"
        "🙋 <b>Себе</b> — используем ваш username\n"
        "🎁 <b>Другому</b> — вы укажете получателя вручную"
    )
    await present_bot_screen(callback.message, text, reply_markup=buy_type_keyboard())
    await state.update_data(order_screen_message_id=callback.message.message_id)
    await callback.answer()


@dp.callback_query(F.data == "buy_self")
async def buy_self_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await refresh_settings()
    await state.clear()
    await state.update_data(mode="self")
    await state.set_state(OrderStates.waiting_for_stars)
    await present_order_screen(
        callback.message,
        state,
        "✨ <b>Звёзды для себя</b>\n"
        "Выберите быстрый вариант или введите своё количество.\n\n"
        "Минимум: <code>50</code> ⭐",
        reply_markup=stars_amount_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "buy_other")
async def buy_other_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await refresh_settings()
    await state.clear()
    await state.update_data(mode="other")
    await state.set_state(OrderStates.waiting_for_stars)
    await present_order_screen(
        callback.message,
        state,
        "🎁 <b>Звёзды для другого пользователя</b>\n"
        "Выберите количество. Получателя попросим следующим шагом.\n\n"
        "Минимум: <code>50</code> ⭐",
        reply_markup=stars_amount_keyboard(),
    )
    await callback.answer()


@dp.callback_query(F.data == "stars_custom")
async def stars_custom_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(OrderStates.waiting_for_stars)
    await present_order_screen(
        callback.message,
        state,
        "💠 <b>Введите своё количество звёзд</b>\n\n"
        "Минимум: <code>50</code>\n"
        "Пример: <code>250</code>"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("starsquick:"))
async def stars_quick_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await refresh_settings()
    try:
        stars = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректная кнопка", show_alert=True)
        return
    data = await state.get_data()
    mode = str(data.get("mode", "")).strip()
    await state.update_data(stars=stars)
    if mode == "self":
        own_username = normalize_username(callback.from_user.username or "")
        if own_username:
            status_code, result = await check_fragment_recipient(own_username, stars)
            if status_code < 400 and isinstance(result, dict) and result.get("found"):
                await show_payment_options_message(
                    callback.message,
                    state,
                    callback.from_user.id,
                    own_username,
                    stars,
                    edit_existing=True,
                )
                await callback.answer()
                return
    await state.set_state(OrderStates.waiting_for_recipient)
    await present_order_screen(
        callback.message,
        state,
        "🎁 <b>Получатель</b>\n\n"
        f"{price_breakdown_text(stars)}\n\n"
        "Отправьте username получателя одним сообщением:\n"
        "<code>@username</code>",
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
    await create_and_send_order_message(callback.message, callback, state, recipient_username, stars, edit_existing=True)
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
    await create_and_send_balance_order_message(callback.message, callback, state, recipient_username, stars, edit_existing=True)
    await callback.answer()


@dp.message(OrderStates.waiting_for_stars)
async def stars_amount_handler(message: Message, state: FSMContext) -> None:
    await refresh_settings()
    raw_value = (message.text or "").strip()
    if not raw_value.isdigit():
        await present_order_screen(
            message,
            state,
            "❌ <b>Некорректное количество</b>\n\nВведите число от <b>50</b> и выше.\nПример: <code>100</code>",
            delete_user_message=True,
        )
        return
    stars = int(raw_value)
    if stars < 50:
        await present_order_screen(
            message,
            state,
            "❌ <b>Некорректное количество</b>\n\nВведите число от <b>50</b> и выше.\nПример: <code>100</code>",
            delete_user_message=True,
        )
        return

    data = await state.get_data()
    mode = str(data.get("mode", "")).strip()
    await state.update_data(stars=stars)
    breakdown = price_breakdown_text(stars)

    if mode == "self":
        own_username = normalize_username(message.from_user.username or "")
        if own_username:
            status_code, result = await check_fragment_recipient(own_username, stars)
            if status_code >= 400:
                await state.set_state(OrderStates.waiting_for_recipient)
                await present_order_screen(
                    message,
                    state,
                    f"{breakdown}\n\n❌ <b>Не удалось проверить username</b>\n\nПопробуйте указать другой username вручную:\n<code>@username</code>",
                    delete_user_message=True,
                )
                return
            if isinstance(result, dict) and result.get("found"):
                await show_payment_options_message(
                    message,
                    state,
                    message.from_user.id,
                    own_username,
                    stars,
                    delete_user_message=True,
                )
                return

            await state.set_state(OrderStates.waiting_for_recipient)
            await present_order_screen(
                message,
                state,
                f"{breakdown}\n\n❌ <b>Ваш username не найден</b>\n\nFragment не смог найти ваш аккаунт для покупки звёзд.\nВы можете указать другой username вручную:\n<code>@username</code>",
                delete_user_message=True,
            )
            return

        await state.set_state(OrderStates.waiting_for_recipient)
        await present_order_screen(
            message,
            state,
            "🎁 <b>Получатель</b>\n\n"
            f"{breakdown}\n\n"
            "Отправьте username получателя одним сообщением:\n"
            "<code>@username</code>",
            delete_user_message=True,
        )
        return

    await state.set_state(OrderStates.waiting_for_recipient)
    await present_order_screen(
        message,
        state,
        "🎁 <b>Получатель</b>\n\n"
        f"{breakdown}\n\n"
        "Отправьте username получателя одним сообщением:\n"
        "<code>@username</code>",
        delete_user_message=True,
    )


@dp.message(OrderStates.waiting_for_recipient)
async def recipient_handler(message: Message, state: FSMContext) -> None:
    recipient_username = normalize_username(message.text or "")
    if not is_valid_username(recipient_username):
        await present_order_screen(
            message,
            state,
            "❌ <b>Некорректный username</b>\n\nОтправьте username в формате:\n<code>@username</code>",
            delete_user_message=True,
        )
        return

    data = await state.get_data()
    stars = int(data.get("stars") or 0)
    if stars < 50:
        await state.set_state(OrderStates.waiting_for_stars)
        await present_order_screen(
            message,
            state,
            "✨ Покупка Telegram Stars\n\nВведите количество звёзд, которое хотите купить.\n\nМожно заказать от <b>50</b> звёзд и больше.\nНапример: <code>100</code>",
            delete_user_message=True,
        )
        return

    status_code, result = await check_fragment_recipient(recipient_username, stars)
    if status_code >= 400:
        await present_order_screen(
            message,
            state,
            "❌ <b>Не удалось проверить username</b>\n\nПопробуйте ещё раз через пару секунд.",
            delete_user_message=True,
        )
        return
    if not isinstance(result, dict) or not result.get("found"):
        await present_order_screen(
            message,
            state,
            "❌ <b>Получатель не найден</b>\n\nFragment не находит такой username.\nПроверьте правильность написания и отправьте другой username.",
            delete_user_message=True,
        )
        return

    await show_payment_options_message(
        message,
        state,
        message.from_user.id,
        recipient_username,
        stars,
        delete_user_message=True,
    )


@dp.message(OrderStates.waiting_for_withdraw_requisites)
async def withdraw_requisites_handler(message: Message, state: FSMContext) -> None:
    requisites = (message.text or "").strip()
    if len(requisites) < 5:
        await present_order_screen(
            message,
            state,
            "❌ <b>Слишком короткие реквизиты</b>\n\nОтправьте реквизиты одним сообщением.",
            delete_user_message=True,
        )
        return

    status_code, result = await create_withdrawal_request(message.from_user.id, requisites)
    if status_code >= 400:
        await present_order_screen(
            message,
            state,
            "❌ <b>Не удалось создать заявку</b>\n\n" f"<code>{parse_backend_error(result)}</code>",
            reply_markup=main_menu_keyboard(),
            delete_user_message=True,
        )
        await state.clear()
        return

    withdrawal = result.get("withdrawal") if isinstance(result, dict) else {}
    user = result.get("user") if isinstance(result, dict) else {}
    try:
        await send_withdraw_admin_notification(message, withdrawal, user)
    except Exception:
        logging.exception("Ошибка при отправке заявки на вывод админу")

    await present_order_screen(
        message,
        state,
        "✅ <b>Заявка на вывод создана</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{withdrawal.get('id', '—')}</code>\n"
        f"💸 <b>Сумма:</b> <code>{money_text(withdrawal.get('amount', 0))}</code> RUB\n"
        "Заявка отправлена админу на обработку.",
        reply_markup=main_menu_keyboard(),
        delete_user_message=True,
    )
    await state.clear()


@dp.message(OrderStates.waiting_for_balance_topup_amount)
async def balance_topup_amount_handler(message: Message, state: FSMContext) -> None:
    raw_value = (message.text or "").replace(",", ".").strip()
    try:
        amount = Decimal(raw_value).quantize(Decimal("0.01"))
    except Exception:
        await present_order_screen(
            message,
            state,
            "❌ <b>Некорректная сумма</b>\n\nВведите сумму числом. Например: <code>500</code>",
            delete_user_message=True,
        )
        return
    if amount <= 0:
        await present_order_screen(
            message,
            state,
            "❌ <b>Сумма должна быть больше 0</b>",
            delete_user_message=True,
        )
        return

    status_code, result = await create_balance_topup_request(
        telegram_user_id=message.from_user.id,
        telegram_username=message.from_user.username or "",
        telegram_full_name=message.from_user.full_name or "",
        amount=amount,
    )
    if status_code >= 400:
        await present_order_screen(
            message,
            state,
            "❌ <b>Не удалось создать заявку на пополнение</b>\n\n"
            f"<code>{parse_backend_error(result)}</code>",
            reply_markup=main_menu_keyboard(),
            delete_user_message=True,
        )
        await state.clear()
        return

    topup = result.get("topup") if isinstance(result, dict) else {}
    await present_order_screen(
        message,
        state,
        "✅ <b>Заявка на пополнение баланса создана</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{topup.get('id', '—')}</code>\n"
        f"💸 <b>Сумма:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB\n\n"
        "💳 <b>Реквизиты для оплаты</b>\n"
        f"{payment_requisites_text()}\n\n"
        f"{payment_comment_text(topup)}\n\n"
        "После перевода ничего нажимать не нужно: бот сам найдёт оплату и пополнит баланс.\n\n"
        f"{support_text()}",
        delete_user_message=True,
    )
    try:
        await send_topup_admin_notification(message, topup)
    except Exception:
        logging.exception("Ошибка при отправке уведомления о пополнении")
    await state.clear()
    return

    await message.answer(
        "✅ <b>Заявка на пополнение создана</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{topup.get('id', '—')}</code>\n"
        f"💸 <b>Сумма:</b> <code>{money_text(topup.get('amount', 0))}</code> RUB\n\n"
        "💳 <b>Реквизиты для оплаты:</b>\n"
        f"Карта: <code>{CARD_DISPLAY}</code>\n"
        f"Получатель: <b>{CARD_HOLDER}</b>\n\n"
        "После поступления перевода админ зачислит деньги на ваш баланс.",
        reply_markup=main_menu_keyboard(),
    )


@dp.callback_query(F.data.startswith("clientpaid:"))
async def client_paid_callback(callback: CallbackQuery) -> None:
    await callback.answer("Передаю заказ на проверку...")
    try:
        order_id = callback.data.split(":", 1)[1]
        status_code, _ = await mark_client_paid(order_id)
        if status_code >= 400:
            await present_bot_screen(callback.message, "❌ <b>Не удалось отправить платёж на проверку</b>\n\nПопробуйте ещё раз немного позже.")
            return
        status_code, order_result = await get_order(order_id)
        if status_code >= 400:
            await present_bot_screen(callback.message, "❌ <b>Не удалось получить заказ</b>\n\nПопробуйте ещё раз немного позже.")
            return
        order = order_result.get("order") if isinstance(order_result, dict) else {}
        if not isinstance(order, dict):
            order = {}
        await send_admin_notification(order_id, order, callback)
        await present_bot_screen(
            callback.message,
            "⏳ <b>Платёж отправлен на проверку</b>\n\n"
            f"🆔 <b>ID заказа:</b> <code>{order_id}</code>\n\n"
            "Мы уже передали заявку админу.\nКак только оплата будет подтверждена, заказ перейдёт в обработку."
        )
    except Exception:
        logging.exception("Ошибка при отправке на проверку")
        await present_bot_screen(callback.message, "❌ <b>Не удалось отправить платёж на проверку</b>\n\nПопробуйте ещё раз немного позже.")


# ------------------------------------------------------------- Документы ----

LEGAL_DOCUMENTS: dict[str, tuple[str, str]] = {
    "terms": ("📄 Пользовательское соглашение", "terms.txt"),
    "offer": ("📝 Договор оферты", "offer.txt"),
    "privacy": ("🔐 Обработка персональных данных", "privacy.txt"),
    "refund": ("💸 Возврат средств", "refund.txt"),
}

LEGAL_TOKENS: dict[str, str] = {
    "{{SELLER_LINE}}": "seller_line",
    "{{REQUISITES}}": "requisites",
    "{{SELLER_EMAIL}}": "email",
    "{{SUPPORT_TG}}": "support",
    "{{DOC_DATE}}": "date",
}

# Предел Telegram — 4096 символов; берём с запасом на разметку.
LEGAL_CHUNK_LIMIT = 3800

# key -> (mtime файла, готовые куски). Правка .txt подхватывается без рестарта.
_legal_cache: dict[str, tuple[float, list[str]]] = {}


def legal_seller_line() -> str:
    """Как Исполнитель называет себя в тексте документов.

    ФИО и ИНН подставляются, только если заданы в .env. Без них остаётся
    нейтральная формулировка — никаких «не указано» в тексте договора.
    """
    parts: list[str] = []
    if LEGAL_SELLER_NAME:
        parts.append(LEGAL_SELLER_NAME)
    if LEGAL_SELLER_INN:
        parts.append(f"ИНН {LEGAL_SELLER_INN}")
    if not parts:
        # Нейтральная форма: подставляется в «Исполнитель: …», «Владелец
        # Сервиса — …» и «Оператор персональных данных: …» без тавтологии.
        return "лицо, применяющее специальный налоговый режим «Налог на профессиональный доход» (самозанятый)"
    parts.append("применяющий специальный налоговый режим «Налог на профессиональный доход»")
    return ", ".join(parts)


def legal_requisites_lines() -> list[str]:
    lines: list[str] = []
    if LEGAL_SELLER_NAME:
        lines.append(f"<b>Исполнитель:</b> {LEGAL_SELLER_NAME}")
    if LEGAL_SELLER_INN:
        lines.append(f"<b>ИНН:</b> <code>{LEGAL_SELLER_INN}</code>")
    lines.append("<b>Налоговый статус:</b> плательщик налога на профессиональный доход (самозанятый)")
    if LEGAL_SELLER_EMAIL:
        lines.append(f"<b>Почта:</b> {LEGAL_SELLER_EMAIL}")
    lines.append(f"<b>Поддержка:</b> {SUPPORT_USERNAME}")
    return lines


def legal_values() -> dict[str, str]:
    return {
        "seller_line": legal_seller_line(),
        "requisites": "\n".join(legal_requisites_lines()),
        "email": LEGAL_SELLER_EMAIL or SUPPORT_USERNAME,
        "support": SUPPORT_USERNAME,
        "date": LEGAL_DOC_DATE or "—",
    }


def split_for_telegram(text: str, limit: int = LEGAL_CHUNK_LIMIT) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(block) > limit:
            cut = block.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(block[:cut].strip())
            block = block[cut:].lstrip("\n")
        current = block
    if current.strip():
        chunks.append(current)
    return chunks or ["Документ пуст."]


def load_legal_document(key: str) -> list[str]:
    _, filename = LEGAL_DOCUMENTS[key]
    path = os.path.join(LEGAL_DIR, filename)
    stamp = os.path.getmtime(path)

    cached = _legal_cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]

    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    values = legal_values()
    for token, field in LEGAL_TOKENS.items():
        text = text.replace(token, values[field])

    chunks = split_for_telegram(text.strip())
    _legal_cache[key] = (stamp, chunks)
    return chunks


def legal_requisites_text() -> str:
    body = "\n".join(legal_requisites_lines())
    date = LEGAL_DOC_DATE or "—"
    return (
        "🏷 <b>Реквизиты и контакты</b>\n\n"
        f"{body}\n\n"
        f"<i>Документы приведены в редакции от {date}</i>"
    )


def legal_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=title, callback_data=f"legal:{key}")]
        for key, (title, _) in LEGAL_DOCUMENTS.items()
    ]
    rows.append([InlineKeyboardButton(text="🏷 Реквизиты и контакты", callback_data="legal:requisites")])
    rows.append([InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


LEGAL_MENU_TEXT = (
    "📑 <b>Документы сервиса</b>\n\n"
    "Оформляя заказ, вы принимаете условия этих документов.\n"
    "Выберите, что открыть:"
)


@dp.message(Command("police", "policy", "docs"))
async def legal_command_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        LEGAL_MENU_TEXT,
        reply_markup=legal_menu_keyboard(),
        disable_web_page_preview=True,
    )


@dp.callback_query(F.data == "legal_menu")
async def legal_menu_handler(callback: CallbackQuery) -> None:
    await present_bot_screen(callback.message, LEGAL_MENU_TEXT, reply_markup=legal_menu_keyboard())
    await callback.answer()


@dp.callback_query(F.data.startswith("legal:"))
async def legal_document_handler(callback: CallbackQuery) -> None:
    key = (callback.data or "").split(":", 1)[1]

    if key == "requisites":
        await callback.answer()
        await callback.message.answer(
            legal_requisites_text(),
            reply_markup=legal_menu_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if key not in LEGAL_DOCUMENTS:
        await callback.answer("Документ не найден", show_alert=True)
        return

    try:
        chunks = load_legal_document(key)
    except OSError:
        logging.exception("Не удалось прочитать документ %s", key)
        await callback.answer("Документ временно недоступен", show_alert=True)
        return

    await callback.answer()
    for index, chunk in enumerate(chunks):
        await callback.message.answer(
            chunk,
            reply_markup=legal_menu_keyboard() if index == len(chunks) - 1 else None,
            disable_web_page_preview=True,
        )


@dp.callback_query()
async def unknown_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка устарела. Нажмите /start", show_alert=True)


@dp.message()
async def fallback_handler(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def main() -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await close_http_session()


if __name__ == "__main__":
    asyncio.run(main())
