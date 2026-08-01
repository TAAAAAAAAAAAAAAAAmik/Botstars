import base64
import binascii
import inspect
import json
import logging
import os
import re
import threading
import time
from typing import Any

import requests
from dotenv import load_dotenv
from tonsdk.boc import Cell
from tonsdk.contract.wallet import Wallets, WalletVersionEnum
from tonsdk.utils import Address

load_dotenv(dotenv_path=".env")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NANO = 1_000_000_000


class FragmentError(RuntimeError):
    """Ошибка до отправки средств. Повторять покупку безопасно."""


class FragmentFundsSentError(RuntimeError):
    """Средства уже ушли в сеть, но заказ не завершён.

    Повторять покупку НЕЛЬЗЯ — будет двойная трата. Заказ обязан уйти
    на ручной разбор.
    """

    def __init__(self, message: str, sent_messages: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.sent_messages = sent_messages or []

FRAGMENT_API_URL = os.getenv("FRAGMENT_API_URL", "https://fragment.com/api").strip()
FRAGMENT_STARS_BUY_URL = os.getenv("FRAGMENT_STARS_BUY_URL", "https://fragment.com/stars/buy").strip()
FRAGMENT_HASH = os.getenv("FRAGMENT_HASH", "").strip()
FRAGMENT_COOKIES_JSON = os.getenv("FRAGMENT_COOKIES_JSON", "").strip()

FRAGMENT_ACCOUNT_ADDRESS = os.getenv("FRAGMENT_ACCOUNT_ADDRESS", "").strip()
FRAGMENT_ACCOUNT_CHAIN = os.getenv("FRAGMENT_ACCOUNT_CHAIN", "-239").strip()
FRAGMENT_SHOW_SENDER = int(os.getenv("FRAGMENT_SHOW_SENDER", "1").strip())

FRAGMENT_WALLET_MNEMONIC = os.getenv("FRAGMENT_WALLET_MNEMONIC", "").strip()
FRAGMENT_WALLET_VERSION = os.getenv("FRAGMENT_WALLET_VERSION", "v4r2").strip().lower()

TONCENTER_SEND_BOC_URL = os.getenv("TONCENTER_SEND_BOC_URL", "https://toncenter.com/api/v2/sendBoc").strip()
TONCENTER_RUNGET_URL = os.getenv("TONCENTER_RUNGET_URL", "https://toncenter.com/api/v2/runGetMethod").strip()
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "").strip()

# Защита горячего кошелька: Fragment сам присылает сумму перевода, и без
# верхней границы протухшая сессия или подменённый ответ уводят весь баланс.
FRAGMENT_MAX_TON_PER_MESSAGE = float(os.getenv("FRAGMENT_MAX_TON_PER_MESSAGE", "50").strip() or 50)
FRAGMENT_MAX_TON_PER_ORDER = float(os.getenv("FRAGMENT_MAX_TON_PER_ORDER", "100").strip() or 100)
# Сколько TON максимум допустимо за 1000 звёзд. Ориентир: ~1000 звёзд ≈ 2-3 TON,
# берём запас x3, чтобы скачок курса не блокировал выдачу.
FRAGMENT_MAX_TON_PER_1000_STARS = float(os.getenv("FRAGMENT_MAX_TON_PER_1000_STARS", "9").strip() or 9)
FRAGMENT_MAX_MESSAGES_PER_ORDER = int(os.getenv("FRAGMENT_MAX_MESSAGES_PER_ORDER", "4").strip() or 4)
# Ожидание подтверждения перевода в сети (рост seqno кошелька).
FRAGMENT_SEQNO_CONFIRM_TIMEOUT = int(os.getenv("FRAGMENT_SEQNO_CONFIRM_TIMEOUT", "90").strip() or 90)
FRAGMENT_SEQNO_POLL_INTERVAL = float(os.getenv("FRAGMENT_SEQNO_POLL_INTERVAL", "3").strip() or 3)

# Кошелёк один, seqno — общий счётчик. Две параллельные покупки прочитают
# одинаковый seqno, и один перевод молча потеряется. Лок обязателен.
_WALLET_LOCK = threading.Lock()

_fragment_hash_cache: dict[str, Any] = {
    "value": FRAGMENT_HASH,
    "expires_at": 0.0,
}
FRAGMENT_HASH_CACHE_TTL_SECONDS = 300


def apply_runtime_config(updates: dict[str, str]) -> None:
    """Обновляет конфигурацию Fragment без перезапуска процесса.

    Значения читаются в модульные переменные при импорте, поэтому после
    записи в .env их нужно применить и в памяти — иначе новые куки или
    кошелёк подхватятся только после restart.
    """
    global FRAGMENT_HASH, FRAGMENT_COOKIES_JSON, FRAGMENT_ACCOUNT_ADDRESS
    global FRAGMENT_ACCOUNT_CHAIN, FRAGMENT_WALLET_MNEMONIC, FRAGMENT_WALLET_VERSION

    mapping = {
        "FRAGMENT_HASH": "FRAGMENT_HASH",
        "FRAGMENT_COOKIES_JSON": "FRAGMENT_COOKIES_JSON",
        "FRAGMENT_ACCOUNT_ADDRESS": "FRAGMENT_ACCOUNT_ADDRESS",
        "FRAGMENT_ACCOUNT_CHAIN": "FRAGMENT_ACCOUNT_CHAIN",
        "FRAGMENT_WALLET_MNEMONIC": "FRAGMENT_WALLET_MNEMONIC",
        "FRAGMENT_WALLET_VERSION": "FRAGMENT_WALLET_VERSION",
    }
    for key, value in updates.items():
        if key in mapping:
            globals()[mapping[key]] = str(value).strip()

    if "FRAGMENT_HASH" in updates:
        _fragment_hash_cache["value"] = FRAGMENT_HASH
        _fragment_hash_cache["expires_at"] = 0.0
    elif "FRAGMENT_COOKIES_JSON" in updates:
        # Новые куки — старый hash мог протухнуть вместе со старой сессией.
        _fragment_hash_cache["expires_at"] = 0.0


def validate_mnemonic(mnemonic: str) -> str:
    """Проверяет сид-фразу и возвращает адрес кошелька.

    Так админ сразу видит, что фраза введена верно и относится к тому
    кошельку, с которого ожидается списание.
    """
    words = [word.strip().lower() for word in mnemonic.split() if word.strip()]
    if len(words) not in (12, 18, 24):
        raise ValueError(f"Ожидается 12, 18 или 24 слова, получено {len(words)}")
    if not all(word.isalpha() and word.isascii() for word in words):
        raise ValueError("Сид-фраза должна состоять только из латинских слов")

    try:
        _, _, _, wallet = Wallets.from_mnemonics(words, get_wallet_version(), workchain=0)
    except Exception as exc:
        raise ValueError(f"Некорректная сид-фраза: {exc}") from exc

    return wallet.address.to_string(is_user_friendly=True, is_url_safe=True, is_bounceable=False)


def validate_address(address: str) -> str:
    """Проверяет адрес TON-кошелька."""
    try:
        return Address(address.strip()).to_string(is_user_friendly=True, is_url_safe=True, is_bounceable=False)
    except Exception as exc:
        raise ValueError(f"Некорректный адрес TON: {exc}") from exc


def require_env(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"{name} не найден в .env")


def ensure_fragment_runtime_config() -> None:
    for key, value in [
        ("FRAGMENT_HASH", FRAGMENT_HASH),
        ("FRAGMENT_COOKIES_JSON", FRAGMENT_COOKIES_JSON),
        ("FRAGMENT_ACCOUNT_ADDRESS", FRAGMENT_ACCOUNT_ADDRESS),
        ("FRAGMENT_WALLET_MNEMONIC", FRAGMENT_WALLET_MNEMONIC),
    ]:
        require_env(key, value)


def get_wallet_version() -> WalletVersionEnum:
    versions = {
        "v3r2": WalletVersionEnum.v3r2,
        "v4r2": WalletVersionEnum.v4r2,
    }
    return versions.get(FRAGMENT_WALLET_VERSION, WalletVersionEnum.v4r2)


def get_cookies_dict() -> dict[str, str]:
    try:
        data = json.loads(FRAGMENT_COOKIES_JSON)
    except json.JSONDecodeError as exc:
        raise ValueError("FRAGMENT_COOKIES_JSON не является валидным JSON") from exc

    if not isinstance(data, dict):
        raise ValueError("FRAGMENT_COOKIES_JSON должен быть JSON-объектом")

    return {str(k): str(v) for k, v in data.items()}


def make_session() -> requests.Session:
    ensure_fragment_runtime_config()
    session = requests.Session()
    session.cookies.update(get_cookies_dict())
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": FRAGMENT_STARS_BUY_URL,
        }
    )
    return session


def toncenter_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if TONCENTER_API_KEY:
        headers["X-API-Key"] = TONCENTER_API_KEY
    return headers


def fix_base64(value: str) -> str:
    value = value.replace("-", "+").replace("_", "/")
    return value + ("=" * (-len(value) % 4))


def get_fresh_fragment_hash(session: requests.Session, *, force: bool = False) -> str:
    now = time.time()
    cached_value = str(_fragment_hash_cache.get("value") or "").strip()
    cached_expires_at = float(_fragment_hash_cache.get("expires_at") or 0)
    if cached_value and not force and now < cached_expires_at:
        return cached_value

    response = session.get(FRAGMENT_STARS_BUY_URL, timeout=30)
    response.raise_for_status()

    match = re.search(r"/api\?hash=([A-Za-z0-9_-]+)", response.text)
    if not match:
        raise RuntimeError("Не удалось получить актуальный Fragment hash со страницы покупки Stars")

    fresh_hash = match.group(1)
    _fragment_hash_cache["value"] = fresh_hash
    _fragment_hash_cache["expires_at"] = now + FRAGMENT_HASH_CACHE_TTL_SECONDS
    return fresh_hash


def fragment_post(session: requests.Session, params: dict[str, Any]) -> dict[str, Any]:
    params = dict(params)
    if "hash" in params:
        params["hash"] = get_fresh_fragment_hash(session)

    response = session.post(FRAGMENT_API_URL, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, dict) and str(data.get("error") or "").lower() == "bad request" and "hash" in params:
        params["hash"] = get_fresh_fragment_hash(session, force=True)
        response = session.post(FRAGMENT_API_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

    return data


def send_boc_to_toncenter(boc_b64: str) -> dict[str, Any]:
    ensure_fragment_runtime_config()
    response = requests.post(
        TONCENTER_SEND_BOC_URL,
        headers=toncenter_headers(),
        json={"boc": boc_b64},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok", False):
        raise RuntimeError(f"Toncenter sendBoc returned not ok: {data}")
    return data


def confirm_req(session: requests.Session, req_id: str, boc_b64: str) -> dict[str, Any]:
    ensure_fragment_runtime_config()
    account_data = json.dumps(
        {
            "address": FRAGMENT_ACCOUNT_ADDRESS,
            "chain": FRAGMENT_ACCOUNT_CHAIN,
        }
    )

    params = {
        "method": "confirmReq",
        "hash": FRAGMENT_HASH,
        "id": req_id,
        "boc": boc_b64,
        "account": account_data,
    }
    return fragment_post(session, params)


def extract_comment_from_payload(payload_b64: str) -> str | None:
    try:
        payload_bytes = base64.b64decode(fix_base64(payload_b64))
        try:
            payload_cell = Cell.one_from_boc(payload_bytes)
            payload_text = payload_cell.bits.get_top_upped_array().decode("utf-8", errors="ignore")
            payload_text = payload_text.lstrip("\x00")
            return " ".join(payload_text.split())
        except Exception:
            return payload_bytes.decode("utf-8", errors="ignore").strip()
    except (binascii.Error, UnicodeDecodeError):
        return None


def _payload_comment_cell(payload_b64: str) -> Cell:
    payload_bytes = base64.b64decode(fix_base64(payload_b64))
    payload_cell = Cell.one_from_boc(payload_bytes)
    payload_text = payload_cell.bits.get_top_upped_array().decode("utf-8", errors="ignore").strip()

    comment_cell = Cell()
    comment_cell.bits.write_uint(0, 32)
    comment_cell.bits.write_bytes(payload_text.encode("utf-8"))
    return comment_cell


def _create_transfer_message_compat(
    wallet: Any,
    private_key: bytes,
    recipient: str,
    amount_nano: int,
    seqno: int,
    payload: Cell,
) -> dict[str, Any]:
    addr = Address(recipient)

    attempts = [
        lambda: wallet.create_transfer_message(
            private_key,
            addr,
            amount_nano,
            seqno,
            payload=payload,
            send_mode=3,
        ),
        lambda: wallet.create_transfer_message(
            secret_key=private_key,
            to_addr=addr,
            amount=amount_nano,
            seqno=seqno,
            payload=payload,
            send_mode=3,
        ),
        lambda: wallet.create_transfer_message(
            private_key=private_key,
            to_addr=addr,
            amount=amount_nano,
            seqno=seqno,
            payload=payload,
            send_mode=3,
        ),
        lambda: wallet.create_transfer_message(
            addr,
            amount_nano,
            seqno,
            payload=payload,
            send_mode=3,
        ),
        lambda: wallet.create_transfer_message(
            to_addr=addr,
            amount=amount_nano,
            seqno=seqno,
            payload=payload,
            send_mode=3,
        ),
    ]

    last_exc: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_exc = exc
            continue

    signature = inspect.signature(wallet.create_transfer_message)
    raise TypeError(f"Не удалось вызвать create_transfer_message. Signature: {signature}. Last error: {last_exc}")


def build_boc_with_comment(recipient: str, amount_nano: str | int, payload_b64: str, seqno: int) -> str:
    ensure_fragment_runtime_config()
    mnemonics = [w.strip() for w in FRAGMENT_WALLET_MNEMONIC.split() if w.strip()]
    _, _, private_key, wallet = Wallets.from_mnemonics(
        mnemonics,
        get_wallet_version(),
        workchain=0,
    )

    comment_cell = _payload_comment_cell(payload_b64)
    transfer_msg = _create_transfer_message_compat(
        wallet=wallet,
        private_key=private_key,
        recipient=recipient,
        amount_nano=int(amount_nano),
        seqno=seqno,
        payload=comment_cell,
    )

    boc_bytes = transfer_msg["message"].to_boc(False)
    return base64.b64encode(boc_bytes).decode()


def get_wallet_raw_address() -> str:
    mnemonics = [w.strip() for w in FRAGMENT_WALLET_MNEMONIC.split() if w.strip()]
    _, _, _, wallet = Wallets.from_mnemonics(
        mnemonics,
        get_wallet_version(),
        workchain=0,
    )

    return wallet.address.to_string(
        is_user_friendly=False,
        is_url_safe=False,
        is_bounceable=False,
        is_test_only=False,
    )


def validate_transfer_amounts(messages: list[dict[str, Any]], quantity: int) -> int:
    """Проверяет суммы, которые прислал Fragment, до отправки в блокчейн.

    Возвращает суммарную сумму в нанотонах. Бросает FragmentError, если
    что-то выходит за разумные пределы — лучше сорвать выдачу, чем отдать
    кошелёк.
    """
    if not messages:
        raise FragmentError("Fragment не вернул ни одного сообщения для перевода")
    if len(messages) > FRAGMENT_MAX_MESSAGES_PER_ORDER:
        raise FragmentError(
            f"Fragment вернул {len(messages)} сообщений, допустимо не больше "
            f"{FRAGMENT_MAX_MESSAGES_PER_ORDER}"
        )

    total_nano = 0
    for index, msg in enumerate(messages):
        raw_address = str(msg.get("address") or "").strip()
        if not raw_address:
            raise FragmentError(f"Сообщение #{index} без адреса получателя")
        try:
            Address(raw_address)
        except Exception as exc:
            raise FragmentError(f"Некорректный адрес получателя в сообщении #{index}: {raw_address}") from exc

        try:
            amount_nano = int(msg.get("amount"))
        except (TypeError, ValueError) as exc:
            raise FragmentError(f"Некорректная сумма в сообщении #{index}: {msg.get('amount')!r}") from exc

        if amount_nano <= 0:
            raise FragmentError(f"Неположительная сумма в сообщении #{index}: {amount_nano}")
        if amount_nano > FRAGMENT_MAX_TON_PER_MESSAGE * NANO:
            raise FragmentError(
                f"Сумма перевода #{index} = {amount_nano / NANO:.4f} TON превышает лимит "
                f"{FRAGMENT_MAX_TON_PER_MESSAGE} TON на сообщение"
            )
        total_nano += amount_nano

    if total_nano > FRAGMENT_MAX_TON_PER_ORDER * NANO:
        raise FragmentError(
            f"Суммарный перевод {total_nano / NANO:.4f} TON превышает лимит "
            f"{FRAGMENT_MAX_TON_PER_ORDER} TON на заказ"
        )

    expected_cap_nano = (max(quantity, 1) / 1000.0) * FRAGMENT_MAX_TON_PER_1000_STARS * NANO
    if total_nano > expected_cap_nano:
        raise FragmentError(
            f"Fragment просит {total_nano / NANO:.4f} TON за {quantity} звёзд — это выше "
            f"потолка {expected_cap_nano / NANO:.4f} TON. Покупка остановлена."
        )

    return total_nano


def wait_for_seqno(wallet_address: str, expected_seqno: int) -> bool:
    """Ждёт, пока seqno кошелька дорастёт до expected_seqno.

    sendBoc возвращает ok сразу после приёма сообщения сетью — это не значит,
    что перевод исполнен. Без этой проверки заказ помечается выданным,
    когда звёзды могли не уйти.
    """
    deadline = time.time() + FRAGMENT_SEQNO_CONFIRM_TIMEOUT
    while time.time() < deadline:
        time.sleep(FRAGMENT_SEQNO_POLL_INTERVAL)
        try:
            if get_seqno(wallet_address) >= expected_seqno:
                return True
        except Exception as exc:
            logger.warning("Не удалось прочитать seqno при подтверждении: %s", exc)
    return False


def get_seqno(wallet_address: str) -> int:
    payload = {
        "address": wallet_address,
        "method": "seqno",
        "stack": [],
    }

    response = requests.post(
        TONCENTER_RUNGET_URL,
        headers=toncenter_headers(),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Ошибка при запросе seqno: {result}")

    stack = result.get("result", {}).get("stack", [])
    if not stack:
        return 0

    seqno_hex = stack[0][1]
    return int(seqno_hex, 16)


def search_stars_recipient(session: requests.Session, username: str, quantity: int = 50) -> dict[str, Any]:
    params = {
        "method": "searchStarsRecipient",
        "hash": FRAGMENT_HASH,
        "query": username,
        "quantity": quantity,
    }
    return fragment_post(session, params)


def init_buy_stars_request(session: requests.Session, recipient: str, quantity: int) -> dict[str, Any]:
    params = {
        "method": "initBuyStarsRequest",
        "hash": FRAGMENT_HASH,
        "recipient": recipient,
        "quantity": quantity,
    }
    return fragment_post(session, params)


def get_buy_stars_link(session: requests.Session, req_id: str, raw_hex_addr: str, chain_str: str) -> dict[str, Any]:
    account_data = json.dumps(
        {
            "address": raw_hex_addr,
            "chain": chain_str,
        }
    )

    device_data = json.dumps(
        {
            "platform": "browser",
            "appName": "telegram-wallet",
            "appVersion": "1",
            "maxProtocolVersion": 2,
            "features": ["SendTransaction", {"name": "SendTransaction", "maxMessages": 4}],
        }
    )

    params = {
        "method": "getBuyStarsLink",
        "hash": FRAGMENT_HASH,
        "id": req_id,
        "transaction": 1,
        "show_sender": FRAGMENT_SHOW_SENDER,
        "account": account_data,
        "device": device_data,
    }
    return fragment_post(session, params)


def normalize_recipient(username: str) -> str:
    username = username.strip()
    return username if username.startswith("@") else f"@{username}"


def auto_buy_stars(username: str, quantity: int) -> dict[str, Any]:
    """Покупает звёзды на Fragment с горячего TON-кошелька.

    Кошелёк один на весь сервис, поэтому вся операция выполняется под локом:
    иначе две параллельные покупки возьмут одинаковый seqno и один перевод
    потеряется. После каждой отправки ждём подтверждения в сети.
    """
    with _WALLET_LOCK:
        return _auto_buy_stars_locked(username, quantity)


def _auto_buy_stars_locked(username: str, quantity: int) -> dict[str, Any]:
    ensure_fragment_runtime_config()
    if quantity < 50:
        raise ValueError("Минимальный заказ — 50 звёзд")

    username = normalize_recipient(username)

    with make_session() as session:
        search_result = search_stars_recipient(session, username, quantity)
        logger.info("searchStarsRecipient => %s", search_result)

        if not search_result:
            raise RuntimeError("Пустой ответ searchStarsRecipient")
        if search_result.get("ok") is False:
            raise RuntimeError(f"searchStarsRecipient returned not ok: {search_result}")

        recipient = (
            search_result.get("recipient")
            or (search_result.get("found") or {}).get("recipient")
            or (search_result.get("user") or {}).get("recipient")
        )

        if not recipient:
            raise RuntimeError(f"Не удалось найти recipient для {username}: {search_result}")

        init_result = init_buy_stars_request(session, recipient, quantity)
        logger.info("initBuyStarsRequest => %s", init_result)
        if init_result.get("ok") is False:
            raise RuntimeError(f"initBuyStarsRequest returned not ok: {init_result}")

        req_id = init_result.get("req_id") or init_result.get("id")
        if not req_id:
            raise RuntimeError(f"Не найден req_id: {init_result}")

        link_result = get_buy_stars_link(
            session,
            str(req_id),
            FRAGMENT_ACCOUNT_ADDRESS,
            FRAGMENT_ACCOUNT_CHAIN,
        )
        logger.info("getBuyStarsLink => %s", link_result)
        if link_result.get("ok") is False:
            raise RuntimeError(f"getBuyStarsLink returned not ok: {link_result}")

        tx = link_result.get("transaction") or {}
        messages = tx.get("messages") or []

        # Ключевая проверка: суммы приходят от Fragment и до этого момента
        # уходили в блокчейн без единой проверки.
        total_nano = validate_transfer_amounts(messages, quantity)
        logger.info(
            "Fragment запросил %.4f TON за %s звёзд (%s сообщений) — в пределах лимитов",
            total_nano / NANO,
            quantity,
            len(messages),
        )

        wallet_address = get_wallet_raw_address()
        start_seqno = get_seqno(wallet_address)

        sent_messages: list[dict[str, Any]] = []
        last_boc = None

        for index, msg in enumerate(messages):
            recipient_addr = msg["address"]
            amount_nano = int(msg["amount"])
            payload_b64 = msg.get("payload", "")
            seqno = start_seqno + index

            boc_b64 = build_boc_with_comment(
                recipient=recipient_addr,
                amount_nano=amount_nano,
                payload_b64=payload_b64,
                seqno=seqno,
            )

            try:
                toncenter_result = send_boc_to_toncenter(boc_b64)
            except Exception as exc:
                if sent_messages:
                    # Часть переводов уже ушла — повторять заказ нельзя.
                    raise FragmentFundsSentError(
                        f"Перевод #{index} не отправлен ({exc}), но {len(sent_messages)} "
                        "предыдущих уже ушли в сеть. Требуется ручная проверка.",
                        sent_messages,
                    ) from exc
                raise FragmentError(f"Не удалось отправить перевод: {exc}") from exc

            sent_messages.append(
                {
                    "address": recipient_addr,
                    "amount_nano": amount_nano,
                    "comment": extract_comment_from_payload(payload_b64),
                    "toncenter_result": toncenter_result,
                }
            )
            last_boc = boc_b64

            # Следующее сообщение требует, чтобы предыдущее уже закоммитилось,
            # иначе его seqno окажется невалидным и перевод отбракуют.
            if not wait_for_seqno(wallet_address, seqno + 1):
                raise FragmentFundsSentError(
                    f"Перевод #{index} отправлен, но сеть не подтвердила его за "
                    f"{FRAGMENT_SEQNO_CONFIRM_TIMEOUT} c. Требуется ручная проверка.",
                    sent_messages,
                )

        if not last_boc:
            raise FragmentError("Не удалось собрать BOC для confirmReq")

        try:
            confirm_result = confirm_req(session, str(req_id), last_boc)
        except Exception as exc:
            raise FragmentFundsSentError(
                f"Переводы отправлены и подтверждены, но confirmReq упал: {exc}. "
                "Повторять покупку нельзя — нужна ручная проверка.",
                sent_messages,
            ) from exc

        logger.info("confirmReq => %s", confirm_result)
        if confirm_result.get("ok") is False:
            raise FragmentFundsSentError(
                f"Переводы отправлены, но confirmReq вернул ошибку: {confirm_result}. "
                "Повторять покупку нельзя — нужна ручная проверка.",
                sent_messages,
            )

        return {
            "ok": True,
            "username": username,
            "quantity": quantity,
            "req_id": req_id,
            "recipient": recipient,
            "total_ton": round(total_nano / NANO, 6),
            "search_result": search_result,
            "init_result": init_result,
            "link_result": link_result,
            "sent_messages": sent_messages,
            "confirm_result": confirm_result,
        }
