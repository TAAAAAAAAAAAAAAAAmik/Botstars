"""Тесты защиты горячего кошелька и экранирования HTML."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common import esc, money_text, quantize_money, username_text  # noqa: E402
from fragment_service import NANO, FragmentError, validate_transfer_amounts  # noqa: E402

# Реальный адрес формата TON для проверок.
VALID_ADDRESS = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"


def msg(amount_ton: float, address: str = VALID_ADDRESS) -> dict:
    return {"address": address, "amount": str(int(amount_ton * NANO)), "payload": ""}


# ---------------------------------------------------------------------------
# Лимиты переводов
# ---------------------------------------------------------------------------


def test_normal_purchase_passes():
    """1000 звёзд примерно за 2.5 TON — обычная покупка."""
    total = validate_transfer_amounts([msg(2.5)], quantity=1000)
    assert total == int(2.5 * NANO)


def test_rejects_absurd_amount_for_quantity():
    """Fragment просит 40 TON за 50 звёзд — покупка должна быть остановлена.

    Это главная защита: сумма приходит от Fragment и раньше уходила
    в блокчейн вообще без проверки.
    """
    with pytest.raises(FragmentError, match="потолка"):
        validate_transfer_amounts([msg(40)], quantity=50)


def test_rejects_amount_over_per_message_limit():
    with pytest.raises(FragmentError, match="лимит"):
        validate_transfer_amounts([msg(500)], quantity=1_000_000)


def test_rejects_empty_messages():
    with pytest.raises(FragmentError):
        validate_transfer_amounts([], quantity=100)


def test_rejects_negative_and_zero_amounts():
    with pytest.raises(FragmentError):
        validate_transfer_amounts([msg(0)], quantity=100)
    with pytest.raises(FragmentError):
        validate_transfer_amounts([{"address": VALID_ADDRESS, "amount": "-5"}], quantity=100)


def test_rejects_broken_amount():
    with pytest.raises(FragmentError):
        validate_transfer_amounts([{"address": VALID_ADDRESS, "amount": "не число"}], quantity=100)


def test_rejects_invalid_address():
    with pytest.raises(FragmentError, match="адрес"):
        validate_transfer_amounts([{"address": "не-адрес", "amount": str(NANO)}], quantity=1000)


def test_rejects_too_many_messages():
    with pytest.raises(FragmentError, match="сообщений"):
        validate_transfer_amounts([msg(0.1) for _ in range(20)], quantity=1000)


def test_sums_multiple_messages_against_order_cap():
    """Несколько сообщений считаются суммарно, а не по отдельности."""
    with pytest.raises(FragmentError):
        validate_transfer_amounts([msg(30), msg(30), msg(30)], quantity=100)


# ---------------------------------------------------------------------------
# Экранирование
# ---------------------------------------------------------------------------


def test_esc_neutralizes_html():
    """Имя с тегами больше не ломает уведомление админу.

    Раньше пользователь с именем "<b" вызывал 400 от Telegram, и уведомление
    о его заказе просто не доходило.
    """
    assert esc("<b") == "&lt;b"
    assert esc("</b>✅ Оплачено<b>") == "&lt;/b&gt;✅ Оплачено&lt;b&gt;"
    assert esc("Tom & Jerry") == "Tom &amp; Jerry"


def test_esc_handles_none_and_numbers():
    assert esc(None) == "—"
    assert esc(42) == "42"


def test_username_text_escapes_full_name():
    user = {"telegram_full_name": "<script>alert(1)</script>", "telegram_user_id": 5}
    result = username_text(user)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_username_text_prefers_username():
    assert username_text({"telegram_username": "vasya", "telegram_user_id": 5}) == "@vasya"


def test_username_text_falls_back_to_id():
    assert username_text({"telegram_user_id": 7}) == "ID 7"


# ---------------------------------------------------------------------------
# Деньги
# ---------------------------------------------------------------------------


def test_money_text_formats_two_decimals():
    assert money_text(1) == "1.00"
    assert money_text("1234.5") == "1234.50"
    assert money_text(None) == "0.00"


def test_money_text_survives_garbage():
    assert money_text("abc") == "0.00"


def test_quantize_money_rounds_half_up():
    """Округление по банковскому правилу, а не по «к чётному» из float."""
    assert str(quantize_money("2.345")) == "2.35"
    assert str(quantize_money("2.344")) == "2.34"
    assert str(quantize_money("0.005")) == "0.01"


# ---------------------------------------------------------------------------
# Fragment hash добывается автоматически
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    """Сессия, отдающая страницу покупки Stars с зашитым hash."""

    def __init__(self, page: str) -> None:
        self.page = page
        self.calls = 0

    def get(self, url: str, timeout: int = 30) -> _FakeResponse:
        self.calls += 1
        return _FakeResponse(self.page)


def test_hash_is_fetched_from_page_when_not_configured(monkeypatch):
    """Пустой FRAGMENT_HASH в конфиге не мешает: он берётся со страницы.

    Поэтому искать hash вручную не нужно — достаточно валидных cookies.
    """
    import fragment_service as fs

    monkeypatch.setattr(fs, "_fragment_hash_cache", {"value": "", "expires_at": 0.0})
    session = _FakeSession('<a href="/api?hash=abc123DEF-456_xyz">buy</a>')

    assert fs.get_fresh_fragment_hash(session) == "abc123DEF-456_xyz"
    assert session.calls == 1


def test_hash_is_cached_between_calls(monkeypatch):
    import fragment_service as fs

    monkeypatch.setattr(fs, "_fragment_hash_cache", {"value": "", "expires_at": 0.0})
    session = _FakeSession('<a href="/api?hash=cached123">buy</a>')

    fs.get_fresh_fragment_hash(session)
    fs.get_fresh_fragment_hash(session)
    assert session.calls == 1, "hash перезапрашивается на каждый вызов"


def test_hash_refetched_when_forced(monkeypatch):
    """При ответе «bad request» hash обновляется принудительно."""
    import fragment_service as fs

    monkeypatch.setattr(fs, "_fragment_hash_cache", {"value": "", "expires_at": 0.0})
    session = _FakeSession('<a href="/api?hash=first">buy</a>')

    assert fs.get_fresh_fragment_hash(session) == "first"
    session.page = '<a href="/api?hash=second">buy</a>'
    assert fs.get_fresh_fragment_hash(session, force=True) == "second"


def test_fragment_config_does_not_require_hash(monkeypatch):
    """Отсутствие FRAGMENT_HASH больше не блокирует работу Fragment."""
    import fragment_service as fs

    monkeypatch.setattr(fs, "FRAGMENT_HASH", "")
    monkeypatch.setattr(fs, "FRAGMENT_COOKIES_JSON", '{"stel_ssid":"x"}')
    monkeypatch.setattr(fs, "FRAGMENT_ACCOUNT_ADDRESS", VALID_ADDRESS)
    monkeypatch.setattr(fs, "FRAGMENT_WALLET_MNEMONIC", "word " * 24)

    fs.ensure_fragment_runtime_config()  # не должно бросать исключение


def test_fragment_config_still_requires_cookies(monkeypatch):
    import fragment_service as fs

    monkeypatch.setattr(fs, "FRAGMENT_COOKIES_JSON", "")
    monkeypatch.setattr(fs, "FRAGMENT_ACCOUNT_ADDRESS", VALID_ADDRESS)
    monkeypatch.setattr(fs, "FRAGMENT_WALLET_MNEMONIC", "word " * 24)

    with pytest.raises(ValueError, match="FRAGMENT_COOKIES_JSON"):
        fs.ensure_fragment_runtime_config()
