"""Общие утилиты для бэкенда и ботов.

Главное здесь — экранирование пользовательского текста. Всё, что приходит от
пользователя (имя, username, реквизиты, причина блокировки), обязано проходить
через esc() перед вставкой в сообщение с parse_mode=HTML. Иначе Telegram
отвечает 400 на имени вида "<b" и уведомление админу просто не доходит.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{4,32}$")


def esc(value: Any) -> str:
    """Экранирует значение для вставки в HTML-сообщение Telegram."""
    if value is None:
        return "—"
    return html.escape(str(value), quote=False)


def money_text(value: Any) -> str:
    """Денежная сумма с двумя знаками. Никогда не бросает исключение."""
    try:
        return f"{to_decimal(value):.2f}"
    except Exception:
        return esc(value)


def to_decimal(value: Any) -> Decimal:
    """Аккуратный перевод в Decimal: деньги нельзя считать во float."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def quantize_money(value: Any) -> Decimal:
    """Округление до копейки по банковскому правилу half-up."""
    return to_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_datetime_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    return esc(raw.replace("T", " ")[:16]) + " UTC"


def parse_iso_datetime(value: Any) -> datetime | None:
    """Разбирает ISO-строку и всегда возвращает datetime с таймзоной.

    Без принудительного UTC сравнение naive и aware дат бросает TypeError.
    """
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


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def normalize_username(value: str) -> str:
    username = (value or "").strip().replace("https://t.me/", "").replace("@", "")
    username = username.split("?")[0].split("/")[0].strip()
    return f"@{username}" if username else ""


def is_valid_username(value: str) -> bool:
    return bool(USERNAME_RE.match(value or ""))


def username_text(data: dict | None) -> str:
    """Безопасное отображение пользователя для админских уведомлений."""
    if not data:
        return "—"
    username = str(data.get("telegram_username") or "").strip()
    if username:
        return f"@{esc(username)}"
    full_name = str(data.get("telegram_full_name") or "").strip()
    if full_name:
        return esc(full_name)
    return f"ID {esc(data.get('telegram_user_id', '—'))}"
