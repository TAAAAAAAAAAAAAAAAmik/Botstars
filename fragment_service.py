import base64
import binascii
import inspect
import json
import logging
import os
import re
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

_fragment_hash_cache: dict[str, Any] = {
    "value": FRAGMENT_HASH,
    "expires_at": 0.0,
}
FRAGMENT_HASH_CACHE_TTL_SECONDS = 300


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
        if not messages:
            raise RuntimeError(f"В transaction нет messages: {link_result}")

        wallet_address = get_wallet_raw_address()
        current_seqno = get_seqno(wallet_address)

        sent_messages = []
        last_boc = None

        for index, msg in enumerate(messages):
            recipient_addr = msg["address"]
            amount_nano = msg["amount"]
            payload_b64 = msg.get("payload", "")

            boc_b64 = build_boc_with_comment(
                recipient=recipient_addr,
                amount_nano=amount_nano,
                payload_b64=payload_b64,
                seqno=current_seqno + index,
            )

            toncenter_result = send_boc_to_toncenter(boc_b64)

            sent_messages.append(
                {
                    "address": recipient_addr,
                    "amount_nano": amount_nano,
                    "comment": extract_comment_from_payload(payload_b64),
                    "toncenter_result": toncenter_result,
                }
            )

            last_boc = boc_b64

        if not last_boc:
            raise RuntimeError("Не удалось собрать BOC для confirmReq")

        confirm_result = confirm_req(session, str(req_id), last_boc)
        logger.info("confirmReq => %s", confirm_result)
        if confirm_result.get("ok") is False:
            raise RuntimeError(f"confirmReq returned not ok: {confirm_result}")

        return {
            "ok": True,
            "username": username,
            "quantity": quantity,
            "req_id": req_id,
            "recipient": recipient,
            "search_result": search_result,
            "init_result": init_result,
            "link_result": link_result,
            "sent_messages": sent_messages,
            "confirm_result": confirm_result,
        }
