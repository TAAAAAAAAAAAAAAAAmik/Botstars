import asyncio
import json
import logging
import os
import random
import re
import secrets
import sqlite3
import time
import uuid
from http.cookies import SimpleCookie
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Optional

import aiohttp
import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from fragment_service import (
    auto_buy_stars,
    make_session,
    normalize_recipient,
    search_stars_recipient,
)

load_dotenv(dotenv_path=".env")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AraStars Backend")

DB_PATH = os.getenv("ORDERS_DB_PATH", "orders.db").strip() or "orders.db"
REFERRAL_MIN_WITHDRAW = float(os.getenv("REFERRAL_MIN_WITHDRAW", "500").strip() or 500)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
BACKEND_ADMIN_TOKEN = os.getenv("BACKEND_ADMIN_TOKEN", ADMIN_BOT_TOKEN).strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@AraStarsS_Manager").strip() or "@AraStarsS_Manager"
OZON_COOKIES_JSON = os.getenv("OZON_COOKIES_JSON", "").strip()
OZON_USE_SELENIUM = os.getenv("OZON_USE_SELENIUM", "false").strip().lower() in {"1", "true", "yes", "on"}
OZON_CHROME_PROFILE_DIR = (
    os.getenv("OZON_CHROME_PROFILE_DIR", "/root/AraStars/ozon_chrome_profile").strip()
    or "/root/AraStars/ozon_chrome_profile"
)
OZON_HEADLESS = os.getenv("OZON_HEADLESS", "true").strip().lower() in {"1", "true", "yes", "on"}
OZON_CHECK_INTERVAL_SECONDS = int(os.getenv("OZON_CHECK_INTERVAL_SECONDS", "15").strip() or 15)
OZON_COOKIE_KEEPALIVE_INTERVAL_SECONDS = int(os.getenv("OZON_COOKIE_KEEPALIVE_INTERVAL_SECONDS", "600").strip() or 600)
OZON_COOKIE_KEEPALIVE_JITTER_SECONDS = int(os.getenv("OZON_COOKIE_KEEPALIVE_JITTER_SECONDS", "120").strip() or 120)
OZON_COOKIE_SAVE_MIN_INTERVAL_SECONDS = int(os.getenv("OZON_COOKIE_SAVE_MIN_INTERVAL_SECONDS", "300").strip() or 300)
OZON_OPERATIONS_PER_PAGE = int(os.getenv("OZON_OPERATIONS_PER_PAGE", "30").strip() or 30)
OZON_SELENIUM_TIMEOUT_SECONDS = int(os.getenv("OZON_SELENIUM_TIMEOUT_SECONDS", "45").strip() or 45)
OZON_PIN = os.getenv("OZON_PIN", "").strip()
OZON_PIN_WAIT_SECONDS = int(os.getenv("OZON_PIN_WAIT_SECONDS", "12").strip() or 12)
ADMIN_STATS_CACHE_TTL_SECONDS = float(os.getenv("ADMIN_STATS_CACHE_TTL_SECONDS", "10").strip() or 10)
PAYMENT_MATCH_MODE = "comment"
PAYMENT_COMMENT_PREFIX = "ARA"
PAYMENT_WATCHER_TASK: asyncio.Task | None = None
OZON_COOKIE_KEEPALIVE_TASK: asyncio.Task | None = None
OZON_CHECK_LOCK: asyncio.Lock | None = None
OZON_DRIVER = None
OZON_LAST_COOKIE_SAVE_MONOTONIC = 0.0
ADMIN_STATS_CACHE: dict[str, object] = {"expires_at": 0.0, "value": None}
OZON_ENABLED = OZON_USE_SELENIUM or bool(OZON_COOKIES_JSON)
OZON_HEALTH: dict[str, object] = {
    "enabled": OZON_ENABLED,
    "mode": "selenium" if OZON_USE_SELENIUM else "cookies",
    "last_success_at": None,
    "last_error": None,
    "last_operations_count": 0,
    "last_checked_at": None,
    "last_pending_count": 0,
    "last_matched_count": 0,
    "last_fetch_status": None,
    "last_fetch_text_length": 0,
    "last_extract_path": None,
    "last_response_summary": None,
    "last_cookie_refresh_at": None,
    "last_cookie_count": 0,
    "last_keepalive_at": None,
    "last_keepalive_error": None,
    "last_keepalive_operations_count": 0,
    "keepalive_interval_seconds": OZON_COOKIE_KEEPALIVE_INTERVAL_SECONDS,
}
OZON_LAST_FETCH_DEBUG: dict[str, object] = {}

SALE_PRICE_PER_STAR = 1.2
SALE_ACCESS_PLANS: dict[str, dict[str, int | float | str]] = {
    "sale_3d": {"title": "3 дня", "days": 3, "price": 79, "sort_order": 1},
    "sale_7d": {"title": "7 дней", "days": 7, "price": 149, "sort_order": 2},
    "sale_30d": {"title": "1 месяц", "days": 30, "price": 349, "sort_order": 3},
    "sale_365d": {"title": "1 год", "days": 365, "price": 2699, "sort_order": 4},
}

STARS_COST_USD_PER_100 = 1.5
DEFAULT_USD_RATE = os.getenv("USD_RATE", "90").strip() or "90"
FRAGMENT_ACCOUNT_ADDRESS = os.getenv("FRAGMENT_ACCOUNT_ADDRESS", "").strip()
PROJECT_TONVIEWER_URL = f"https://tonviewer.com/{FRAGMENT_ACCOUNT_ADDRESS}" if FRAGMENT_ACCOUNT_ADDRESS else ""
DEFAULT_TONVIEWER_URL = (
    os.getenv("TONVIEWER_URL", PROJECT_TONVIEWER_URL).strip()
    or PROJECT_TONVIEWER_URL
    or "https://tonviewer.com/"
)
DEFAULT_REFERRAL_INFO_URL = (
    os.getenv("REFERRAL_INFO_URL", "https://telegra.ph/Referalnaya-programma-AraStars-05-07").strip()
    or "https://telegra.ph/Referalnaya-programma-AraStars-05-07"
)

DEFAULT_SETTINGS: dict[str, str] = {
    "usd_rate": DEFAULT_USD_RATE,
    "price_50_99": os.getenv("PRICE_50_99", "1.50").strip() or "1.50",
    "price_100_4999": os.getenv("PRICE_100_4999", "1.32").strip() or "1.32",
    "price_5000_plus": os.getenv("PRICE_5000_PLUS", "1.24").strip() or "1.24",
    "sale_price_per_star": os.getenv("SALE_PRICE_PER_STAR", str(SALE_PRICE_PER_STAR)).strip() or str(SALE_PRICE_PER_STAR),
    "tonviewer_url": DEFAULT_TONVIEWER_URL,
    "referral_info_url": DEFAULT_REFERRAL_INFO_URL,
    "stars_cost_usd_per_100": str(STARS_COST_USD_PER_100),
}

DIRECT_REFERRAL_LEVELS = [
    {"level": 1, "name": "1 уровень", "percent": 15, "min_turnover": 0, "next_turnover": 1000},
    {"level": 2, "name": "2 уровень", "percent": 25, "min_turnover": 1000, "next_turnover": 7000},
    {"level": 3, "name": "3 уровень", "percent": 40, "min_turnover": 7000, "next_turnover": 15000},
    {"level": 4, "name": "4 уровень", "percent": 65, "min_turnover": 15000, "next_turnover": 25000},
    {"level": 5, "name": "5 уровень", "percent": 80, "min_turnover": 25000, "next_turnover": 40000},
    {"level": 6, "name": "MAX", "percent": 90, "min_turnover": 40000, "next_turnover": None},
]

NETWORK_REFERRAL_LEVELS = [
    {"level": 1, "name": "1 уровень", "percent": 2, "min_turnover": 0, "next_turnover": 1000},
    {"level": 2, "name": "2 уровень", "percent": 3.5, "min_turnover": 1000, "next_turnover": 5000},
    {"level": 3, "name": "3 уровень", "percent": 5, "min_turnover": 5000, "next_turnover": 10000},
    {"level": 4, "name": "4 уровень", "percent": 7.5, "min_turnover": 10000, "next_turnover": 26000},
    {"level": 5, "name": "5 уровень", "percent": 10, "min_turnover": 26000, "next_turnover": None},
]
MAX_REFERRAL_CHAIN_DEPTH = 5


class OrderStatus(str, Enum):
    draft = "draft"
    payment_pending = "payment_pending"
    paid = "paid"
    failed = "failed"


class WithdrawalStatus(str, Enum):
    pending = "pending"
    paid = "paid"
    rejected = "rejected"


class BalanceTopupStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class SaleAccessRequestStatus(str, Enum):
    pending = "pending"
    active = "active"
    rejected = "rejected"


class OrderCreateRequest(BaseModel):
    telegram_user_id: int
    telegram_username: str = ""
    telegram_full_name: str = ""
    recipient_username: str = Field(..., min_length=2)
    stars_amount: int = Field(..., ge=50)

    stars_count: Optional[int] = None
    stars: Optional[int] = None

    amount_rub: float = Field(..., gt=0)
    amount: Optional[float] = None
    status: Optional[str] = None


class BalanceOrderCreateRequest(BaseModel):
    telegram_user_id: int
    telegram_username: str = ""
    telegram_full_name: str = ""
    recipient_username: str = Field(..., min_length=2)
    stars_amount: int = Field(..., ge=50)
    amount_rub: float = Field(..., gt=0)


class OrderStatusUpdateRequest(BaseModel):
    order_id: str
    status: str


class RegisterUserRequest(BaseModel):
    telegram_user_id: int
    telegram_username: str = ""
    telegram_full_name: str = ""
    inviter_code: Optional[str] = None


class WithdrawalCreateRequest(BaseModel):
    telegram_user_id: int
    requisites: str = Field(..., min_length=5)
    amount: Optional[float] = Field(default=None, gt=0)


class WithdrawalProcessRequest(BaseModel):
    processed_by: Optional[int] = None


class BalanceTopupCreateRequest(BaseModel):
    telegram_user_id: int
    telegram_username: str = ""
    telegram_full_name: str = ""
    amount: float = Field(..., gt=0)


class BalanceTopupProcessRequest(BaseModel):
    processed_by: Optional[int] = None


class SaleAccessCreateRequest(BaseModel):
    telegram_user_id: int
    telegram_username: str = ""
    telegram_full_name: str = ""
    plan_key: str = Field(..., min_length=3)


class SaleAccessProcessRequest(BaseModel):
    processed_by: Optional[int] = None


class AdminSettingsUpdateRequest(BaseModel):
    settings: dict[str, str | int | float] = Field(default_factory=dict)


class AdminOzonCookiesUpdateRequest(BaseModel):
    cookies: str = Field(..., min_length=10)
    use_selenium: bool = True


class AdminBalanceAdjustRequest(BaseModel):
    amount: float
    note: str = "Ручная корректировка админом"
    processed_by: Optional[int] = None


class AdminBlacklistRequest(BaseModel):
    is_blacklisted: bool
    reason: str = ""
    processed_by: Optional[int] = None


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_amount(value: float | int | str | None) -> float:
    return round(float(value or 0), 2)


def amount_to_cents(value: float | int | str | None) -> int:
    return int(round(normalize_amount(value) * 100))


def get_ozon_check_lock() -> asyncio.Lock:
    global OZON_CHECK_LOCK
    if OZON_CHECK_LOCK is None:
        OZON_CHECK_LOCK = asyncio.Lock()
    return OZON_CHECK_LOCK


def parse_ozon_cookie_input(raw: str) -> dict[str, str]:
    value = raw.strip()
    if value.startswith("OZON_COOKIES_JSON="):
        value = value.split("=", 1)[1].strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    if not value:
        raise HTTPException(status_code=400, detail="Cookie header is empty")

    if value.startswith("["):
        data = json.loads(value)
        if not isinstance(data, list):
            raise HTTPException(status_code=400, detail="Cookie JSON array is invalid")
        cookies: dict[str, str] = {}
        for item in data:
            if isinstance(item, dict) and item.get("name"):
                cookies[str(item["name"])] = str(item.get("value", ""))
        if cookies:
            return cookies

    if value.startswith("{"):
        data = json.loads(value)
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="Cookie JSON must be an object")
        return {str(key): str(item) for key, item in data.items()}

    parsed = SimpleCookie()
    try:
        parsed.load(value)
    except Exception:
        parsed = SimpleCookie()
    cookies = {key: morsel.value for key, morsel in parsed.items()}
    if cookies:
        return cookies

    for chunk in value.split(";"):
        if "=" not in chunk:
            continue
        name, cookie_value = chunk.split("=", 1)
        name = name.strip()
        if name:
            cookies[name] = cookie_value.strip()
    if not cookies:
        raise HTTPException(status_code=400, detail="Could not parse Ozon cookies")
    return cookies


def update_env_file_values(updates: dict[str, str]) -> None:
    env_path = ".env"
    try:
        with open(env_path, "r", encoding="utf-8") as file:
            lines = file.read().splitlines()
    except FileNotFoundError:
        lines = []

    seen: set[str] = set()
    updated_lines: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                updated_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        updated_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            updated_lines.append(f"{key}={value}")
    with open(env_path, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(updated_lines) + "\n")


def apply_ozon_runtime_config(cookies_json: str, use_selenium: bool) -> None:
    global OZON_COOKIES_JSON, OZON_USE_SELENIUM, OZON_ENABLED
    OZON_COOKIES_JSON = cookies_json
    OZON_USE_SELENIUM = use_selenium
    OZON_ENABLED = OZON_USE_SELENIUM or bool(OZON_COOKIES_JSON)
    OZON_HEALTH.update(
        {
            "enabled": OZON_ENABLED,
            "mode": "selenium" if OZON_USE_SELENIUM else "cookies",
            "last_error": None,
        }
    )


def save_ozon_cookies_from_driver(driver, *, force: bool = False) -> int:
    global OZON_LAST_COOKIE_SAVE_MONOTONIC
    try:
        browser_cookies = driver.get_cookies()
    except Exception:
        logger.exception("Failed to read Ozon browser cookies")
        return 0

    cookies = {
        str(item.get("name")): str(item.get("value") or "")
        for item in browser_cookies
        if item.get("name") and str(item.get("domain") or "").endswith("ozon.ru")
    }
    if not cookies:
        return 0

    now_monotonic = time.monotonic()
    if (
        not force
        and OZON_COOKIE_SAVE_MIN_INTERVAL_SECONDS > 0
        and now_monotonic - OZON_LAST_COOKIE_SAVE_MONOTONIC < OZON_COOKIE_SAVE_MIN_INTERVAL_SECONDS
    ):
        OZON_HEALTH["last_cookie_count"] = len(cookies)
        return len(cookies)

    cookies_json = json.dumps(cookies, ensure_ascii=False, separators=(",", ":"))
    if cookies_json != OZON_COOKIES_JSON:
        update_env_file_values(
            {
                "OZON_COOKIES_JSON": cookies_json,
                "OZON_USE_SELENIUM": "true" if OZON_USE_SELENIUM else "false",
                "OZON_PIN": OZON_PIN or "7910",
            }
        )
        apply_ozon_runtime_config(cookies_json, OZON_USE_SELENIUM)
    OZON_LAST_COOKIE_SAVE_MONOTONIC = now_monotonic

    OZON_HEALTH.update(
        {
            "last_cookie_refresh_at": now_iso(),
            "last_cookie_count": len(cookies),
        }
    )
    return len(cookies)


def add_ozon_cookie_to_driver(driver, name: str, value: str) -> bool:
    base = {
        "name": name,
        "value": value,
        "path": "/",
        "secure": True,
        "expiry": int(time.time()) + 7 * 24 * 60 * 60,
    }
    for domain in (".ozon.ru", ".finance.ozon.ru", "finance.ozon.ru", None):
        cookie = dict(base)
        if domain:
            cookie["domain"] = domain
        try:
            driver.add_cookie(cookie)
            return True
        except Exception:
            continue
    return False


def require_admin_token(x_backend_token: str | None = Header(default=None, alias="X-Backend-Token")) -> None:
    if not BACKEND_ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="Backend admin token is not configured")
    if not x_backend_token or not secrets.compare_digest(x_backend_token, BACKEND_ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid backend token")


def generate_payment_comment() -> str:
    return f"{PAYMENT_COMMENT_PREFIX}-{uuid.uuid4().hex[:8].upper()}"


def make_operation_id(operation: dict) -> str:
    raw_id = (
        operation.get("id")
        or operation.get("operationId")
        or operation.get("transactionId")
        or operation.get("uuid")
    )
    if raw_id:
        return str(raw_id)
    amount = ((operation.get("accountAmount") or {}).get("cents"))
    return "|".join(
        [
            str(operation.get("time") or ""),
            str(operation.get("purpose") or ""),
            str(operation.get("comment") or ""),
            str(amount or ""),
        ]
    )


def parse_ozon_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_payment_text(value: str | None) -> str:
    return " ".join(str(value or "").upper().split())


def operation_has_comment(operation: dict, payment_comment: str) -> bool:
    expected = normalize_payment_text(payment_comment)
    if not expected:
        return False
    haystack = normalize_payment_text(
        f"{operation.get('comment') or ''} {operation.get('purpose') or ''}"
    )
    return expected in haystack


def operation_matches_payment(operation: dict, payment: dict) -> bool:
    operation_amount = ((operation.get("accountAmount") or {}).get("cents"))
    try:
        operation_cents = int(operation_amount)
    except (TypeError, ValueError):
        return False
    if operation_cents <= 0:
        return False

    expected_cents = amount_to_cents(payment.get("payable_amount") or payment.get("amount"))
    if operation_cents != expected_cents:
        return False

    operation_time = parse_ozon_time(operation.get("time"))
    created_at = parse_iso_datetime(payment.get("created_at"))
    if operation_time and created_at:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if operation_time < created_at.astimezone(UTC) - timedelta(minutes=2):
            return False

    return operation_has_comment(operation, str(payment.get("payment_comment") or ""))


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def is_future_iso(value: str | None) -> bool:
    dt = parse_iso_datetime(value)
    if dt is None:
        return False
    return dt > datetime.now(UTC)


def get_sale_plan(plan_key: str) -> dict:
    plan = SALE_ACCESS_PLANS.get(plan_key)
    if not plan:
        raise HTTPException(status_code=400, detail="Unknown sale access plan")
    return plan


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str:
    fallback = DEFAULT_SETTINGS.get(key, default if default is not None else "")
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return str(fallback)
    if row is None:
        return str(fallback)
    return str(row["value"])


def get_float_setting(conn: sqlite3.Connection, key: str, default: float) -> float:
    raw = get_setting(conn, key, str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def set_setting(conn: sqlite3.Connection, key: str, value: str | int | float) -> None:
    if key not in DEFAULT_SETTINGS:
        raise HTTPException(status_code=400, detail=f"Unsupported setting: {key}")
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, str(value), now_iso()),
    )


def get_settings_dict(conn: sqlite3.Connection) -> dict[str, str]:
    result = dict(DEFAULT_SETTINGS)
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    except sqlite3.OperationalError:
        return result
    for row in rows:
        result[str(row["key"])] = str(row["value"])
    return result


def get_price_settings_dict(conn: sqlite3.Connection) -> dict[str, str]:
    settings = get_settings_dict(conn)
    return {
        "price_50_99": settings["price_50_99"],
        "price_100_4999": settings["price_100_4999"],
        "price_5000_plus": settings["price_5000_plus"],
        "sale_price_per_star": settings["sale_price_per_star"],
    }


def get_price_per_star(conn: sqlite3.Connection, stars: int) -> float:
    if 50 <= stars < 100:
        return get_float_setting(conn, "price_50_99", float(DEFAULT_SETTINGS["price_50_99"]))
    if 100 <= stars < 5000:
        return get_float_setting(conn, "price_100_4999", float(DEFAULT_SETTINGS["price_100_4999"]))
    if stars >= 5000:
        return get_float_setting(conn, "price_5000_plus", float(DEFAULT_SETTINGS["price_5000_plus"]))
    raise HTTPException(status_code=400, detail="stars_amount must be >= 50")


def get_sale_price_per_star(conn: sqlite3.Connection) -> float:
    return get_float_setting(conn, "sale_price_per_star", SALE_PRICE_PER_STAR)


def calculate_order_amount_rub(conn: sqlite3.Connection, stars: int) -> float:
    return normalize_amount(get_price_per_star(conn, stars) * float(stars))


def calculate_star_cost_rub(conn: sqlite3.Connection, stars: int) -> float:
    usd_rate = get_float_setting(conn, "usd_rate", float(DEFAULT_USD_RATE))
    cost_usd_per_100 = get_float_setting(conn, "stars_cost_usd_per_100", STARS_COST_USD_PER_100)
    return normalize_amount((float(stars) / 100.0) * cost_usd_per_100 * usd_rate)


def calculate_order_profit(conn: sqlite3.Connection, order: dict) -> dict[str, float]:
    amount = normalize_amount(order.get("amount_rub") or order.get("amount") or 0)
    stars = int(order.get("stars_amount") or order.get("stars_count") or order.get("stars") or 0)
    star_cost = calculate_star_cost_rub(conn, stars)
    base_profit = normalize_amount(max(0, amount - star_cost))
    return {
        "amount_rub": amount,
        "stars_cost_rub": star_cost,
        "referral_base_profit": base_profit,
    }


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {row[1] for row in rows}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def ensure_payment_columns(conn: sqlite3.Connection, table_name: str) -> None:
    ensure_column(conn, table_name, "payment_comment", "TEXT")
    ensure_column(conn, table_name, "payable_amount", "REAL")
    ensure_column(conn, table_name, "payment_match_mode", "TEXT NOT NULL DEFAULT 'comment'")
    ensure_column(conn, table_name, "paid_operation_id", "TEXT")
    ensure_column(conn, table_name, "paid_operation_time", "TEXT")
    ensure_column(conn, table_name, "processing_notified_at", "TEXT")
    ensure_column(conn, table_name, "customer_notified_at", "TEXT")


def create_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            telegram_user_id INTEGER NOT NULL,
            telegram_username TEXT NOT NULL,
            telegram_full_name TEXT NOT NULL,
            recipient_username TEXT NOT NULL,
            stars_amount INTEGER NOT NULL,
            stars_count INTEGER NOT NULL,
            stars INTEGER NOT NULL,
            amount_rub REAL NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            payment_source TEXT NOT NULL DEFAULT 'card',
            balance_charge_amount REAL NOT NULL DEFAULT 0,
            payment_comment TEXT,
            payable_amount REAL,
            payment_match_mode TEXT NOT NULL DEFAULT 'comment',
            paid_operation_id TEXT,
            paid_operation_time TEXT,
            processing_notified_at TEXT,
            customer_notified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            fragment_result TEXT,
            fragment_error TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id INTEGER PRIMARY KEY,
            telegram_username TEXT NOT NULL DEFAULT '',
            telegram_full_name TEXT NOT NULL DEFAULT '',
            ref_code TEXT NOT NULL UNIQUE,
            invited_by_user_id INTEGER,
            ref_balance_available REAL NOT NULL DEFAULT 0,
            ref_balance_frozen REAL NOT NULL DEFAULT 0,
            ref_total_paid REAL NOT NULL DEFAULT 0,
            ref_total_turnover REAL NOT NULL DEFAULT 0,
            ref_level INTEGER NOT NULL DEFAULT 1,
            ref_percent REAL NOT NULL DEFAULT 15,
            custom_ref_percent REAL,
            balance REAL NOT NULL DEFAULT 0,
            total_deposited REAL NOT NULL DEFAULT 0,
            total_spent REAL NOT NULL DEFAULT 0,
            is_blacklisted INTEGER NOT NULL DEFAULT 0,
            blacklist_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(invited_by_user_id) REFERENCES users(telegram_user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS referral_transactions (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL UNIQUE,
            source_order_id TEXT,
            chain_depth INTEGER NOT NULL DEFAULT 1,
            reward_kind TEXT NOT NULL DEFAULT 'direct',
            referrer_user_id INTEGER NOT NULL,
            referral_user_id INTEGER NOT NULL,
            order_amount REAL NOT NULL,
            stars_amount INTEGER NOT NULL DEFAULT 0,
            stars_cost_rub REAL NOT NULL DEFAULT 0,
            base_profit REAL NOT NULL DEFAULT 0,
            reward_amount REAL NOT NULL,
            percent_used REAL NOT NULL,
            level_before INTEGER NOT NULL,
            level_after INTEGER NOT NULL,
            turnover_before REAL NOT NULL,
            turnover_after REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(referrer_user_id) REFERENCES users(telegram_user_id),
            FOREIGN KEY(referral_user_id) REFERENCES users(telegram_user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS withdrawal_requests (
            id TEXT PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            requisites TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            processed_by INTEGER,
            FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_topup_requests (
            id TEXT PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            telegram_username TEXT NOT NULL DEFAULT '',
            telegram_full_name TEXT NOT NULL DEFAULT '',
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            payment_comment TEXT,
            payable_amount REAL,
            payment_match_mode TEXT NOT NULL DEFAULT 'comment',
            paid_operation_id TEXT,
            paid_operation_time TEXT,
            processing_notified_at TEXT,
            customer_notified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            processed_by INTEGER,
            FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_transactions (
            id TEXT PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            related_order_id TEXT,
            related_topup_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sale_access_requests (
            id TEXT PRIMARY KEY,
            telegram_user_id INTEGER NOT NULL,
            telegram_username TEXT NOT NULL DEFAULT '',
            telegram_full_name TEXT NOT NULL DEFAULT '',
            plan_key TEXT NOT NULL,
            plan_title TEXT NOT NULL,
            days INTEGER NOT NULL,
            amount REAL NOT NULL,
            price_per_star REAL NOT NULL DEFAULT 1.2,
            status TEXT NOT NULL,
            payment_comment TEXT,
            payable_amount REAL,
            payment_match_mode TEXT NOT NULL DEFAULT 'comment',
            paid_operation_id TEXT,
            paid_operation_time TEXT,
            processing_notified_at TEXT,
            customer_notified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            processed_by INTEGER,
            activated_at TEXT,
            expires_at TEXT,
            FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
        )
        """
    )

    ensure_column(conn, "orders", "payment_source", "TEXT NOT NULL DEFAULT 'card'")
    ensure_column(conn, "orders", "balance_charge_amount", "REAL NOT NULL DEFAULT 0")
    ensure_payment_columns(conn, "orders")
    ensure_payment_columns(conn, "balance_topup_requests")
    ensure_payment_columns(conn, "sale_access_requests")
    ensure_column(conn, "users", "balance", "REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "users", "total_deposited", "REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "users", "total_spent", "REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "users", "sale_access_until", "TEXT")
    ensure_column(conn, "users", "sale_access_plan_key", "TEXT")
    ensure_column(conn, "users", "sale_price_per_star", "REAL NOT NULL DEFAULT 1.2")
    ensure_column(conn, "users", "is_blacklisted", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "users", "blacklist_reason", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "referral_transactions", "source_order_id", "TEXT")
    ensure_column(conn, "referral_transactions", "chain_depth", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(conn, "referral_transactions", "reward_kind", "TEXT NOT NULL DEFAULT 'direct'")
    ensure_column(conn, "referral_transactions", "stars_amount", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "referral_transactions", "stars_cost_rub", "REAL NOT NULL DEFAULT 0")
    ensure_column(conn, "referral_transactions", "base_profit", "REAL NOT NULL DEFAULT 0")

    now = now_iso()
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, str(value), now),
        )



def init_db() -> None:
    conn = get_db()
    try:
        create_tables(conn)
        conn.commit()
    finally:
        conn.close()



def row_to_order(row: sqlite3.Row) -> dict:
    fragment_result_raw = row["fragment_result"]
    fragment_result = None
    if fragment_result_raw:
        try:
            fragment_result = json.loads(fragment_result_raw)
        except json.JSONDecodeError:
            fragment_result = None

    return {
        "id": row["id"],
        "order_id": row["order_id"],
        "telegram_user_id": row["telegram_user_id"],
        "telegram_username": row["telegram_username"],
        "telegram_full_name": row["telegram_full_name"],
        "recipient_username": row["recipient_username"],
        "stars_amount": row["stars_amount"],
        "stars_count": row["stars_count"],
        "stars": row["stars"],
        "amount_rub": normalize_amount(row["amount_rub"]),
        "amount": normalize_amount(row["amount"]),
        "status": row["status"],
        "payment_source": row["payment_source"],
        "balance_charge_amount": normalize_amount(row["balance_charge_amount"]),
        "payment_comment": row["payment_comment"],
        "payable_amount": normalize_amount(row["payable_amount"] if row["payable_amount"] is not None else row["amount_rub"]),
        "payment_match_mode": row["payment_match_mode"],
        "paid_operation_id": row["paid_operation_id"],
        "paid_operation_time": row["paid_operation_time"],
        "processing_notified_at": row["processing_notified_at"],
        "customer_notified_at": row["customer_notified_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "fragment_result": fragment_result,
        "fragment_error": row["fragment_error"],
    }



def row_to_user(row: sqlite3.Row) -> dict:
    sale_access_until = row["sale_access_until"] if "sale_access_until" in row.keys() else None
    sale_access_plan_key = row["sale_access_plan_key"] if "sale_access_plan_key" in row.keys() else None
    sale_price_per_star = row["sale_price_per_star"] if "sale_price_per_star" in row.keys() else SALE_PRICE_PER_STAR
    is_blacklisted = row["is_blacklisted"] if "is_blacklisted" in row.keys() else 0
    blacklist_reason = row["blacklist_reason"] if "blacklist_reason" in row.keys() else ""
    return {
        "telegram_user_id": row["telegram_user_id"],
        "telegram_username": row["telegram_username"],
        "telegram_full_name": row["telegram_full_name"],
        "ref_code": row["ref_code"],
        "invited_by_user_id": row["invited_by_user_id"],
        "ref_balance_available": normalize_amount(row["ref_balance_available"]),
        "ref_balance_frozen": normalize_amount(row["ref_balance_frozen"]),
        "ref_total_paid": normalize_amount(row["ref_total_paid"]),
        "ref_total_turnover": normalize_amount(row["ref_total_turnover"]),
        "ref_level": int(row["ref_level"] or 1),
        "ref_percent": float(row["ref_percent"] or 15),
        "custom_ref_percent": row["custom_ref_percent"],
        "balance": normalize_amount(row["balance"]),
        "total_deposited": normalize_amount(row["total_deposited"]),
        "total_spent": normalize_amount(row["total_spent"]),
        "sale_access_until": sale_access_until,
        "sale_access_plan_key": sale_access_plan_key,
        "sale_price_per_star": normalize_amount(sale_price_per_star),
        "sale_access_active": is_future_iso(sale_access_until),
        "is_blacklisted": bool(is_blacklisted),
        "blacklist_reason": blacklist_reason or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }



def row_to_referral_transaction(row: sqlite3.Row) -> dict:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "order_id": row["order_id"],
        "source_order_id": row["source_order_id"] if "source_order_id" in keys else row["order_id"],
        "chain_depth": int(row["chain_depth"] if "chain_depth" in keys else 1),
        "reward_kind": row["reward_kind"] if "reward_kind" in keys else "direct",
        "referrer_user_id": row["referrer_user_id"],
        "referral_user_id": row["referral_user_id"],
        "order_amount": normalize_amount(row["order_amount"]),
        "stars_amount": int(row["stars_amount"] if "stars_amount" in keys else 0),
        "stars_cost_rub": normalize_amount(row["stars_cost_rub"] if "stars_cost_rub" in keys else 0),
        "base_profit": normalize_amount(row["base_profit"] if "base_profit" in keys else row["order_amount"]),
        "reward_amount": normalize_amount(row["reward_amount"]),
        "percent_used": float(row["percent_used"] or 0),
        "level_before": int(row["level_before"] or 1),
        "level_after": int(row["level_after"] or 1),
        "turnover_before": normalize_amount(row["turnover_before"]),
        "turnover_after": normalize_amount(row["turnover_after"]),
        "status": row["status"],
        "created_at": row["created_at"],
    }



def row_to_withdrawal(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "telegram_user_id": row["telegram_user_id"],
        "amount": normalize_amount(row["amount"]),
        "requisites": row["requisites"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "processed_by": row["processed_by"],
    }



def row_to_topup(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "telegram_user_id": row["telegram_user_id"],
        "telegram_username": row["telegram_username"],
        "telegram_full_name": row["telegram_full_name"],
        "amount": normalize_amount(row["amount"]),
        "status": row["status"],
        "payment_comment": row["payment_comment"],
        "payable_amount": normalize_amount(row["payable_amount"] if row["payable_amount"] is not None else row["amount"]),
        "payment_match_mode": row["payment_match_mode"],
        "paid_operation_id": row["paid_operation_id"],
        "paid_operation_time": row["paid_operation_time"],
        "processing_notified_at": row["processing_notified_at"],
        "customer_notified_at": row["customer_notified_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "processed_by": row["processed_by"],
    }



def row_to_balance_transaction(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "telegram_user_id": row["telegram_user_id"],
        "type": row["type"],
        "amount": normalize_amount(row["amount"]),
        "status": row["status"],
        "note": row["note"],
        "related_order_id": row["related_order_id"],
        "related_topup_id": row["related_topup_id"],
        "created_at": row["created_at"],
    }



def row_to_sale_access_request(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "telegram_user_id": row["telegram_user_id"],
        "telegram_username": row["telegram_username"],
        "telegram_full_name": row["telegram_full_name"],
        "plan_key": row["plan_key"],
        "plan_title": row["plan_title"],
        "days": int(row["days"] or 0),
        "amount": normalize_amount(row["amount"]),
        "price_per_star": normalize_amount(row["price_per_star"]),
        "status": row["status"],
        "payment_comment": row["payment_comment"],
        "payable_amount": normalize_amount(row["payable_amount"] if row["payable_amount"] is not None else row["amount"]),
        "payment_match_mode": row["payment_match_mode"],
        "paid_operation_id": row["paid_operation_id"],
        "paid_operation_time": row["paid_operation_time"],
        "processing_notified_at": row["processing_notified_at"],
        "customer_notified_at": row["customer_notified_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "processed_by": row["processed_by"],
        "activated_at": row["activated_at"],
        "expires_at": row["expires_at"],
    }



def get_order_or_404(order_id: str) -> dict:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    return row_to_order(row)



def get_user_row(conn: sqlite3.Connection, telegram_user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE telegram_user_id = ?",
        (telegram_user_id,),
    ).fetchone()



def get_user_or_404(telegram_user_id: int) -> dict:
    conn = get_db()
    try:
        row = get_user_row(conn, telegram_user_id)
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row_to_user(row)



def get_user_by_code(conn: sqlite3.Connection, ref_code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE ref_code = ?", (ref_code,)).fetchone()



def get_ref_code_for_user(user_id: int) -> str:
    return f"ARA{int(user_id)}"



def get_level_by_turnover(turnover: float, custom_percent: float | None = None) -> dict:
    turnover = normalize_amount(turnover)
    if custom_percent is not None:
        return {
            "level": 999,
            "name": "VIP",
            "percent": float(custom_percent),
            "next_level": None,
            "next_turnover": None,
            "remaining_to_next_level": 0,
        }

    levels = DIRECT_REFERRAL_LEVELS

    current = levels[0]
    for level in levels:
        if turnover >= level["min_turnover"]:
            current = level
        else:
            break

    next_turnover = current["next_turnover"]
    return {
        **current,
        "next_level": current["level"] + 1 if next_turnover is not None else None,
        "remaining_to_next_level": normalize_amount(max(0, next_turnover - turnover)) if next_turnover is not None else 0,
    }


def get_network_level_by_turnover(turnover: float) -> dict:
    turnover = normalize_amount(turnover)
    current = NETWORK_REFERRAL_LEVELS[0]
    for level in NETWORK_REFERRAL_LEVELS:
        if turnover >= level["min_turnover"]:
            current = level
        else:
            break
    next_turnover = current["next_turnover"]
    return {
        **current,
        "next_level": current["level"] + 1 if next_turnover is not None else None,
        "remaining_to_next_level": normalize_amount(max(0, next_turnover - turnover)) if next_turnover is not None else 0,
    }



def ensure_user_record(
    conn: sqlite3.Connection,
    telegram_user_id: int,
    telegram_username: str = "",
    telegram_full_name: str = "",
) -> dict:
    row = get_user_row(conn, telegram_user_id)
    now = now_iso()
    clean_username = telegram_username or ""
    clean_full_name = telegram_full_name or ""

    if row is None:
        ref_code = get_ref_code_for_user(telegram_user_id)
        level_data = get_level_by_turnover(0)
        conn.execute(
            """
            INSERT INTO users (
                telegram_user_id, telegram_username, telegram_full_name, ref_code,
                invited_by_user_id, ref_balance_available, ref_balance_frozen,
                ref_total_paid, ref_total_turnover, ref_level, ref_percent,
                custom_ref_percent, balance, total_deposited, total_spent,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_user_id,
                clean_username,
                clean_full_name,
                ref_code,
                None,
                0,
                0,
                0,
                0,
                level_data["level"],
                level_data["percent"],
                None,
                0,
                0,
                0,
                now,
                now,
            ),
        )
    else:
        updates: list[str] = []
        values: list[object] = []
        if clean_username and clean_username != row["telegram_username"]:
            updates.append("telegram_username = ?")
            values.append(clean_username)
        if clean_full_name and clean_full_name != row["telegram_full_name"]:
            updates.append("telegram_full_name = ?")
            values.append(clean_full_name)
        if updates:
            updates.append("updated_at = ?")
            values.append(now)
            values.append(telegram_user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE telegram_user_id = ?", values)

    row = get_user_row(conn, telegram_user_id)
    return row_to_user(row)



def bind_user_to_inviter(conn: sqlite3.Connection, telegram_user_id: int, inviter_code: str | None) -> dict | None:
    if not inviter_code:
        return None

    inviter_code = inviter_code.strip().upper()
    if not inviter_code:
        return None

    user_row = get_user_row(conn, telegram_user_id)
    if user_row is None:
        raise HTTPException(status_code=404, detail="User not found")

    if user_row["invited_by_user_id"]:
        inviter_row = get_user_row(conn, int(user_row["invited_by_user_id"]))
        return row_to_user(inviter_row) if inviter_row else None

    inviter_row = get_user_by_code(conn, inviter_code)
    if inviter_row is None:
        raise HTTPException(status_code=404, detail="Inviter not found")

    inviter_id = int(inviter_row["telegram_user_id"])
    if inviter_id == telegram_user_id:
        raise HTTPException(status_code=400, detail="Self referral is not allowed")

    conn.execute(
        "UPDATE users SET invited_by_user_id = ?, updated_at = ? WHERE telegram_user_id = ?",
        (inviter_id, now_iso(), telegram_user_id),
    )
    return row_to_user(inviter_row)



def insert_order(order: dict) -> None:
    conn = get_db()
    try:
        ensure_user_record(
            conn,
            telegram_user_id=order["telegram_user_id"],
            telegram_username=order["telegram_username"],
            telegram_full_name=order["telegram_full_name"],
        )
        conn.execute(
            """
            INSERT INTO orders (
                id, order_id, telegram_user_id, telegram_username, telegram_full_name,
                recipient_username, stars_amount, stars_count, stars, amount_rub,
                amount, status, payment_source, balance_charge_amount,
                payment_comment, payable_amount, payment_match_mode, paid_operation_id,
                paid_operation_time, processing_notified_at, customer_notified_at,
                created_at, updated_at, fragment_result, fragment_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order["id"],
                order["order_id"],
                order["telegram_user_id"],
                order["telegram_username"],
                order["telegram_full_name"],
                order["recipient_username"],
                order["stars_amount"],
                order["stars_count"],
                order["stars"],
                order["amount_rub"],
                order["amount"],
                order["status"],
                order.get("payment_source", "card"),
                order.get("balance_charge_amount", 0),
                order.get("payment_comment"),
                order.get("payable_amount", order.get("amount_rub")),
                order.get("payment_match_mode", PAYMENT_MATCH_MODE),
                order.get("paid_operation_id"),
                order.get("paid_operation_time"),
                order.get("processing_notified_at"),
                order.get("customer_notified_at"),
                order["created_at"],
                order["updated_at"],
                json.dumps(order["fragment_result"], ensure_ascii=False) if order["fragment_result"] is not None else None,
                order["fragment_error"],
            ),
        )
        conn.commit()
    finally:
        conn.close()



def update_order_fields(order_id: str, **fields) -> dict:
    if not fields:
        return get_order_or_404(order_id)

    allowed = {
        "telegram_user_id",
        "telegram_username",
        "telegram_full_name",
        "recipient_username",
        "stars_amount",
        "stars_count",
        "stars",
        "amount_rub",
        "amount",
        "status",
        "payment_source",
        "balance_charge_amount",
        "created_at",
        "updated_at",
        "fragment_result",
        "fragment_error",
        "payment_comment",
        "payable_amount",
        "payment_match_mode",
        "paid_operation_id",
        "paid_operation_time",
        "processing_notified_at",
        "customer_notified_at",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Недопустимые поля для обновления: {unknown}")

    if "fragment_result" in fields:
        value = fields["fragment_result"]
        fields["fragment_result"] = json.dumps(value, ensure_ascii=False) if value is not None else None

    set_clause = ", ".join(f"{key} = ?" for key in fields.keys())
    values = list(fields.values())
    values.append(order_id)

    conn = get_db()
    try:
        cur = conn.execute(f"UPDATE orders SET {set_clause} WHERE id = ?", values)
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Order not found")
    finally:
        conn.close()

    return get_order_or_404(order_id)



def set_order_status(order_id: str, new_status: OrderStatus) -> dict:
    return update_order_fields(order_id, status=new_status.value, updated_at=now_iso())


def mark_order_for_payment_review(order_id: str) -> dict:
    order = get_order_or_404(order_id)
    current_status = str(order.get("status") or "").strip()

    if current_status == OrderStatus.payment_pending.value:
        return order
    if current_status == OrderStatus.paid.value:
        raise HTTPException(status_code=409, detail="Order is already paid")
    if current_status == OrderStatus.failed.value:
        raise HTTPException(status_code=409, detail="Failed order cannot be sent for payment review")
    if current_status != OrderStatus.draft.value:
        raise HTTPException(status_code=409, detail=f"Unsupported order status: {current_status}")

    return set_order_status(order_id, OrderStatus.payment_pending)


def reject_order_internal(order_id: str) -> dict:
    order = get_order_or_404(order_id)
    current_status = str(order.get("status") or "").strip()

    if current_status == OrderStatus.failed.value:
        return order
    if current_status == OrderStatus.paid.value:
        raise HTTPException(status_code=409, detail="Paid order cannot be rejected")
    if current_status not in {OrderStatus.draft.value, OrderStatus.payment_pending.value}:
        raise HTTPException(status_code=409, detail=f"Unsupported order status: {current_status}")

    return set_order_status(order_id, OrderStatus.failed)



def count_orders() -> int:
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM orders").fetchone()
        return int(row["cnt"])
    finally:
        conn.close()



def count_users() -> int:
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
        return int(row["cnt"])
    finally:
        conn.close()


def ensure_user_not_blacklisted(conn: sqlite3.Connection, telegram_user_id: int) -> dict:
    row = get_user_row(conn, telegram_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    user = row_to_user(row)
    if user.get("is_blacklisted"):
        reason = user.get("blacklist_reason") or "Пользователь в чёрном списке"
        raise HTTPException(status_code=403, detail=f"User is blacklisted: {reason}")
    return user


def search_users(query: str, limit: int = 10) -> list[dict]:
    query = (query or "").strip()
    conn = get_db()
    try:
        if not query:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        elif query.lstrip("@").isdigit():
            rows = conn.execute(
                """
                SELECT * FROM users
                WHERE telegram_user_id = ? OR telegram_username LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(query.lstrip("@")), f"%{query.lstrip('@')}%", limit),
            ).fetchall()
        else:
            clean = query.lstrip("@")
            rows = conn.execute(
                """
                SELECT * FROM users
                WHERE telegram_username LIKE ? OR telegram_full_name LIKE ? OR ref_code LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (f"%{clean}%", f"%{query}%", f"%{query.upper()}%", limit),
            ).fetchall()
        return [row_to_user(row) for row in rows]
    finally:
        conn.close()


def build_order_history(telegram_user_id: int, limit: int = 10) -> dict:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM orders
            WHERE telegram_user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_user_id, limit),
        ).fetchall()
        orders = [row_to_order(row) for row in rows]
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS orders_count,
                COALESCE(SUM(CASE WHEN status = ? THEN stars_amount ELSE 0 END), 0) AS paid_stars,
                COALESCE(SUM(CASE WHEN status = ? THEN amount_rub ELSE 0 END), 0) AS paid_rub
            FROM orders
            WHERE telegram_user_id = ?
            """,
            (OrderStatus.paid.value, OrderStatus.paid.value, telegram_user_id),
        ).fetchone()
        return {
            "orders": orders,
            "orders_count": int(totals["orders_count"] or 0),
            "paid_stars": int(totals["paid_stars"] or 0),
            "paid_rub": normalize_amount(totals["paid_rub"] or 0),
        }
    finally:
        conn.close()


def _period_start(days: int | None) -> datetime | None:
    if days is None:
        return None
    return datetime.now(UTC) - timedelta(days=days)


def build_admin_stats() -> dict:
    conn = get_db()
    try:
        settings = get_settings_dict(conn)
        usd_rate = get_float_setting(conn, "usd_rate", float(DEFAULT_USD_RATE))
        all_orders = [row_to_order(row) for row in conn.execute("SELECT * FROM orders").fetchall()]
        all_ref_txs = [row_to_referral_transaction(row) for row in conn.execute("SELECT * FROM referral_transactions").fetchall()]
        users = [row_to_user(row) for row in conn.execute("SELECT * FROM users").fetchall()]

        def in_period(created_at: str, days: int | None) -> bool:
            start = _period_start(days)
            if start is None:
                return True
            dt = parse_iso_datetime(created_at)
            return bool(dt and dt >= start)

        periods = {"day": 1, "three_days": 3, "week": 7, "month": 30, "all_time": None}
        period_stats: dict[str, dict] = {}
        for key, days in periods.items():
            orders = [order for order in all_orders if order["status"] == OrderStatus.paid.value and in_period(order["created_at"], days)]
            ref_txs = [tx for tx in all_ref_txs if in_period(tx["created_at"], days)]
            gross_rub = normalize_amount(sum(float(order["amount_rub"]) for order in orders))
            stars = int(sum(int(order["stars_amount"] or 0) for order in orders))
            stars_cost = normalize_amount(sum(calculate_star_cost_rub(conn, int(order["stars_amount"] or 0)) for order in orders))
            base_profit = normalize_amount(sum(max(0, float(order["amount_rub"]) - calculate_star_cost_rub(conn, int(order["stars_amount"] or 0))) for order in orders))
            referral_paid = normalize_amount(sum(float(tx["reward_amount"]) for tx in ref_txs))
            period_stats[key] = {
                "orders_count": len(orders),
                "turnover_stars": stars,
                "turnover_rub": gross_rub,
                "turnover_usd": normalize_amount(gross_rub / usd_rate if usd_rate else 0),
                "stars_cost_rub": stars_cost,
                "profit_before_referrals": base_profit,
                "referral_rewards": referral_paid,
                "net_profit": normalize_amount(base_profit - referral_paid),
                "average_order_rub": normalize_amount(gross_rub / len(orders) if orders else 0),
            }

        statuses = {}
        for status in [OrderStatus.payment_pending.value, OrderStatus.paid.value, OrderStatus.failed.value, OrderStatus.draft.value]:
            statuses[status] = len([order for order in all_orders if order["status"] == status])

        rows = conn.execute(
            """
            SELECT
                u.*,
                COUNT(child.telegram_user_id) AS invite_count,
                COALESCE(SUM(rt.reward_amount), 0) AS total_reward
            FROM users u
            LEFT JOIN users child ON child.invited_by_user_id = u.telegram_user_id
            LEFT JOIN referral_transactions rt ON rt.referrer_user_id = u.telegram_user_id
            GROUP BY u.telegram_user_id
            ORDER BY total_reward DESC, invite_count DESC
            LIMIT 10
            """
        ).fetchall()
        top_referrers = []
        for row in rows:
            item = row_to_user(row)
            item["invite_count"] = int(row["invite_count"] or 0)
            item["total_reward"] = normalize_amount(row["total_reward"] or 0)
            top_referrers.append(item)

        return {
            "settings": settings,
            "users_total": len(users),
            "blacklisted_users": len([user for user in users if user.get("is_blacklisted")]),
            "new_users": {
                "day": len([user for user in users if in_period(user["created_at"], 1)]),
                "three_days": len([user for user in users if in_period(user["created_at"], 3)]),
                "week": len([user for user in users if in_period(user["created_at"], 7)]),
                "month": len([user for user in users if in_period(user["created_at"], 30)]),
            },
            "orders_today": period_stats["day"]["orders_count"],
            "order_statuses": statuses,
            "periods": period_stats,
            "top_referrers": top_referrers,
        }
    finally:
        conn.close()



def create_balance_transaction(
    conn: sqlite3.Connection,
    telegram_user_id: int,
    tx_type: str,
    amount: float,
    status: str,
    note: str,
    related_order_id: str | None = None,
    related_topup_id: str | None = None,
) -> dict:
    tx_id = str(uuid.uuid4())
    created_at = now_iso()
    conn.execute(
        """
        INSERT INTO balance_transactions (
            id, telegram_user_id, type, amount, status, note,
            related_order_id, related_topup_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tx_id,
            telegram_user_id,
            tx_type,
            normalize_amount(amount),
            status,
            note,
            related_order_id,
            related_topup_id,
            created_at,
        ),
    )
    row = conn.execute("SELECT * FROM balance_transactions WHERE id = ?", (tx_id,)).fetchone()
    return row_to_balance_transaction(row)



def update_balance_transaction_status(conn: sqlite3.Connection, transaction_id: str, status: str) -> dict | None:
    conn.execute("UPDATE balance_transactions SET status = ? WHERE id = ?", (status, transaction_id))
    row = conn.execute("SELECT * FROM balance_transactions WHERE id = ?", (transaction_id,)).fetchone()
    return row_to_balance_transaction(row) if row else None



def get_recent_referral_transactions(conn: sqlite3.Connection, telegram_user_id: int, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM referral_transactions
        WHERE referrer_user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (telegram_user_id, limit),
    ).fetchall()
    return [row_to_referral_transaction(row) for row in rows]



def get_recent_withdrawals(conn: sqlite3.Connection, telegram_user_id: int, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM withdrawal_requests
        WHERE telegram_user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (telegram_user_id, limit),
    ).fetchall()
    return [row_to_withdrawal(row) for row in rows]



def get_recent_balance_transactions(conn: sqlite3.Connection, telegram_user_id: int, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM balance_transactions
        WHERE telegram_user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (telegram_user_id, limit),
    ).fetchall()
    return [row_to_balance_transaction(row) for row in rows]



def get_recent_sale_access_requests(conn: sqlite3.Connection, telegram_user_id: int, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM sale_access_requests
        WHERE telegram_user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (telegram_user_id, limit),
    ).fetchall()
    return [row_to_sale_access_request(row) for row in rows]



def build_referral_profile(conn: sqlite3.Connection, telegram_user_id: int) -> dict:
    user_row = get_user_row(conn, telegram_user_id)
    if user_row is None:
        raise HTTPException(status_code=404, detail="User not found")

    user = row_to_user(user_row)
    current_level = get_level_by_turnover(user["ref_total_turnover"], user["custom_ref_percent"])

    invite_count_row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM users WHERE invited_by_user_id = ?",
        (telegram_user_id,),
    ).fetchone()
    invite_count = int(invite_count_row["cnt"] if invite_count_row else 0)

    pending_withdrawal_row = conn.execute(
        """
        SELECT * FROM withdrawal_requests
        WHERE telegram_user_id = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (telegram_user_id, WithdrawalStatus.pending.value),
    ).fetchone()

    inviter = None
    if user["invited_by_user_id"]:
        inviter_row = get_user_row(conn, int(user["invited_by_user_id"]))
        if inviter_row:
            inviter = row_to_user(inviter_row)

    return {
        "user": user,
        "inviter": inviter,
        "invite_count": invite_count,
        "level": current_level,
        "min_withdraw": REFERRAL_MIN_WITHDRAW,
        "can_withdraw": user["ref_balance_available"] >= REFERRAL_MIN_WITHDRAW,
        "has_pending_withdrawal": bool(pending_withdrawal_row),
        "pending_withdrawal": row_to_withdrawal(pending_withdrawal_row) if pending_withdrawal_row else None,
        "recent_transactions": get_recent_referral_transactions(conn, telegram_user_id),
        "recent_withdrawals": get_recent_withdrawals(conn, telegram_user_id),
    }



def build_balance_profile(conn: sqlite3.Connection, telegram_user_id: int) -> dict:
    user_row = get_user_row(conn, telegram_user_id)
    if user_row is None:
        raise HTTPException(status_code=404, detail="User not found")

    pending_topup_row = conn.execute(
        """
        SELECT * FROM balance_topup_requests
        WHERE telegram_user_id = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (telegram_user_id, BalanceTopupStatus.pending.value),
    ).fetchone()

    return {
        "user": row_to_user(user_row),
        "has_pending_topup": bool(pending_topup_row),
        "pending_topup": row_to_topup(pending_topup_row) if pending_topup_row else None,
        "recent_transactions": get_recent_balance_transactions(conn, telegram_user_id),
    }



def build_sale_access_profile(conn: sqlite3.Connection, telegram_user_id: int) -> dict:
    user_row = get_user_row(conn, telegram_user_id)
    if user_row is None:
        raise HTTPException(status_code=404, detail="User not found")

    user = row_to_user(user_row)
    pending_request_row = conn.execute(
        """
        SELECT * FROM sale_access_requests
        WHERE telegram_user_id = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (telegram_user_id, SaleAccessRequestStatus.pending.value),
    ).fetchone()

    current_plan = SALE_ACCESS_PLANS.get(user.get("sale_access_plan_key") or "")
    price_per_star = get_sale_price_per_star(conn)
    plans = [
        {"key": key, **value, "price_per_star": price_per_star}
        for key, value in sorted(SALE_ACCESS_PLANS.items(), key=lambda item: int(item[1]["sort_order"]))
    ]

    return {
        "user": user,
        "price_per_star": price_per_star,
        "current_plan": {"key": user.get("sale_access_plan_key"), **current_plan} if current_plan else None,
        "sale_access_active": bool(user.get("sale_access_active")),
        "has_pending_request": bool(pending_request_row),
        "pending_request": row_to_sale_access_request(pending_request_row) if pending_request_row else None,
        "recent_requests": get_recent_sale_access_requests(conn, telegram_user_id),
        "plans": plans,
    }



def get_existing_referral_transaction(conn: sqlite3.Connection, order_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM referral_transactions
        WHERE order_id = ? OR source_order_id = ?
        ORDER BY chain_depth ASC
        LIMIT 1
        """,
        (order_id, order_id),
    ).fetchone()
    return row_to_referral_transaction(row) if row else None


def get_existing_referral_transactions(conn: sqlite3.Connection, order_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT * FROM referral_transactions
        WHERE source_order_id = ? OR order_id = ? OR order_id LIKE ?
        ORDER BY chain_depth ASC
        """,
        (order_id, order_id, f"{order_id}:L%"),
    ).fetchall()
    return [row_to_referral_transaction(row) for row in rows]


def process_referral_reward(conn: sqlite3.Connection, order: dict) -> dict | None:
    existing_transactions = get_existing_referral_transactions(conn, order["id"])
    if existing_transactions:
        return {
            "awarded": False,
            "already_exists": True,
            "transaction": existing_transactions[0],
            "transactions": existing_transactions,
            "total_reward": normalize_amount(sum(float(item["reward_amount"]) for item in existing_transactions)),
        }

    buyer = ensure_user_record(
        conn,
        telegram_user_id=int(order["telegram_user_id"]),
        telegram_username=str(order.get("telegram_username") or ""),
        telegram_full_name=str(order.get("telegram_full_name") or ""),
    )

    inviter_id = buyer.get("invited_by_user_id")
    if not inviter_id:
        return None

    amount = normalize_amount(order.get("amount_rub") or order.get("amount") or 0)
    stars = int(order.get("stars_amount") or order.get("stars_count") or order.get("stars") or 0)
    if amount <= 0:
        return None

    profit = calculate_order_profit(conn, order)
    base_profit = profit["referral_base_profit"]
    if base_profit <= 0:
        return None

    transactions: list[dict] = []
    referrers: list[dict] = []
    current_referral_user = buyer
    current_referrer_id = int(inviter_id)

    for depth in range(1, MAX_REFERRAL_CHAIN_DEPTH + 1):
        referrer_row = get_user_row(conn, current_referrer_id)
        if referrer_row is None:
            break
        referrer = row_to_user(referrer_row)

        before_direct_level = get_level_by_turnover(referrer["ref_total_turnover"], referrer["custom_ref_percent"])
        reward_level = before_direct_level if depth == 1 else get_network_level_by_turnover(referrer["ref_total_turnover"])
        percent_used = float(reward_level["percent"])
        reward_amount = normalize_amount(base_profit * percent_used / 100)
        new_available = normalize_amount(referrer["ref_balance_available"] + reward_amount)
        new_turnover = normalize_amount(referrer["ref_total_turnover"] + amount)
        after_direct_level = get_level_by_turnover(new_turnover, referrer["custom_ref_percent"])

        conn.execute(
            """
            UPDATE users
            SET ref_balance_available = ?,
                ref_total_turnover = ?,
                ref_level = ?,
                ref_percent = ?,
                updated_at = ?
            WHERE telegram_user_id = ?
            """,
            (
                new_available,
                new_turnover,
                after_direct_level["level"],
                after_direct_level["percent"],
                now_iso(),
                referrer["telegram_user_id"],
            ),
        )

        transaction = {
            "id": str(uuid.uuid4()),
            "order_id": f"{order['id']}:L{depth}",
            "source_order_id": order["id"],
            "chain_depth": depth,
            "reward_kind": "direct" if depth == 1 else "network",
            "referrer_user_id": referrer["telegram_user_id"],
            "referral_user_id": current_referral_user["telegram_user_id"],
            "order_amount": amount,
            "stars_amount": stars,
            "stars_cost_rub": profit["stars_cost_rub"],
            "base_profit": base_profit,
            "reward_amount": reward_amount,
            "percent_used": percent_used,
            "level_before": reward_level["level"],
            "level_after": after_direct_level["level"],
            "turnover_before": referrer["ref_total_turnover"],
            "turnover_after": new_turnover,
            "status": "available",
            "created_at": now_iso(),
        }
        conn.execute(
            """
            INSERT INTO referral_transactions (
                id, order_id, source_order_id, chain_depth, reward_kind,
                referrer_user_id, referral_user_id, order_amount, stars_amount,
                stars_cost_rub, base_profit, reward_amount, percent_used,
                level_before, level_after, turnover_before, turnover_after,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction["id"],
                transaction["order_id"],
                transaction["source_order_id"],
                transaction["chain_depth"],
                transaction["reward_kind"],
                transaction["referrer_user_id"],
                transaction["referral_user_id"],
                transaction["order_amount"],
                transaction["stars_amount"],
                transaction["stars_cost_rub"],
                transaction["base_profit"],
                transaction["reward_amount"],
                transaction["percent_used"],
                transaction["level_before"],
                transaction["level_after"],
                transaction["turnover_before"],
                transaction["turnover_after"],
                transaction["status"],
                transaction["created_at"],
            ),
        )

        updated_referrer = {
            **referrer,
            "ref_balance_available": new_available,
            "ref_total_turnover": new_turnover,
            "ref_level": after_direct_level["level"],
            "ref_percent": after_direct_level["percent"],
        }
        transactions.append(transaction)
        referrers.append(updated_referrer)

        next_referrer_id = referrer.get("invited_by_user_id")
        if not next_referrer_id:
            break
        current_referral_user = updated_referrer
        current_referrer_id = int(next_referrer_id)

    if not transactions:
        return None

    return {
        "awarded": True,
        "transaction": transactions[0] if transactions else None,
        "transactions": transactions,
        "referrer": referrers[0] if referrers else None,
        "referrers": referrers,
        "buyer": buyer,
        "profit": profit,
        "total_reward": normalize_amount(sum(float(item["reward_amount"]) for item in transactions)),
        "final_net_profit": normalize_amount(base_profit - sum(float(item["reward_amount"]) for item in transactions)),
        "level_before": None,
        "level_after": None,
        "level_up": False,
    }



def create_withdrawal_request(telegram_user_id: int, requisites: str, amount: float | None = None) -> dict:
    requisites = requisites.strip()
    if len(requisites) < 5:
        raise HTTPException(status_code=400, detail="Requisites are too short")

    conn = get_db()
    try:
        user_row = get_user_row(conn, telegram_user_id)
        if user_row is None:
            raise HTTPException(status_code=404, detail="User not found")

        user = row_to_user(user_row)
        if user.get("is_blacklisted"):
            raise HTTPException(status_code=403, detail=f"User is blacklisted: {user.get('blacklist_reason') or 'blocked'}")
        existing_pending = conn.execute(
            "SELECT * FROM withdrawal_requests WHERE telegram_user_id = ? AND status = ? LIMIT 1",
            (telegram_user_id, WithdrawalStatus.pending.value),
        ).fetchone()
        if existing_pending:
            raise HTTPException(status_code=400, detail="Pending withdrawal already exists")

        available = float(user["ref_balance_available"])
        withdraw_amount = normalize_amount(amount if amount is not None else available)

        if withdraw_amount < REFERRAL_MIN_WITHDRAW:
            raise HTTPException(status_code=400, detail=f"Minimum withdrawal is {REFERRAL_MIN_WITHDRAW}")
        if withdraw_amount > available:
            raise HTTPException(status_code=400, detail="Insufficient available balance")

        withdraw_id = str(uuid.uuid4())
        now = now_iso()
        new_available = normalize_amount(available - withdraw_amount)
        new_frozen = normalize_amount(float(user["ref_balance_frozen"]) + withdraw_amount)

        conn.execute(
            """
            INSERT INTO withdrawal_requests (
                id, telegram_user_id, amount, requisites, status,
                created_at, updated_at, processed_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                withdraw_id,
                telegram_user_id,
                withdraw_amount,
                requisites,
                WithdrawalStatus.pending.value,
                now,
                now,
                None,
            ),
        )
        conn.execute(
            """
            UPDATE users
            SET ref_balance_available = ?, ref_balance_frozen = ?, updated_at = ?
            WHERE telegram_user_id = ?
            """,
            (new_available, new_frozen, now, telegram_user_id),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM withdrawal_requests WHERE id = ?", (withdraw_id,)).fetchone()
        return {
            "withdrawal": row_to_withdrawal(row),
            "user": {
                **user,
                "ref_balance_available": new_available,
                "ref_balance_frozen": new_frozen,
            },
        }
    finally:
        conn.close()



def update_withdrawal_status(withdraw_id: str, new_status: WithdrawalStatus, processed_by: int | None = None) -> dict:
    conn = get_db()
    try:
        withdrawal_row = conn.execute("SELECT * FROM withdrawal_requests WHERE id = ?", (withdraw_id,)).fetchone()
        if withdrawal_row is None:
            raise HTTPException(status_code=404, detail="Withdrawal request not found")

        withdrawal = row_to_withdrawal(withdrawal_row)
        if withdrawal["status"] != WithdrawalStatus.pending.value:
            return {
                "withdrawal": withdrawal,
                "user": row_to_user(get_user_row(conn, withdrawal["telegram_user_id"])),
                "already_processed": True,
            }

        user_row = get_user_row(conn, int(withdrawal["telegram_user_id"]))
        if user_row is None:
            raise HTTPException(status_code=404, detail="User not found")
        user = row_to_user(user_row)

        frozen = float(user["ref_balance_frozen"])
        available = float(user["ref_balance_available"])
        total_paid = float(user["ref_total_paid"])
        amount = float(withdrawal["amount"])

        if new_status == WithdrawalStatus.paid:
            frozen = normalize_amount(max(0, frozen - amount))
            total_paid = normalize_amount(total_paid + amount)
        elif new_status == WithdrawalStatus.rejected:
            frozen = normalize_amount(max(0, frozen - amount))
            available = normalize_amount(available + amount)

        now = now_iso()
        conn.execute(
            "UPDATE withdrawal_requests SET status = ?, updated_at = ?, processed_by = ? WHERE id = ?",
            (new_status.value, now, processed_by, withdraw_id),
        )
        conn.execute(
            """
            UPDATE users
            SET ref_balance_available = ?, ref_balance_frozen = ?, ref_total_paid = ?, updated_at = ?
            WHERE telegram_user_id = ?
            """,
            (available, frozen, total_paid, now, withdrawal["telegram_user_id"]),
        )
        conn.commit()

        updated_withdrawal_row = conn.execute("SELECT * FROM withdrawal_requests WHERE id = ?", (withdraw_id,)).fetchone()
        updated_user_row = get_user_row(conn, int(withdrawal["telegram_user_id"]))
        return {
            "withdrawal": row_to_withdrawal(updated_withdrawal_row),
            "user": row_to_user(updated_user_row),
            "already_processed": False,
        }
    finally:
        conn.close()



def get_sale_access_request_or_404(request_id: str) -> dict:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM sale_access_requests WHERE id = ?", (request_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Sale access request not found")
    return row_to_sale_access_request(row)



def create_sale_access_request(
    telegram_user_id: int,
    telegram_username: str,
    telegram_full_name: str,
    plan_key: str,
) -> dict:
    plan = get_sale_plan(plan_key)

    conn = get_db()
    try:
        user = ensure_user_record(
            conn,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            telegram_full_name=telegram_full_name,
        )
        if user.get("is_blacklisted"):
            raise HTTPException(status_code=403, detail=f"User is blacklisted: {user.get('blacklist_reason') or 'blocked'}")

        existing_pending = conn.execute(
            "SELECT * FROM sale_access_requests WHERE telegram_user_id = ? AND status = ? LIMIT 1",
            (telegram_user_id, SaleAccessRequestStatus.pending.value),
        ).fetchone()
        if existing_pending:
            raise HTTPException(status_code=400, detail="У вас уже есть заявка на продажу звёзд в обработке")

        request_id = str(uuid.uuid4())
        now = now_iso()
        payment_comment = generate_payment_comment()
        price_per_star = get_sale_price_per_star(conn)
        conn.execute(
            """
            INSERT INTO sale_access_requests (
                id, telegram_user_id, telegram_username, telegram_full_name,
                plan_key, plan_title, days, amount, price_per_star,
                status, payment_comment, payable_amount, payment_match_mode,
                paid_operation_id, paid_operation_time, processing_notified_at,
                customer_notified_at, created_at, updated_at, processed_by,
                activated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                telegram_user_id,
                telegram_username or "",
                telegram_full_name or "",
                plan_key,
                str(plan["title"]),
                int(plan["days"]),
                normalize_amount(plan["price"]),
                normalize_amount(price_per_star),
                SaleAccessRequestStatus.pending.value,
                payment_comment,
                normalize_amount(plan["price"]),
                PAYMENT_MATCH_MODE,
                None,
                None,
                None,
                None,
                now,
                now,
                None,
                None,
                None,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sale_access_requests WHERE id = ?", (request_id,)).fetchone()
        return {
            "request": row_to_sale_access_request(row),
            "user": user,
        }
    finally:
        conn.close()



def update_sale_access_request_status(
    request_id: str,
    new_status: SaleAccessRequestStatus,
    processed_by: int | None = None,
) -> dict:
    conn = get_db()
    try:
        request_row = conn.execute("SELECT * FROM sale_access_requests WHERE id = ?", (request_id,)).fetchone()
        if request_row is None:
            raise HTTPException(status_code=404, detail="Sale access request not found")

        request = row_to_sale_access_request(request_row)
        user_row = get_user_row(conn, int(request["telegram_user_id"]))
        if user_row is None:
            raise HTTPException(status_code=404, detail="User not found")
        user = row_to_user(user_row)

        if request["status"] != SaleAccessRequestStatus.pending.value:
            return {
                "request": request,
                "user": user,
                "already_processed": True,
            }

        now_dt = datetime.now(UTC)
        now = now_iso()
        activated_at = None
        expires_at = None

        if new_status == SaleAccessRequestStatus.active:
            current_until = parse_iso_datetime(user.get("sale_access_until"))
            start_dt = current_until if current_until and current_until > now_dt else now_dt
            expires_dt = start_dt + timedelta(days=int(request["days"]))
            activated_at = now_dt.isoformat()
            expires_at = expires_dt.isoformat()

            conn.execute(
                """
                UPDATE users
                SET sale_access_until = ?, sale_access_plan_key = ?, sale_price_per_star = ?, updated_at = ?
                WHERE telegram_user_id = ?
                """,
                (
                    expires_at,
                    request["plan_key"],
                    normalize_amount(request.get("price_per_star", SALE_PRICE_PER_STAR)),
                    now,
                    request["telegram_user_id"],
                ),
            )

        conn.execute(
            """
            UPDATE sale_access_requests
            SET status = ?, updated_at = ?, processed_by = ?, activated_at = ?, expires_at = ?
            WHERE id = ?
            """,
            (new_status.value, now, processed_by, activated_at, expires_at, request_id),
        )
        conn.commit()

        updated_request_row = conn.execute("SELECT * FROM sale_access_requests WHERE id = ?", (request_id,)).fetchone()
        updated_user_row = get_user_row(conn, int(request["telegram_user_id"]))
        return {
            "request": row_to_sale_access_request(updated_request_row),
            "user": row_to_user(updated_user_row),
            "already_processed": False,
        }
    finally:
        conn.close()



def create_balance_topup_request(
    telegram_user_id: int,
    telegram_username: str,
    telegram_full_name: str,
    amount: float,
) -> dict:
    amount = normalize_amount(amount)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    conn = get_db()
    try:
        user = ensure_user_record(
            conn,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            telegram_full_name=telegram_full_name,
        )
        if user.get("is_blacklisted"):
            raise HTTPException(status_code=403, detail=f"User is blacklisted: {user.get('blacklist_reason') or 'blocked'}")

        existing_pending = conn.execute(
            "SELECT * FROM balance_topup_requests WHERE telegram_user_id = ? AND status = ? LIMIT 1",
            (telegram_user_id, BalanceTopupStatus.pending.value),
        ).fetchone()
        if existing_pending:
            raise HTTPException(status_code=400, detail="Pending balance top-up request already exists")

        topup_id = str(uuid.uuid4())
        now = now_iso()
        payment_comment = generate_payment_comment()
        conn.execute(
            """
            INSERT INTO balance_topup_requests (
                id, telegram_user_id, telegram_username, telegram_full_name,
                amount, status, payment_comment, payable_amount, payment_match_mode,
                paid_operation_id, paid_operation_time, processing_notified_at,
                customer_notified_at, created_at, updated_at, processed_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topup_id,
                telegram_user_id,
                telegram_username or "",
                telegram_full_name or "",
                amount,
                BalanceTopupStatus.pending.value,
                payment_comment,
                amount,
                PAYMENT_MATCH_MODE,
                None,
                None,
                None,
                None,
                now,
                now,
                None,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM balance_topup_requests WHERE id = ?", (topup_id,)).fetchone()
        return {
            "topup": row_to_topup(row),
            "user": user,
        }
    finally:
        conn.close()



def update_balance_topup_status(topup_id: str, new_status: BalanceTopupStatus, processed_by: int | None = None) -> dict:
    conn = get_db()
    try:
        topup_row = conn.execute("SELECT * FROM balance_topup_requests WHERE id = ?", (topup_id,)).fetchone()
        if topup_row is None:
            raise HTTPException(status_code=404, detail="Top-up request not found")

        topup = row_to_topup(topup_row)
        if topup["status"] != BalanceTopupStatus.pending.value:
            return {
                "topup": topup,
                "user": row_to_user(get_user_row(conn, topup["telegram_user_id"])),
                "already_processed": True,
            }

        user_row = get_user_row(conn, int(topup["telegram_user_id"]))
        if user_row is None:
            raise HTTPException(status_code=404, detail="User not found")
        user = row_to_user(user_row)

        new_balance = float(user["balance"])
        new_total_deposited = float(user["total_deposited"])

        if new_status == BalanceTopupStatus.approved:
            new_balance = normalize_amount(new_balance + float(topup["amount"]))
            new_total_deposited = normalize_amount(new_total_deposited + float(topup["amount"]))
            create_balance_transaction(
                conn,
                telegram_user_id=int(topup["telegram_user_id"]),
                tx_type="deposit",
                amount=float(topup["amount"]),
                status="completed",
                note="Пополнение баланса",
                related_topup_id=topup_id,
            )

        now = now_iso()
        conn.execute(
            "UPDATE balance_topup_requests SET status = ?, updated_at = ?, processed_by = ? WHERE id = ?",
            (new_status.value, now, processed_by, topup_id),
        )
        conn.execute(
            "UPDATE users SET balance = ?, total_deposited = ?, updated_at = ? WHERE telegram_user_id = ?",
            (new_balance, new_total_deposited, now, topup["telegram_user_id"]),
        )
        conn.commit()

        updated_topup_row = conn.execute("SELECT * FROM balance_topup_requests WHERE id = ?", (topup_id,)).fetchone()
        updated_user_row = get_user_row(conn, int(topup["telegram_user_id"]))
        return {
            "topup": row_to_topup(updated_topup_row),
            "user": row_to_user(updated_user_row),
            "already_processed": False,
        }
    finally:
        conn.close()



def create_balance_order_payload(payload: BalanceOrderCreateRequest) -> dict:
    order_id = str(uuid.uuid4())
    amount_rub = normalize_amount(payload.amount_rub)
    stars_amount = int(payload.stars_amount)
    return {
        "id": order_id,
        "order_id": order_id,
        "telegram_user_id": payload.telegram_user_id,
        "telegram_username": payload.telegram_username,
        "telegram_full_name": payload.telegram_full_name,
        "recipient_username": payload.recipient_username,
        "stars_amount": stars_amount,
        "stars_count": stars_amount,
        "stars": stars_amount,
        "amount_rub": amount_rub,
        "amount": amount_rub,
        "status": OrderStatus.draft.value,
        "payment_source": "balance",
        "balance_charge_amount": amount_rub,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "fragment_result": None,
        "fragment_error": None,
    }



def process_balance_order(payload: BalanceOrderCreateRequest) -> dict:
    recipient_username = str(payload.recipient_username or "").strip()
    if not recipient_username:
        raise HTTPException(status_code=400, detail="recipient_username is empty")
    if int(payload.stars_amount) < 50:
        raise HTTPException(status_code=400, detail="stars_amount must be >= 50")

    order = create_balance_order_payload(payload)
    purchase_tx_id = str(uuid.uuid4())

    conn = get_db()
    try:
        user = ensure_user_record(
            conn,
            telegram_user_id=payload.telegram_user_id,
            telegram_username=payload.telegram_username,
            telegram_full_name=payload.telegram_full_name,
        )
        if user.get("is_blacklisted"):
            raise HTTPException(status_code=403, detail=f"User is blacklisted: {user.get('blacklist_reason') or 'blocked'}")
        current_amount = calculate_order_amount_rub(conn, int(order["stars_amount"]))
        order["amount_rub"] = current_amount
        order["amount"] = current_amount
        order["balance_charge_amount"] = current_amount
        if float(user["balance"]) < float(order["amount_rub"]):
            raise HTTPException(status_code=400, detail="Insufficient balance")

        new_balance = normalize_amount(float(user["balance"]) - float(order["amount_rub"]))
        conn.execute(
            "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_user_id = ?",
            (new_balance, now_iso(), payload.telegram_user_id),
        )
        conn.execute(
            """
            INSERT INTO orders (
                id, order_id, telegram_user_id, telegram_username, telegram_full_name,
                recipient_username, stars_amount, stars_count, stars, amount_rub,
                amount, status, payment_source, balance_charge_amount,
                created_at, updated_at, fragment_result, fragment_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order["id"],
                order["order_id"],
                order["telegram_user_id"],
                order["telegram_username"],
                order["telegram_full_name"],
                order["recipient_username"],
                order["stars_amount"],
                order["stars_count"],
                order["stars"],
                order["amount_rub"],
                order["amount"],
                order["status"],
                order["payment_source"],
                order["balance_charge_amount"],
                order["created_at"],
                order["updated_at"],
                None,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO balance_transactions (
                id, telegram_user_id, type, amount, status, note,
                related_order_id, related_topup_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                purchase_tx_id,
                payload.telegram_user_id,
                "purchase",
                order["amount_rub"],
                "processing",
                "Покупка звёзд с баланса",
                order["id"],
                None,
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        fragment_result = auto_buy_stars(recipient_username, int(payload.stars_amount))
    except Exception as exc:
        conn = get_db()
        try:
            user_row = get_user_row(conn, payload.telegram_user_id)
            current_user = row_to_user(user_row)
            refunded_balance = normalize_amount(float(current_user["balance"]) + float(order["amount_rub"]))
            conn.execute(
                "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_user_id = ?",
                (refunded_balance, now_iso(), payload.telegram_user_id),
            )
            conn.execute(
                "UPDATE orders SET fragment_error = ?, status = ?, updated_at = ? WHERE id = ?",
                (str(exc), OrderStatus.failed.value, now_iso(), order["id"]),
            )
            update_balance_transaction_status(conn, purchase_tx_id, "failed")
            create_balance_transaction(
                conn,
                telegram_user_id=payload.telegram_user_id,
                tx_type="refund",
                amount=float(order["amount_rub"]),
                status="completed",
                note="Возврат за неуспешную покупку с баланса",
                related_order_id=order["id"],
            )
            conn.commit()
        finally:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Balance purchase failed and funds were refunded: {exc}") from exc

    conn = get_db()
    try:
        conn.execute(
            "UPDATE orders SET fragment_result = ?, fragment_error = ?, status = ?, updated_at = ? WHERE id = ?",
            (json.dumps(fragment_result, ensure_ascii=False), None, OrderStatus.paid.value, now_iso(), order["id"]),
        )
        update_balance_transaction_status(conn, purchase_tx_id, "completed")
        user_row = get_user_row(conn, payload.telegram_user_id)
        current_user = row_to_user(user_row)
        conn.execute(
            "UPDATE users SET total_spent = ?, updated_at = ? WHERE telegram_user_id = ?",
            (normalize_amount(float(current_user["total_spent"]) + float(order["amount_rub"])), now_iso(), payload.telegram_user_id),
        )
        order_row = conn.execute("SELECT * FROM orders WHERE id = ?", (order["id"],)).fetchone()
        updated_order = row_to_order(order_row)
        try:
            referral_result = process_referral_reward(conn, updated_order)
        except Exception as referral_exc:
            logger.exception("Referral reward failed for balance order %s", order["id"])
            referral_result = {"awarded": False, "error": str(referral_exc)}
        conn.commit()
        final_user = row_to_user(get_user_row(conn, payload.telegram_user_id))
        return {
            "order": updated_order,
            "user": final_user,
            "fragment_result": fragment_result,
            "referral_result": referral_result,
        }
    finally:
        conn.close()


async def send_telegram_message(token: str, chat_id: int | str, text: str) -> None:
    if not token or not chat_id:
        return
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload) as response:
            if response.status >= 400:
                body = await response.text()
                raise RuntimeError(f"Telegram sendMessage failed: {response.status} {body}")


async def notify_customer_processing(telegram_user_id: int) -> None:
    await send_telegram_message(BOT_TOKEN, telegram_user_id, "🚀 <b>Приступаю к выполнению заказа</b>")


async def notify_customer_order_success(order: dict) -> None:
    text = (
        "✅ <b>Звёзды успешно отправлены</b>\n\n"
        f"🆔 <b>ID заказа:</b> <code>{order.get('id', '—')}</code>\n"
        f"🎁 <b>Получатель:</b> <code>{order.get('recipient_username', '—')}</code>\n"
        f"⭐ <b>Количество:</b> <code>{order.get('stars_amount') or order.get('stars') or '—'}</code>\n\n"
        f"Если понадобится помощь, напишите в поддержку: <code>{SUPPORT_USERNAME}</code>"
    )
    await send_telegram_message(BOT_TOKEN, int(order["telegram_user_id"]), text)


async def notify_customer_topup_success(topup: dict, user: dict) -> None:
    text = (
        "✅ <b>Баланс успешно пополнен</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{topup.get('id', '—')}</code>\n"
        f"💸 <b>Сумма:</b> <code>{normalize_amount(topup.get('amount')):.2f}</code> RUB\n"
        f"💳 <b>Текущий баланс:</b> <code>{normalize_amount(user.get('balance')):.2f}</code> RUB\n\n"
        f"Если понадобится помощь, напишите в поддержку: <code>{SUPPORT_USERNAME}</code>"
    )
    await send_telegram_message(BOT_TOKEN, int(topup["telegram_user_id"]), text)


async def notify_customer_sale_access_success(request: dict, user: dict) -> None:
    text = (
        "✅ <b>Доступ к продаже звёзд активирован</b>\n\n"
        f"🆔 <b>ID заявки:</b> <code>{request.get('id', '—')}</code>\n"
        f"📦 <b>Тариф:</b> <code>{request.get('plan_title', '—')}</code>\n"
        f"⏰ <b>Доступ до:</b> <code>{(request.get('expires_at') or user.get('sale_access_until') or '—')}</code>\n\n"
        f"Если понадобится помощь, напишите в поддержку: <code>{SUPPORT_USERNAME}</code>"
    )
    await send_telegram_message(BOT_TOKEN, int(request["telegram_user_id"]), text)


def mention_user(user: dict | None) -> str:
    if not user:
        return "—"
    username = str(user.get("telegram_username") or "").strip()
    if username:
        return f"@{username}"
    return f"ID {user.get('telegram_user_id', '—')}"


async def notify_referral_reward_messages(referral_result: dict | None, order: dict) -> None:
    if not isinstance(referral_result, dict) or not referral_result.get("awarded"):
        return

    transactions = referral_result.get("transactions") or []
    referrers = referral_result.get("referrers") or []
    buyer = referral_result.get("buyer") or {}
    stars = order.get("stars_amount") or order.get("stars_count") or order.get("stars") or 0
    amount = order.get("amount_rub") or order.get("amount") or 0

    for index, tx in enumerate(transactions):
        referrer = referrers[index] if index < len(referrers) else {}
        referrer_id = referrer.get("telegram_user_id") or tx.get("referrer_user_id")
        if referrer_id:
            client_text = (
                "🎉 <b>Реферальное начисление!</b>\n\n"
                f"💸 <b>Начислено:</b> <code>{normalize_amount(tx.get('reward_amount', 0)):.2f}</code> ₽\n"
                f"👤 <b>Покупатель:</b> {mention_user(buyer)}\n"
                f"📦 <b>Куплено:</b> <code>{stars}</code>⭐️\n"
                f"💰 <b>Сумма покупки:</b> <code>{normalize_amount(amount):.2f}</code> ₽\n"
                f"🔋 <b>Ваш уровень:</b> <code>{referrer.get('ref_level', tx.get('level_after', '—'))}</code>"
            )
            try:
                await send_telegram_message(BOT_TOKEN, int(referrer_id), client_text)
            except Exception:
                logger.exception("Failed to notify referrer %s", referrer_id)

        admin_text = (
            "🎉<b>Новая покупка(реферальная)</b>\n\n"
            f"😘<b>Партнер:</b> {mention_user(referrer)}\n"
            f"💰<b>Покупатель:</b> {mention_user(buyer)}\n"
            f"🔋 <b>Уровень партнера:</b> <code>{referrer.get('ref_level', tx.get('level_after', '—'))}</code>"
        )
        try:
            await send_telegram_message(ADMIN_BOT_TOKEN, ADMIN_CHAT_ID, admin_text)
        except Exception:
            logger.exception("Failed to notify admin about referral purchase")


async def notify_admin_payment_error(kind: str, item_id: str, error: Exception | str) -> None:
    text = (
        "⚠️ <b>Ошибка автоматической обработки оплаты</b>\n\n"
        f"📌 <b>Тип:</b> <code>{kind}</code>\n"
        f"🆔 <b>ID:</b> <code>{item_id}</code>\n"
        f"❌ <b>Ошибка:</b> <code>{str(error)}</code>"
    )
    try:
        await send_telegram_message(ADMIN_BOT_TOKEN, ADMIN_CHAT_ID, text)
    except Exception:
        logger.exception("Failed to notify admin about payment error")


def update_payment_audit(
    table_name: str,
    item_id: str,
    *,
    paid_operation_id: str | None = None,
    paid_operation_time: str | None = None,
    processing_notified_at: str | None = None,
    customer_notified_at: str | None = None,
) -> None:
    fields: dict[str, object] = {}
    if paid_operation_id is not None:
        fields["paid_operation_id"] = paid_operation_id
    if paid_operation_time is not None:
        fields["paid_operation_time"] = paid_operation_time
    if processing_notified_at is not None:
        fields["processing_notified_at"] = processing_notified_at
    if customer_notified_at is not None:
        fields["customer_notified_at"] = customer_notified_at
    if not fields:
        return

    set_clause = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    values.append(item_id)
    conn = get_db()
    try:
        conn.execute(f"UPDATE {table_name} SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def operation_already_used(operation_id: str) -> bool:
    conn = get_db()
    try:
        for table_name in ("orders", "balance_topup_requests", "sale_access_requests"):
            row = conn.execute(
                f"SELECT id FROM {table_name} WHERE paid_operation_id = ? LIMIT 1",
                (operation_id,),
            ).fetchone()
            if row:
                return True
        return False
    finally:
        conn.close()


def get_pending_payments() -> list[dict]:
    conn = get_db()
    try:
        result: list[dict] = []
        order_rows = conn.execute(
            """
            SELECT * FROM orders
            WHERE status = ? AND payment_source = 'card'
              AND payment_comment IS NOT NULL
              AND paid_operation_id IS NULL
            ORDER BY created_at ASC
            """,
            (OrderStatus.payment_pending.value,),
        ).fetchall()
        for row in order_rows:
            item = row_to_order(row)
            result.append({
                "kind": "order",
                "table": "orders",
                "id": item["id"],
                "amount": item["amount_rub"],
                **item,
            })

        topup_rows = conn.execute(
            """
            SELECT * FROM balance_topup_requests
            WHERE status = ? AND payment_comment IS NOT NULL
              AND paid_operation_id IS NULL
            ORDER BY created_at ASC
            """,
            (BalanceTopupStatus.pending.value,),
        ).fetchall()
        for row in topup_rows:
            item = row_to_topup(row)
            result.append({
                "kind": "topup",
                "table": "balance_topup_requests",
                "id": item["id"],
                "amount": item["amount"],
                **item,
            })

        sale_rows = conn.execute(
            """
            SELECT * FROM sale_access_requests
            WHERE status = ? AND payment_comment IS NOT NULL
              AND paid_operation_id IS NULL
            ORDER BY created_at ASC
            """,
            (SaleAccessRequestStatus.pending.value,),
        ).fetchall()
        for row in sale_rows:
            item = row_to_sale_access_request(row)
            result.append({
                "kind": "sale_access",
                "table": "sale_access_requests",
                "id": item["id"],
                "amount": item["amount"],
                **item,
            })
        return result
    finally:
        conn.close()


def load_ozon_cookies() -> dict[str, str]:
    if not OZON_COOKIES_JSON:
        raise RuntimeError("OZON_COOKIES_JSON is empty")
    cookies = json.loads(OZON_COOKIES_JSON)
    if not isinstance(cookies, dict):
        raise RuntimeError("OZON_COOKIES_JSON must be a JSON object")
    return {str(key): str(value) for key, value in cookies.items()}


def extract_ozon_operations(data: dict) -> tuple[list[dict], str | None]:
    known_paths: list[tuple[str, list[str]]] = [
        ("data.me.client.groupOperationsV2.items", ["data", "me", "client", "groupOperationsV2", "items"]),
        ("data.groupOperationsV2.items", ["data", "groupOperationsV2", "items"]),
        ("groupOperationsV2.items", ["groupOperationsV2", "items"]),
        ("items", ["items"]),
    ]
    for path_name, path in known_paths:
        current: object = data
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        if isinstance(current, list):
            return [item for item in current if isinstance(item, dict)], path_name

    # Fallback for Ozon response shape changes: find the first list that looks like operations.
    stack: list[tuple[str, object]] = [("$", data)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, list):
            dict_items = [item for item in value if isinstance(item, dict)]
            if dict_items and any(
                ("accountAmount" in item or "operationId" in item or "purpose" in item or "time" in item)
                for item in dict_items[:5]
            ):
                return dict_items, path
            continue
        if isinstance(value, dict):
            for key, child in value.items():
                stack.append((f"{path}.{key}", child))
    return [], None


def summarize_ozon_response(data: object, text: str) -> dict[str, object]:
    summary: dict[str, object] = {
        "text_length": len(text),
        "json_type": type(data).__name__,
    }
    if isinstance(data, dict):
        keys = [str(key) for key in data.keys()]
        summary["top_level_keys"] = keys[:12]
        for key in ("error", "message", "code", "status", "type"):
            value = data.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                summary[key] = value
        errors = data.get("errors")
        if isinstance(errors, list):
            summary["errors_count"] = len(errors)
            if errors and isinstance(errors[0], dict):
                summary["first_error"] = {
                    str(key): value
                    for key, value in errors[0].items()
                    if isinstance(value, (str, int, float, bool)) or value is None
                }
    elif isinstance(data, list):
        summary["items_count"] = len(data)
    summary["preview"] = text[:300]
    return summary


def maybe_enter_ozon_pin(driver) -> bool:
    if not OZON_PIN:
        return False

    try:
        from selenium.webdriver.common.by import By
    except ImportError:
        return False

    deadline = time.time() + max(1, OZON_PIN_WAIT_SECONDS)
    selectors = (
        '[data-testid="pincode-input"] input',
        '[data-testid="pincode-input"]',
        '[data-testid="code-wrapper"] input',
        'input[inputmode="numeric"]',
        'input[type="password"]',
    )

    while time.time() < deadline:
        try:
            state = driver.execute_script(
                """
                const text = document.body ? document.body.innerText : '';
                return {
                    hasPhoneForm: !!document.querySelector('[data-testid="phone-form"]'),
                    hasPin: !!document.querySelector('[data-testid="pincode-input"], [data-testid="code-wrapper"], input[inputmode="numeric"], input[type="password"]'),
                    asksPin: text.includes('Введите код-пароль') || text.includes('код-пароль')
                };
                """
            )
        except Exception:
            state = {}

        if isinstance(state, dict) and state.get("hasPhoneForm"):
            return False

        if isinstance(state, dict) and (state.get("hasPin") or state.get("asksPin")):
            for selector in selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    continue
                for element in elements:
                    try:
                        if not element.is_displayed() or not element.is_enabled():
                            continue
                        element.click()
                        try:
                            element.clear()
                        except Exception:
                            pass
                        element.send_keys(OZON_PIN)
                        time.sleep(4)
                        logger.info("Ozon PIN submitted")
                        return True
                    except Exception:
                        continue
        time.sleep(0.5)

    return False


def build_ozon_selenium_driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
    except ImportError as exc:
        raise RuntimeError("selenium is not installed in backend venv") from exc

    os.makedirs(OZON_CHROME_PROFILE_DIR, mode=0o700, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={OZON_CHROME_PROFILE_DIR}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if OZON_HEADLESS:
        options.add_argument("--headless=new")

    service = Service("/usr/bin/chromedriver") if os.path.exists("/usr/bin/chromedriver") else None
    driver = webdriver.Chrome(service=service, options=options) if service else webdriver.Chrome(options=options)
    driver.set_page_load_timeout(OZON_SELENIUM_TIMEOUT_SECONDS)
    driver.set_script_timeout(OZON_SELENIUM_TIMEOUT_SECONDS)
    return driver


def close_ozon_selenium_driver() -> None:
    global OZON_DRIVER
    if OZON_DRIVER is None:
        return
    try:
        OZON_DRIVER.quit()
    except Exception:
        logger.exception("Failed to close Ozon Selenium driver")
    finally:
        OZON_DRIVER = None


def get_ozon_selenium_driver(*, reset: bool = False):
    global OZON_DRIVER
    if reset:
        close_ozon_selenium_driver()
    if OZON_DRIVER is not None:
        try:
            _ = OZON_DRIVER.current_url
            return OZON_DRIVER
        except Exception:
            close_ozon_selenium_driver()
    OZON_DRIVER = build_ozon_selenium_driver()
    return OZON_DRIVER


def import_ozon_cookies_to_selenium_profile(cookies: dict[str, str]) -> dict[str, object]:
    driver = get_ozon_selenium_driver(reset=True)
    driver.get("https://finance.ozon.ru/")
    driver.delete_all_cookies()
    added = 0
    for name, value in cookies.items():
        if add_ozon_cookie_to_driver(driver, name, value):
            added += 1
    driver.get("https://finance.ozon.ru/lk/operations")
    maybe_enter_ozon_pin(driver)
    saved = save_ozon_cookies_from_driver(driver)
    return {
        "added": added,
        "saved": saved,
        "total": len(cookies),
        "page_url": driver.current_url,
        "page_title": driver.title,
    }


def fetch_ozon_operations_with_selenium() -> list[dict]:
    driver = get_ozon_selenium_driver()
    try:
        driver.get("https://finance.ozon.ru/lk/operations")
        payload = {
            "filter": {"categories": [], "effect": "EFFECT_UNKNOWN"},
            "cursorPagination": {"cursor": None, "perPage": OZON_OPERATIONS_PER_PAGE},
        }
        maybe_enter_ozon_pin(driver)
        result = driver.execute_async_script(
            """
            const payload = arguments[0];
            const done = arguments[arguments.length - 1];
            fetch('/api/v4/operations/list', {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify(payload)
            })
                .then(async (response) => {
                    const text = await response.text();
                    done({
                        ok: response.ok,
                        status: response.status,
                        url: response.url,
                        text
                    });
                })
                .catch((error) => done({error: String(error)}));
            """,
            payload,
        )
        if not isinstance(result, dict):
            raise RuntimeError(f"Ozon Selenium fetch returned unexpected result: {result!r}")
        if result.get("error"):
            raise RuntimeError(f"Ozon Selenium fetch failed: {result['error']}")
        text = str(result.get("text") or "")
        if not result.get("ok"):
            raise RuntimeError(f"Ozon Selenium operations failed: {result.get('status')} {text[:500]}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ozon Selenium returned non-JSON response: {text[:500]}") from exc
        operations, extract_path = extract_ozon_operations(data)
        response_summary = summarize_ozon_response(data, text)
        if extract_path is None and "NO_BANK_AUTH" in text and maybe_enter_ozon_pin(driver):
            result = driver.execute_async_script(
                """
                const payload = arguments[0];
                const done = arguments[arguments.length - 1];
                fetch('/api/v4/operations/list', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    body: JSON.stringify(payload)
                })
                    .then(async (response) => {
                        const text = await response.text();
                        done({
                            ok: response.ok,
                            status: response.status,
                            url: response.url,
                            text
                        });
                    })
                    .catch((error) => done({error: String(error)}));
                """,
                payload,
            )
            if not isinstance(result, dict):
                raise RuntimeError(f"Ozon Selenium fetch returned unexpected result: {result!r}")
            if result.get("error"):
                raise RuntimeError(f"Ozon Selenium fetch failed: {result['error']}")
            text = str(result.get("text") or "")
            if not result.get("ok"):
                raise RuntimeError(f"Ozon Selenium operations failed: {result.get('status')} {text[:500]}")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Ozon Selenium returned non-JSON response: {text[:500]}") from exc
            operations, extract_path = extract_ozon_operations(data)
            response_summary = summarize_ozon_response(data, text)
        OZON_LAST_FETCH_DEBUG.clear()
        OZON_LAST_FETCH_DEBUG.update(
            {
                "mode": "selenium",
                "status": result.get("status"),
                "url": result.get("url"),
                "page_url": driver.current_url,
                "page_title": driver.title,
                "extract_path": extract_path,
                "operations_count": len(operations),
                "response_summary": response_summary,
            }
        )
        OZON_HEALTH.update(
            {
                "last_fetch_status": result.get("status"),
                "last_fetch_text_length": len(text),
                "last_extract_path": extract_path,
                "last_response_summary": response_summary,
            }
        )
        if extract_path is None:
            raise RuntimeError(f"Ozon response does not contain operations list: {response_summary}")
        save_ozon_cookies_from_driver(driver)
        return operations
    except Exception:
        close_ozon_selenium_driver()
        raise


def refresh_ozon_cookies_with_selenium() -> dict[str, object]:
    if not OZON_USE_SELENIUM:
        raise RuntimeError("Ozon Selenium keepalive is disabled")

    try:
        operations = fetch_ozon_operations_with_selenium()
    except Exception:
        if not OZON_COOKIES_JSON:
            raise
        import_ozon_cookies_to_selenium_profile(load_ozon_cookies())
        operations = fetch_ozon_operations_with_selenium()
    cookie_count = save_ozon_cookies_from_driver(get_ozon_selenium_driver(), force=True)
    return {
        "operations_count": len(operations),
        "cookie_count": cookie_count,
    }


async def fetch_ozon_operations() -> list[dict]:
    if OZON_USE_SELENIUM:
        try:
            return await asyncio.to_thread(fetch_ozon_operations_with_selenium)
        except Exception as exc:
            if not OZON_COOKIES_JSON:
                raise
            logger.warning("Ozon Selenium fetch failed, importing saved cookies before fallback: %s", exc)
            try:
                cookies = load_ozon_cookies()
                await asyncio.to_thread(import_ozon_cookies_to_selenium_profile, cookies)
                return await asyncio.to_thread(fetch_ozon_operations_with_selenium)
            except Exception as retry_exc:
                logger.warning("Ozon Selenium retry after cookie import failed, falling back to cookies: %s", retry_exc)
                OZON_HEALTH["last_error"] = f"Selenium failed, cookie fallback used: {retry_exc}"
    return await asyncio.to_thread(fetch_ozon_operations_with_cookies)


def fetch_ozon_operations_with_cookies() -> list[dict]:
    cookies = load_ozon_cookies()
    url = "https://finance.ozon.ru/api/v4/operations/list"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://finance.ozon.ru",
        "Referer": "https://finance.ozon.ru/lk/operations",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    payload = {
        "filter": {"categories": [], "effect": "EFFECT_UNKNOWN"},
        "cursorPagination": {"cursor": None, "perPage": OZON_OPERATIONS_PER_PAGE},
    }
    response = requests.post(url, headers=headers, json=payload, cookies=cookies, timeout=30)
    text = response.text
    if response.status_code != 200:
        raise RuntimeError(f"Ozon operations failed: {response.status_code} {text[:500]}")
    data = json.loads(text)
    operations, extract_path = extract_ozon_operations(data)
    response_summary = summarize_ozon_response(data, text)
    OZON_LAST_FETCH_DEBUG.clear()
    OZON_LAST_FETCH_DEBUG.update(
        {
            "mode": "cookies",
            "status": response.status_code,
            "url": url,
            "extract_path": extract_path,
            "operations_count": len(operations),
            "response_summary": response_summary,
        }
    )
    OZON_HEALTH.update(
        {
            "last_fetch_status": response.status_code,
            "last_fetch_text_length": len(text),
            "last_extract_path": extract_path,
            "last_response_summary": response_summary,
        }
    )
    if extract_path is None:
        raise RuntimeError(f"Ozon response does not contain operations list: {response_summary}")
    return operations


async def process_matched_payment(payment: dict, operation: dict) -> None:
    operation_id = make_operation_id(operation)
    operation_time = str(operation.get("time") or "")
    now = now_iso()

    update_payment_audit(
        payment["table"],
        payment["id"],
        paid_operation_id=operation_id,
        paid_operation_time=operation_time,
        processing_notified_at=payment.get("processing_notified_at") or now,
    )

    if not payment.get("processing_notified_at"):
        try:
            await notify_customer_processing(int(payment["telegram_user_id"]))
        except Exception:
            logger.exception("Failed to send processing notification")

    try:
        if payment["kind"] == "order":
            result = await approve_order_internal(payment["id"])
            order = result.get("order") if isinstance(result, dict) else get_order_or_404(payment["id"])
            if not order.get("customer_notified_at"):
                await notify_customer_order_success(order)
                update_payment_audit("orders", payment["id"], customer_notified_at=now_iso())
            return

        if payment["kind"] == "topup":
            result = update_balance_topup_status(payment["id"], BalanceTopupStatus.approved, processed_by=None)
            topup = result.get("topup") or {}
            user = result.get("user") or {}
            if not topup.get("customer_notified_at"):
                await notify_customer_topup_success(topup, user)
                update_payment_audit("balance_topup_requests", payment["id"], customer_notified_at=now_iso())
            return

        if payment["kind"] == "sale_access":
            result = update_sale_access_request_status(payment["id"], SaleAccessRequestStatus.active, processed_by=None)
            request = result.get("request") or {}
            user = result.get("user") or {}
            if not request.get("customer_notified_at"):
                await notify_customer_sale_access_success(request, user)
                update_payment_audit("sale_access_requests", payment["id"], customer_notified_at=now_iso())
            return
    except Exception as exc:
        await notify_admin_payment_error(payment["kind"], payment["id"], exc)


async def run_ozon_payment_check_once() -> None:
    async with get_ozon_check_lock():
        operations = await fetch_ozon_operations()
        pending = get_pending_payments()
        matched_count = 0
        OZON_HEALTH.update(
            {
                "enabled": True,
                "last_success_at": now_iso(),
                "last_error": None,
                "last_operations_count": len(operations),
                "last_checked_at": now_iso(),
                "last_pending_count": len(pending),
                "last_matched_count": 0,
            }
        )

        for payment in pending:
            for operation in operations:
                operation_id = make_operation_id(operation)
                if operation_already_used(operation_id):
                    continue
                if operation_matches_payment(operation, payment):
                    await process_matched_payment(payment, operation)
                    matched_count += 1
                    break
        OZON_HEALTH["last_matched_count"] = matched_count


async def ozon_payment_watcher_loop() -> None:
    if not OZON_ENABLED:
        logger.warning("Ozon payment watcher is disabled: no Selenium profile or cookies configured")
        return
    while True:
        try:
            await run_ozon_payment_check_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            OZON_HEALTH.update(
                {
                    "enabled": True,
                    "last_error": str(exc),
                    "last_checked_at": now_iso(),
                }
            )
            logger.exception("Ozon payment watcher failed")
        await asyncio.sleep(max(5, OZON_CHECK_INTERVAL_SECONDS))


async def ozon_cookie_keepalive_loop() -> None:
    if not OZON_USE_SELENIUM:
        logger.info("Ozon cookie keepalive is disabled: OZON_USE_SELENIUM=false")
        return

    await asyncio.sleep(30)
    while True:
        try:
            async with get_ozon_check_lock():
                result = await asyncio.to_thread(refresh_ozon_cookies_with_selenium)
            OZON_HEALTH.update(
                {
                    "last_keepalive_at": now_iso(),
                    "last_keepalive_error": None,
                    "last_keepalive_operations_count": result.get("operations_count", 0),
                    "last_cookie_count": result.get("cookie_count", OZON_HEALTH.get("last_cookie_count", 0)),
                    "keepalive_interval_seconds": OZON_COOKIE_KEEPALIVE_INTERVAL_SECONDS,
                }
            )
            logger.info(
                "Ozon cookie keepalive refreshed cookies=%s operations=%s",
                result.get("cookie_count"),
                result.get("operations_count"),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            OZON_HEALTH.update(
                {
                    "last_keepalive_at": now_iso(),
                    "last_keepalive_error": str(exc),
                    "keepalive_interval_seconds": OZON_COOKIE_KEEPALIVE_INTERVAL_SECONDS,
                }
            )
            logger.exception("Ozon cookie keepalive failed")

        jitter = random.randint(0, max(0, OZON_COOKIE_KEEPALIVE_JITTER_SECONDS))
        await asyncio.sleep(max(60, OZON_COOKIE_KEEPALIVE_INTERVAL_SECONDS) + jitter)


init_db()


@app.on_event("startup")
async def startup_payment_watcher() -> None:
    global PAYMENT_WATCHER_TASK, OZON_COOKIE_KEEPALIVE_TASK
    if PAYMENT_WATCHER_TASK is None:
        PAYMENT_WATCHER_TASK = asyncio.create_task(ozon_payment_watcher_loop())
    if OZON_COOKIE_KEEPALIVE_TASK is None:
        OZON_COOKIE_KEEPALIVE_TASK = asyncio.create_task(ozon_cookie_keepalive_loop())


@app.on_event("shutdown")
async def shutdown_payment_watcher() -> None:
    global PAYMENT_WATCHER_TASK, OZON_COOKIE_KEEPALIVE_TASK
    if PAYMENT_WATCHER_TASK is not None:
        PAYMENT_WATCHER_TASK.cancel()
        try:
            await PAYMENT_WATCHER_TASK
        except asyncio.CancelledError:
            pass
        PAYMENT_WATCHER_TASK = None
    if OZON_COOKIE_KEEPALIVE_TASK is not None:
        OZON_COOKIE_KEEPALIVE_TASK.cancel()
        try:
            await OZON_COOKIE_KEEPALIVE_TASK
        except asyncio.CancelledError:
            pass
        OZON_COOKIE_KEEPALIVE_TASK = None
    await asyncio.to_thread(close_ozon_selenium_driver)


@app.get("/")
async def root():
    return {"ok": True, "service": "AraStars Backend", "time": now_iso()}


@app.get("/health")
async def health():
    return {
        "ok": True,
        "orders_count": count_orders(),
        "users_count": count_users(),
        "db_path": os.path.abspath(DB_PATH),
        "time": now_iso(),
    }


@app.get("/payments/ozon/health")
async def ozon_payment_health():
    return {"ok": True, **OZON_HEALTH}


@app.post("/payments/ozon/check-once")
async def ozon_payment_check_once(_: None = Depends(require_admin_token)):
    try:
        await run_ozon_payment_check_once()
    except Exception as exc:
        OZON_HEALTH.update(
            {
                "enabled": True,
                "last_error": str(exc),
                "last_checked_at": now_iso(),
            }
        )
        logger.exception("Manual Ozon payment check failed")
        return {"ok": False, **OZON_HEALTH}
    return {"ok": True, **OZON_HEALTH}


@app.get("/payments/ozon/debug")
async def ozon_payment_debug(_: None = Depends(require_admin_token)):
    pending = get_pending_payments()
    pending_sample = [
        {
            "kind": item.get("kind"),
            "id": item.get("id"),
            "amount": item.get("payable_amount") or item.get("amount"),
            "payment_comment": item.get("payment_comment"),
            "created_at": item.get("created_at"),
        }
        for item in pending[:10]
    ]
    return {
        "ok": True,
        "health": OZON_HEALTH,
        "last_fetch": OZON_LAST_FETCH_DEBUG,
        "pending_count": len(pending),
        "pending_sample": pending_sample,
    }


@app.post("/admin/ozon/cookies")
async def admin_update_ozon_cookies_endpoint(payload: AdminOzonCookiesUpdateRequest, _: None = Depends(require_admin_token)):
    cookies = parse_ozon_cookie_input(payload.cookies)
    cookies_json = json.dumps(cookies, ensure_ascii=False, separators=(",", ":"))
    use_selenium = bool(payload.use_selenium)
    update_env_file_values(
        {
            "OZON_COOKIES_JSON": cookies_json,
            "OZON_USE_SELENIUM": "true" if use_selenium else "false",
            "OZON_PIN": OZON_PIN or "7910",
        }
    )
    apply_ozon_runtime_config(cookies_json, use_selenium)

    import_result: dict[str, object] | None = None
    if use_selenium:
        async with get_ozon_check_lock():
            import_result = await asyncio.to_thread(import_ozon_cookies_to_selenium_profile, cookies)
            operations = await asyncio.to_thread(fetch_ozon_operations_with_selenium)
    else:
        operations = await asyncio.to_thread(fetch_ozon_operations_with_cookies)

    return {
        "ok": True,
        "mode": "selenium" if use_selenium else "cookies",
        "cookie_count": len(cookies),
        "import_result": import_result,
        "operations_count": len(operations),
        "health": OZON_HEALTH,
    }


def check_fragment_recipient_sync(username: str, quantity: int) -> dict:
    with make_session() as session:
        search_result = search_stars_recipient(session, username, quantity)

    recipient = (
        search_result.get("recipient")
        or (search_result.get("found") or {}).get("recipient")
        or (search_result.get("user") or {}).get("recipient")
    )
    return {
        "ok": True,
        "username": username,
        "quantity": quantity,
        "found": bool(recipient),
        "recipient": recipient,
        "search_result": search_result,
    }


@app.get("/fragment/check-recipient")
async def fragment_check_recipient(username: str, quantity: int = 50):
    username = normalize_recipient(username)
    if quantity < 50:
        raise HTTPException(status_code=400, detail="quantity must be >= 50")
    try:
        return await asyncio.to_thread(check_fragment_recipient_sync, username, quantity)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Fragment recipient check failed: {exc}") from exc


@app.post("/referral/users/register")
async def register_user_endpoint(payload: RegisterUserRequest):
    conn = get_db()
    try:
        existing_row = get_user_row(conn, payload.telegram_user_id)
        had_inviter_before = bool(existing_row["invited_by_user_id"]) if existing_row else False

        ensure_user_record(
            conn,
            telegram_user_id=payload.telegram_user_id,
            telegram_username=payload.telegram_username,
            telegram_full_name=payload.telegram_full_name,
        )

        inviter = None
        if payload.inviter_code:
            inviter = bind_user_to_inviter(conn, payload.telegram_user_id, payload.inviter_code)

        conn.commit()
        profile = build_referral_profile(conn, payload.telegram_user_id)
        updated_row = get_user_row(conn, payload.telegram_user_id)
        referral_bound_now = bool(
            payload.inviter_code
            and inviter
            and not had_inviter_before
            and updated_row
            and updated_row["invited_by_user_id"]
        )
        return {
            "ok": True,
            "profile": profile,
            "inviter": inviter,
            "referral_bound_now": referral_bound_now,
        }
    finally:
        conn.close()


@app.get("/referral/users/{telegram_user_id}")
async def get_referral_profile_endpoint(telegram_user_id: int):
    conn = get_db()
    try:
        profile = build_referral_profile(conn, telegram_user_id)
        return {"ok": True, "profile": profile}
    finally:
        conn.close()


@app.post("/referral/withdrawals/create")
async def create_withdrawal_endpoint(payload: WithdrawalCreateRequest):
    result = create_withdrawal_request(
        telegram_user_id=payload.telegram_user_id,
        requisites=payload.requisites,
        amount=payload.amount,
    )
    return {"ok": True, **result}


@app.post("/referral/withdrawals/{withdraw_id}/mark-paid")
async def mark_withdrawal_paid_endpoint(withdraw_id: str, payload: WithdrawalProcessRequest, _: None = Depends(require_admin_token)):
    result = update_withdrawal_status(withdraw_id, WithdrawalStatus.paid, processed_by=payload.processed_by)
    return {"ok": True, **result}


@app.post("/referral/withdrawals/{withdraw_id}/reject")
async def reject_withdrawal_endpoint(withdraw_id: str, payload: WithdrawalProcessRequest, _: None = Depends(require_admin_token)):
    result = update_withdrawal_status(withdraw_id, WithdrawalStatus.rejected, processed_by=payload.processed_by)
    return {"ok": True, **result}


@app.get("/balance/users/{telegram_user_id}")
async def get_balance_profile_endpoint(telegram_user_id: int):
    conn = get_db()
    try:
        profile = build_balance_profile(conn, telegram_user_id)
        return {"ok": True, "profile": profile}
    finally:
        conn.close()


@app.post("/balance/topups/create")
async def create_balance_topup_endpoint(payload: BalanceTopupCreateRequest):
    result = create_balance_topup_request(
        telegram_user_id=payload.telegram_user_id,
        telegram_username=payload.telegram_username,
        telegram_full_name=payload.telegram_full_name,
        amount=payload.amount,
    )
    return {"ok": True, **result}


@app.post("/balance/topups/{topup_id}/approve")
async def approve_balance_topup_endpoint(topup_id: str, payload: BalanceTopupProcessRequest, _: None = Depends(require_admin_token)):
    result = update_balance_topup_status(topup_id, BalanceTopupStatus.approved, processed_by=payload.processed_by)
    return {"ok": True, **result}


@app.post("/balance/topups/{topup_id}/reject")
async def reject_balance_topup_endpoint(topup_id: str, payload: BalanceTopupProcessRequest, _: None = Depends(require_admin_token)):
    result = update_balance_topup_status(topup_id, BalanceTopupStatus.rejected, processed_by=payload.processed_by)
    return {"ok": True, **result}


@app.get("/sale-access/users/{telegram_user_id}")
async def get_sale_access_profile_endpoint(telegram_user_id: int):
    conn = get_db()
    try:
        profile = build_sale_access_profile(conn, telegram_user_id)
        return {"ok": True, "profile": profile}
    finally:
        conn.close()


@app.get("/sale-access/requests/{request_id}")
async def get_sale_access_request_endpoint(request_id: str):
    request = get_sale_access_request_or_404(request_id)
    return {"ok": True, "request": request}


@app.post("/sale-access/requests/create")
async def create_sale_access_request_endpoint(payload: SaleAccessCreateRequest):
    result = create_sale_access_request(
        telegram_user_id=payload.telegram_user_id,
        telegram_username=payload.telegram_username,
        telegram_full_name=payload.telegram_full_name,
        plan_key=payload.plan_key,
    )
    return {"ok": True, **result}


@app.post("/sale-access/requests/{request_id}/approve")
async def approve_sale_access_request_endpoint(request_id: str, payload: SaleAccessProcessRequest, _: None = Depends(require_admin_token)):
    result = update_sale_access_request_status(request_id, SaleAccessRequestStatus.active, processed_by=payload.processed_by)
    return {"ok": True, **result}


@app.post("/sale-access/requests/{request_id}/reject")
async def reject_sale_access_request_endpoint(request_id: str, payload: SaleAccessProcessRequest, _: None = Depends(require_admin_token)):
    result = update_sale_access_request_status(request_id, SaleAccessRequestStatus.rejected, processed_by=payload.processed_by)
    return {"ok": True, **result}


@app.get("/settings/prices")
async def get_price_settings_endpoint():
    conn = get_db()
    try:
        return {"ok": True, "prices": get_price_settings_dict(conn)}
    finally:
        conn.close()


@app.get("/admin/settings")
async def admin_get_settings_endpoint(_: None = Depends(require_admin_token)):
    conn = get_db()
    try:
        return {"ok": True, "settings": get_settings_dict(conn)}
    finally:
        conn.close()


@app.post("/admin/settings")
async def admin_update_settings_endpoint(payload: AdminSettingsUpdateRequest, _: None = Depends(require_admin_token)):
    conn = get_db()
    try:
        for key, value in payload.settings.items():
            set_setting(conn, key, value)
        conn.commit()
        return {"ok": True, "settings": get_settings_dict(conn)}
    finally:
        conn.close()


@app.get("/admin/stats")
async def admin_stats_endpoint(_: None = Depends(require_admin_token)):
    now = asyncio.get_running_loop().time()
    cached_stats = ADMIN_STATS_CACHE.get("value")
    if isinstance(cached_stats, dict) and now < float(ADMIN_STATS_CACHE.get("expires_at") or 0):
        return {"ok": True, "stats": cached_stats}

    stats = await asyncio.to_thread(build_admin_stats)
    ADMIN_STATS_CACHE["value"] = stats
    ADMIN_STATS_CACHE["expires_at"] = now + ADMIN_STATS_CACHE_TTL_SECONDS
    return {"ok": True, "stats": stats}


@app.get("/admin/users/search")
async def admin_search_users_endpoint(q: str = "", limit: int = 10, _: None = Depends(require_admin_token)):
    return {"ok": True, "users": search_users(q, max(1, min(limit, 50)))}


@app.post("/admin/users/{telegram_user_id}/balance-adjust")
async def admin_balance_adjust_endpoint(telegram_user_id: int, payload: AdminBalanceAdjustRequest, _: None = Depends(require_admin_token)):
    conn = get_db()
    try:
        user_row = get_user_row(conn, telegram_user_id)
        if user_row is None:
            raise HTTPException(status_code=404, detail="User not found")
        user = row_to_user(user_row)
        amount = normalize_amount(payload.amount)
        new_balance = normalize_amount(float(user["balance"]) + amount)
        if new_balance < 0:
            raise HTTPException(status_code=400, detail="Balance cannot become negative")
        conn.execute(
            "UPDATE users SET balance = ?, updated_at = ? WHERE telegram_user_id = ?",
            (new_balance, now_iso(), telegram_user_id),
        )
        tx = create_balance_transaction(
            conn,
            telegram_user_id=telegram_user_id,
            tx_type="admin_adjustment",
            amount=abs(amount),
            status="completed",
            note=f"{payload.note} ({'+' if amount >= 0 else '-'}{abs(amount):.2f})",
        )
        conn.commit()
        updated_user = row_to_user(get_user_row(conn, telegram_user_id))
        return {"ok": True, "user": updated_user, "transaction": tx}
    finally:
        conn.close()


@app.post("/admin/users/{telegram_user_id}/blacklist")
async def admin_blacklist_endpoint(telegram_user_id: int, payload: AdminBlacklistRequest, _: None = Depends(require_admin_token)):
    conn = get_db()
    try:
        user_row = get_user_row(conn, telegram_user_id)
        if user_row is None:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute(
            """
            UPDATE users
            SET is_blacklisted = ?, blacklist_reason = ?, updated_at = ?
            WHERE telegram_user_id = ?
            """,
            (1 if payload.is_blacklisted else 0, payload.reason or "", now_iso(), telegram_user_id),
        )
        conn.commit()
        return {"ok": True, "user": row_to_user(get_user_row(conn, telegram_user_id))}
    finally:
        conn.close()


@app.get("/orders/history/{telegram_user_id}")
async def order_history_endpoint(telegram_user_id: int, limit: int = 10):
    return {"ok": True, "history": build_order_history(telegram_user_id, max(1, min(limit, 50)))}


@app.post("/orders/create")
async def create_order_endpoint(payload: OrderCreateRequest):
    order_id = str(uuid.uuid4())
    stars_amount = payload.stars_amount
    conn = get_db()
    try:
        user = ensure_user_record(
            conn,
            telegram_user_id=payload.telegram_user_id,
            telegram_username=payload.telegram_username,
            telegram_full_name=payload.telegram_full_name,
        )
        if user.get("is_blacklisted"):
            raise HTTPException(status_code=403, detail=f"User is blacklisted: {user.get('blacklist_reason') or 'blocked'}")
        amount_rub = calculate_order_amount_rub(conn, stars_amount)
    finally:
        conn.close()
    payment_comment = generate_payment_comment()

    order = {
        "id": order_id,
        "order_id": order_id,
        "telegram_user_id": payload.telegram_user_id,
        "telegram_username": payload.telegram_username,
        "telegram_full_name": payload.telegram_full_name,
        "recipient_username": payload.recipient_username,
        "stars_amount": stars_amount,
        "stars_count": payload.stars_count if payload.stars_count is not None else stars_amount,
        "stars": payload.stars if payload.stars is not None else stars_amount,
        "amount_rub": amount_rub,
        "amount": amount_rub,
        "status": OrderStatus.draft.value,
        "payment_source": "card",
        "balance_charge_amount": 0,
        "payment_comment": payment_comment,
        "payable_amount": amount_rub,
        "payment_match_mode": PAYMENT_MATCH_MODE,
        "paid_operation_id": None,
        "paid_operation_time": None,
        "processing_notified_at": None,
        "customer_notified_at": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "fragment_result": None,
        "fragment_error": None,
    }

    insert_order(order)
    return {"ok": True, "order_id": order_id, "order": order}


@app.post("/orders/create-from-balance")
async def create_order_from_balance_endpoint(payload: BalanceOrderCreateRequest):
    result = await asyncio.to_thread(process_balance_order, payload)
    try:
        await notify_referral_reward_messages(result.get("referral_result"), result.get("order") or {})
    except Exception:
        logger.exception("Failed to send referral reward notifications for balance order")
    return {"ok": True, **result, "order_id": result["order"]["id"]}


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    order = get_order_or_404(order_id)
    return {"ok": True, "order_id": order_id, "order": order}


@app.post("/orders/{order_id}/client-paid")
async def order_client_paid(order_id: str):
    order = mark_order_for_payment_review(order_id)
    return {"ok": True, "order_id": order_id, "status": order["status"], "order": order}


async def approve_order_internal(order_id: str):
    order = get_order_or_404(order_id)

    if order["status"] == OrderStatus.paid.value and order.get("fragment_result"):
        conn = get_db()
        try:
            referral_transactions = get_existing_referral_transactions(conn, order_id)
        finally:
            conn.close()
        return {
            "ok": True,
            "order_id": order_id,
            "status": order["status"],
            "order": order,
            "fragment_result": order.get("fragment_result"),
            "referral_result": {
                "awarded": False,
                "already_exists": bool(referral_transactions),
                "transaction": referral_transactions[0] if referral_transactions else None,
                "transactions": referral_transactions,
                "total_reward": normalize_amount(sum(float(item["reward_amount"]) for item in referral_transactions)),
            }
            if referral_transactions
            else None,
            "already_processed": True,
        }

    if order["status"] != OrderStatus.payment_pending.value:
        raise HTTPException(status_code=409, detail="Only payment_pending orders can be approved")

    recipient_username = str(order.get("recipient_username") or "").strip()
    stars_amount = int(order.get("stars_amount") or order.get("stars_count") or order.get("stars") or 0)

    if not recipient_username:
        raise HTTPException(status_code=400, detail="recipient_username is empty")
    if stars_amount < 50:
        raise HTTPException(status_code=400, detail="stars_amount must be >= 50")

    conn = get_db()
    try:
        fragment_result = await asyncio.to_thread(auto_buy_stars, recipient_username, stars_amount)
        conn.execute(
            "UPDATE orders SET fragment_result = ?, fragment_error = ?, status = ?, updated_at = ? WHERE id = ?",
            (json.dumps(fragment_result, ensure_ascii=False), None, OrderStatus.paid.value, now_iso(), order_id),
        )
        order_row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        updated_order = row_to_order(order_row)
        try:
            referral_result = process_referral_reward(conn, updated_order)
        except Exception as referral_exc:
            logger.exception("Referral reward failed for paid order %s", order_id)
            referral_result = {"awarded": False, "error": str(referral_exc)}
        conn.commit()
        try:
            await notify_referral_reward_messages(referral_result, updated_order)
        except Exception:
            logger.exception("Failed to send referral reward notifications for order %s", order_id)
        return {
            "ok": True,
            "order_id": order_id,
            "status": updated_order["status"],
            "order": updated_order,
            "fragment_result": fragment_result,
            "referral_result": referral_result,
        }
    except Exception as exc:
        conn.execute(
            "UPDATE orders SET fragment_error = ?, status = ?, updated_at = ? WHERE id = ?",
            (str(exc), OrderStatus.failed.value, now_iso(), order_id),
        )
        conn.commit()
        raise HTTPException(status_code=500, detail=f"Fragment auto buy failed: {exc}") from exc
    finally:
        conn.close()


@app.post("/orders/{order_id}/approve")
async def order_approve(order_id: str, _: None = Depends(require_admin_token)):
    return await approve_order_internal(order_id)


@app.post("/orders/{order_id}/reject")
async def order_reject(order_id: str, _: None = Depends(require_admin_token)):
    order = reject_order_internal(order_id)
    return {"ok": True, "order_id": order_id, "status": order["status"], "order": order}


@app.post("/orders/status")
async def order_status_update(payload: OrderStatusUpdateRequest, _: None = Depends(require_admin_token)):
    allowed = {
        "payment_pending": OrderStatus.payment_pending,
        "failed": OrderStatus.failed,
        "rejected": OrderStatus.failed,
    }
    new_status = allowed.get(payload.status)
    if not new_status:
        raise HTTPException(status_code=400, detail="Unsupported status")

    if new_status == OrderStatus.payment_pending:
        order = mark_order_for_payment_review(payload.order_id)
    elif new_status == OrderStatus.failed:
        order = reject_order_internal(payload.order_id)
    else:
        raise HTTPException(status_code=400, detail="Unsupported status transition")

    return {"ok": True, "order_id": payload.order_id, "status": order["status"], "order": order}
