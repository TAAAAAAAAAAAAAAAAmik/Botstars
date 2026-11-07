# Развёртывание AraStars на сервере

Целевой путь: `/home/<user>/botstars` (например `/home/tamik/botstars`).

## Что запускается

Проект — это **три отдельных процесса**, а не один:

| Сервис | Файл | Назначение |
|---|---|---|
| `ara-backend` | `main.py` | FastAPI на `127.0.0.1:8000`, база SQLite `orders.db`, заказы, рефералы, баланс, Fragment, watcher платежей Ozon |
| `ara-client-bot` | `bot.py` | Телеграм-бот для покупателей (long-polling) |
| `ara-admin-bot` | `admin_bot.py` | Админский бот: заявки, настройки, рассылки |

Боты обращаются к бэкенду по `BACKEND_URL`, поэтому бэкенд поднимается первым.

> `server_bot_work.py` — альтернативная версия клиентского бота. **Одновременно с `bot.py` не запускать**: у них один `BOT_TOKEN`, Telegram ответит ошибкой 409 Conflict.

## Требования

- Ubuntu / Debian с systemd
- Python **3.11+** (в коде используется `from datetime import UTC`)
- Два бота, созданных в [@BotFather](https://t.me/BotFather)

## Установка

```bash
cd ~
git clone https://github.com/TAAAAAAAAAAAAAAAAmik/Botstars.git botstars
cd botstars
./deploy.sh
```

Первый запуск создаст `.env` из `.env.example` и остановится — заполните его и запустите скрипт повторно:

```bash
nano .env
./deploy.sh
```

Скрипт идемпотентен: его можно запускать сколько угодно раз, в том числе после `git pull`.

### Обязательные переменные `.env`

Без них процессы падают на старте с `ValueError`:

```ini
BOT_TOKEN=токен_клиентского_бота
ADMIN_BOT_TOKEN=токен_админского_бота
ADMIN_CHAT_ID=ваш_telegram_user_id
BACKEND_URL=http://127.0.0.1:8000
```

Остальное — по необходимости: реквизиты оплаты (`CARD_NUMBER`, `CARD_HOLDER`, `PAYMENT_REQUISITES_TEXT`), цены (`PRICE_50_99`, `PRICE_100_4999`, `PRICE_5000_PLUS`, `SALE_PRICE_PER_STAR`), Fragment (`FRAGMENT_HASH`, `FRAGMENT_COOKIES_JSON`, `FRAGMENT_ACCOUNT_ADDRESS`, `FRAGMENT_WALLET_MNEMONIC`).

`BACKEND_ADMIN_TOKEN` в `.env.example` отсутствует намеренно: если он не задан, код подставляет `ADMIN_BOT_TOKEN`. Задавайте явно, только если нужен отдельный токен для админских эндпоинтов.

### Что делает `deploy.sh`

1. Находит Python 3.11+ и создаёт `venv/`, если его ещё нет
2. Ставит зависимости из `requirements.txt`
3. Создаёт `.env` из `.env.example` и выставляет ему права `600`
4. Проверяет обязательные переменные и **не запускает сервисы**, если они пустые
5. Подставляет в `.service`-файлы реальный путь проекта и пользователя-владельца каталога, кладёт их в `/etc/systemd/system/`
6. Запускает все три сервиса и проверяет `/health`

Флаги: `--no-systemd` (только venv, зависимости и `.env`), `--units-only` (только установка юнитов, см. ниже), `--user` (поставить как пользовательские сервисы, без root), `--force` (пропустить проверку владельца каталога).

### Если у пользователя нет прав sudo

Шаг с systemd требует root. Если `sudo` отказывает (`sudo -l` покажет, что разрешено), всё остальное уже сделано — остаётся доставить юниты от root:

```bash
su -
/home/tamik/botstars/deploy.sh --units-only
systemctl enable --now ara-backend ara-client-bot ara-admin-bot
```

Либо один раз выдать права и больше к этому не возвращаться — от root:

```bash
usermod -aG sudo tamik      # затем перезайти по SSH
```

### Если root недоступен вообще

Тогда сервисы ставятся в пользовательский systemd — root не нужен совсем:

```bash
./deploy.sh --user
```

Юниты уезжают в `~/.config/systemd/user/`, управление и логи — с флагом `--user`:

```bash
systemctl --user status ara-backend ara-client-bot ara-admin-bot
journalctl --user -u ara-client-bot -f
systemctl --user restart ara-backend ara-client-bot ara-admin-bot
```

Одна оговорка: чтобы сервисы продолжали работать после выхода из SSH и поднимались при перезагрузке, нужен lingering. Скрипт пытается включить его сам (`loginctl enable-linger`), но на некоторых хостах это требует прав администратора. Если не вышло — сервисы будут жить только до закрытия последней сессии, и строчку

```bash
loginctl enable-linger tamik
```

придётся попросить выполнить того, у кого есть root.

## Управление

```bash
# логи
journalctl -u ara-backend    -f
journalctl -u ara-client-bot -f
journalctl -u ara-admin-bot  -f

# статус и перезапуск
systemctl status ara-backend ara-client-bot ara-admin-bot
sudo systemctl restart ara-backend ara-client-bot ara-admin-bot

# проверка бэкенда
curl http://127.0.0.1:8000/health
```

## Перенос на другой сервер

Код целиком приезжает из git, поэтому весь функционал переносится сам. А вот состояние — нет:
`orders.db` и `.env` в `.gitignore` и в репозиторий не попадают.

Без копирования `orders.db` теряются:

- история заказов,
- внутренние балансы пользователей,
- реферальные связи и накопленные вознаграждения,
- заявки на вывод и на доступ к продаже,
- настройки, изменённые через админ-бота (таблица `settings` перекрывает значения из `.env`).

Посмотреть, есть ли что терять, на старом сервере:

```bash
cd ~/botstars && ./venv/bin/python -c "
import sqlite3
c = sqlite3.connect('orders.db')
for t in ('users','orders','referral_transactions','balance_transactions','withdrawal_requests','settings'):
    try:
        print(f'{t:24} {c.execute(f\"select count(*) from {t}\").fetchone()[0]}')
    except Exception as exc:
        print(f'{t:24} — {exc}')
"
```

### Порядок переезда

Важно: **сначала гасим старого бота**. Если оба процесса будут опрашивать один `BOT_TOKEN`,
Telegram ответит ошибкой 409 Conflict и работать не будет ни один.

```bash
# 1. На СТАРОМ сервере — остановить и отдать данные
systemctl --user stop ara-backend ara-client-bot ara-admin-bot
scp ~/botstars/.env ~/botstars/orders.db root@НОВЫЙ_IP:/tmp/

# 2. На НОВОМ сервере, под root — подготовка
apt update && apt install -y python3 python3-venv git
adduser bot && usermod -aG sudo bot

# 3. Под пользователем bot — развернуть
su - bot
git clone -b <ветка> <репозиторий> ~/botstars
sudo mv /tmp/.env /tmp/orders.db ~/botstars/
sudo chown bot:bot ~/botstars/.env ~/botstars/orders.db
chmod 600 ~/botstars/.env
cd ~/botstars && ./deploy.sh

# 4. Проверить
systemctl status ara-backend ara-client-bot ara-admin-bot --no-pager
curl -s http://127.0.0.1:8000/health; echo
```

Ставить от root в `/root/botstars` тоже можно — `deploy.sh` подставит `User=root`, — но держать
бота под отдельным пользователем безопаснее, и юниты рассчитаны именно на это.

Старый сервер не удаляйте, пока новый не отработает пару дней: `orders.db` оттуда — единственная
копия данных.

## Обновление

```bash
cd ~/botstars
git pull
./deploy.sh
```

## Ozon (автопроверка платежей) — опционально

Watcher включается сам, если задан `OZON_COOKIES_JSON` **или** `OZON_USE_SELENIUM=true`. Иначе в логах будет `Ozon payment watcher is disabled` — это не ошибка.

Для Selenium-режима нужен Chrome с драйвером (код ищет `/usr/bin/chromedriver`):

```bash
sudo apt install -y chromium-browser chromium-chromedriver
```

```ini
OZON_USE_SELENIUM=true
OZON_HEADLESS=true
```

Профиль Chrome по умолчанию создаётся внутри проекта (`ozon_chrome_profile/`). Первичная заливка кук:

```bash
./venv/bin/python ozon_selenium_bootstrap.py
```

Состояние: `curl http://127.0.0.1:8000/payments/ozon/health`

## Безопасность

- `.env` хранится с правами `600` — не коммитить, в `.gitignore` он уже есть.
- Бэкенд слушает **только `127.0.0.1`**. Наружу порт 8000 открывать не нужно: его админские эндпоинты защищены лишь заголовком `X-Backend-Token`.
- Сервисы работают от обычного пользователя — владельца каталога проекта, не от root.

## Траблшутинг

| Симптом | Причина |
|---|---|
| `ValueError: BOT_TOKEN не найден в .env` | `.env` не заполнен |
| `TelegramConflictError` / 409 | один токен опрашивают два процесса. Проверить: `ps aux \| grep -E 'bot\.py\|server_bot_work' \| grep -v grep` |
| Бот отвечает, но заказы не создаются | не поднят бэкенд или неверный `BACKEND_URL` |
| Пустая история заказов после переезда | `orders.db` лежит в `.gitignore` и не переносится через git — копировать вручную |
| `sudo: ... can't do that` на шаге systemd | пароль не принят или нет прав sudo — см. раздел про `--units-only` |

Бэкап базы перед обновлением: `cp orders.db orders.db.$(date +%F).bak`
