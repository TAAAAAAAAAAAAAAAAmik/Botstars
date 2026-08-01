#!/usr/bin/env python3
"""Интерактивная настройка .env.

Запуск:  python3 setup_env.py

Задаёт вопросы по одному и сам записывает ответы в .env — редактировать файл
руками не нужно. Токены бэкенда генерируются автоматически. Существующие
значения сохраняются: чтобы оставить как есть, просто нажмите Enter.

Секреты остаются только на этой машине и никуда не отправляются.
"""

from __future__ import annotations

import getpass
import os
import re
import secrets
import sys
from pathlib import Path

ENV_PATH = Path(".env")
EXAMPLE_PATH = Path(".env.example")


class Field:
    def __init__(
        self,
        key: str,
        prompt: str,
        hint: str = "",
        required: bool = False,
        secret: bool = False,
        default: str = "",
    ) -> None:
        self.key = key
        self.prompt = prompt
        self.hint = hint
        self.required = required
        self.secret = secret
        self.default = default


# Поля, которые спрашиваем. Остальные берутся из .env.example как есть.
FIELDS = [
    Field(
        "BOT_TOKEN",
        "Токен Telegram-бота",
        "@BotFather → /mybots → ваш бот → API Token",
        required=True,
        secret=True,
    ),
    Field(
        "BOT_USERNAME",
        "Username бота без @",
        "например: AraStarsS_bot",
        required=True,
    ),
    Field(
        "ADMIN_CHAT_ID",
        "Ваш Telegram ID (владелец панели)",
        "узнать: @userinfobot",
        required=True,
        default="6887373040",
    ),
    Field("SUPPORT_USERNAME", "Username поддержки", "с @", default="@AraStarsS_Manager"),
    Field("CARD_NUMBER", "Номер карты для приёма оплаты", secret=True),
    Field("CARD_HOLDER", "Владелец карты", "как в банке"),
    Field(
        "FRAGMENT_ACCOUNT_ADDRESS",
        "Адрес TON-кошелька",
        "начинается с EQ… или UQ…",
    ),
    Field(
        "FRAGMENT_WALLET_MNEMONIC",
        "Сид-фраза кошелька (24 слова через пробел)",
        "ввод скрыт; храните копию ВНЕ сервера",
        secret=True,
    ),
    Field(
        "FRAGMENT_COOKIES_JSON",
        "Cookies Fragment одной строкой JSON",
        'вида {"stel_ssid":"...","stel_token":"..."}',
        secret=True,
    ),
    Field("OZON_PIN", "PIN Ozon", "нужен для входа в выписку", secret=True),
    Field("TONCENTER_API_KEY", "Ключ toncenter (необязательно)", "@tonapibot", secret=True),
    Field("PRICE_50_99", "Цена за звезду, 50-99 ⭐", default="1.50"),
    Field("PRICE_100_4999", "Цена за звезду, 100-4999 ⭐", default="1.32"),
    Field("PRICE_5000_PLUS", "Цена за звезду, 5000+ ⭐", default="1.24"),
    Field("USD_RATE", "Курс доллара для расчёта себестоимости", default="90"),
]

# Генерируются сами, у пользователя не спрашиваются.
GENERATED = ("BACKEND_SERVICE_TOKEN", "BACKEND_ADMIN_TOKEN")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def ask(field: Field, current: str) -> str:
    existing = current or field.default
    print(f"\n\033[1m{field.prompt}\033[0m")
    if field.hint:
        print(f"  \033[90m{field.hint}\033[0m")

    if existing:
        shown = "••••••••" if field.secret else existing
        print(f"  \033[90mсейчас: {shown}  (Enter — оставить)\033[0m")

    while True:
        try:
            answer = (getpass.getpass("  > ") if field.secret else input("  > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nОтменено.")
            sys.exit(1)

        if not answer:
            if existing:
                return existing
            if field.required:
                print("  \033[91mЭто поле обязательное.\033[0m")
                continue
            return ""

        if "\n" in answer or "\r" in answer:
            print("  \033[91mПереносы строк недопустимы.\033[0m")
            continue
        return answer


def write_env(values: dict[str, str]) -> None:
    """Пишет .env атомарно, сохраняя структуру и комментарии .env.example."""
    if EXAMPLE_PATH.exists():
        template = EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    else:
        template = [f"{key}=" for key in values]

    written: set[str] = set()
    lines: list[str] = []
    for line in template:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                lines.append(f"{key}={values[key]}")
                written.add(key)
                continue
        lines.append(line)

    for key, value in values.items():
        if key not in written:
            lines.append(f"{key}={value}")

    tmp = ENV_PATH.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV_PATH)
    os.chmod(ENV_PATH, 0o600)


def main() -> None:
    print("\n\033[1m=== Настройка AraStars ===\033[0m")
    print("Enter — оставить текущее значение. Ctrl+C — выйти без сохранения.")

    if not EXAMPLE_PATH.exists():
        print("\n\033[91m.env.example не найден. Запускайте скрипт из папки проекта.\033[0m")
        sys.exit(1)

    values = read_env(EXAMPLE_PATH)
    values.update(read_env(ENV_PATH))

    if ENV_PATH.exists():
        print(f"\n\033[90mНайден существующий .env — значения из него подставлены.\033[0m")

    for field in FIELDS:
        values[field.key] = ask(field, values.get(field.key, ""))

    # Токены бэкенда: генерируем, если их ещё нет.
    for key in GENERATED:
        if not values.get(key):
            values[key] = secrets.token_urlsafe(48)
            print(f"\n\033[92m✓ {key} сгенерирован автоматически\033[0m")

    if values["BACKEND_SERVICE_TOKEN"] == values["BACKEND_ADMIN_TOKEN"]:
        values["BACKEND_ADMIN_TOKEN"] = secrets.token_urlsafe(48)
        print("\033[92m✓ BACKEND_ADMIN_TOKEN перегенерирован (совпадал с сервисным)\033[0m")

    values.setdefault("BACKEND_URL", "http://127.0.0.1:8000")

    write_env(values)

    print("\n\033[92m✓ Файл .env сохранён, права 600 (читает только root).\033[0m")

    missing = [f.key for f in FIELDS if f.required and not values.get(f.key)]
    if missing:
        print(f"\033[91m⚠ Не заполнено обязательное: {', '.join(missing)}\033[0m")

    if not values.get("FRAGMENT_WALLET_MNEMONIC"):
        print("\033[93m⚠ Сид-фраза не задана — автовыдача звёзд работать не будет.\033[0m")
    if not values.get("OZON_PIN"):
        print("\033[93m⚠ PIN Ozon не задан — автосверка платежей работать не будет.\033[0m")

    print("\nДальше:")
    print("  ./venv/bin/python -m pytest tests/ -q")
    print("  systemctl restart ara-backend ara-client-bot")


if __name__ == "__main__":
    main()
