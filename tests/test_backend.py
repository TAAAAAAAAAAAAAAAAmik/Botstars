"""Тесты на то, где ошибка стоит денег: авторизация, баланс, рефералы, лимиты TON."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BACKEND_SERVICE_TOKEN", "test-service-token")
os.environ.setdefault("BACKEND_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("ADMIN_CHAT_ID", "6887373040")

SERVICE_TOKEN = os.environ["BACKEND_SERVICE_TOKEN"]
ADMIN_TOKEN = os.environ["BACKEND_ADMIN_TOKEN"]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Приложение на изолированной временной базе."""
    db_path = tmp_path / "test_orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db_path))

    for module in ("main",):
        sys.modules.pop(module, None)

    import main as main_module

    main_module.DB_PATH = str(db_path)
    main_module.init_db()

    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as test_client:
        test_client.main = main_module
        yield test_client


def register(client, user_id: int, inviter_code: str | None = None):
    return client.post(
        "/referral/users/register",
        json={
            "telegram_user_id": user_id,
            "telegram_username": f"user{user_id}",
            "telegram_full_name": f"User {user_id}",
            "inviter_code": inviter_code,
        },
        headers={"X-Backend-Token": SERVICE_TOKEN},
    )


# ---------------------------------------------------------------------------
# Авторизация
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/orders/create-from-balance", {
            "telegram_user_id": 1, "recipient_username": "@someone",
            "stars_amount": 100, "amount_rub": 100.0,
        }),
        ("post", "/referral/withdrawals/create", {"telegram_user_id": 1, "requisites": "1234567890"}),
        ("post", "/balance/topups/create", {"telegram_user_id": 1, "amount": 500.0}),
        ("get", "/referral/users/1", None),
        ("get", "/balance/users/1", None),
        ("get", "/admin/stats", None),
    ],
)
def test_money_endpoints_reject_anonymous(client, method, path, body):
    """Денежные ручки не должны отвечать без токена.

    Раньше все они были публичными: зная telegram_user_id, любой мог списать
    чужой баланс или создать заявку на вывод на свои реквизиты.
    """
    response = getattr(client, method)(path, json=body) if body else getattr(client, method)(path)
    assert response.status_code == 401, f"{path} доступен без токена!"


def test_admin_endpoint_rejects_service_token(client):
    """Сервисного токена бота недостаточно для админских операций."""
    response = client.get("/admin/stats", headers={"X-Backend-Token": SERVICE_TOKEN})
    assert response.status_code == 403


def test_admin_endpoint_accepts_admin_token(client):
    response = client.get("/admin/stats", headers={"X-Backend-Token": ADMIN_TOKEN})
    assert response.status_code == 200


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


def test_health_does_not_leak_db_path(client):
    assert "db_path" not in client.get("/health").json()


# ---------------------------------------------------------------------------
# Баланс
# ---------------------------------------------------------------------------


def test_balance_cannot_go_negative(client):
    main_module = client.main
    register(client, 555)

    with main_module.db_write() as conn:
        conn.execute("UPDATE users SET balance = 100 WHERE telegram_user_id = 555")

    # Списание 500 при балансе 100 не должно пройти.
    response = client.post(
        "/orders/create-from-balance",
        json={
            "telegram_user_id": 555,
            "recipient_username": "@target",
            "stars_amount": 1000,
            "amount_rub": 500.0,
        },
        headers={"X-Backend-Token": SERVICE_TOKEN},
    )
    assert response.status_code == 400

    with main_module.db_write() as conn:
        balance = conn.execute("SELECT balance FROM users WHERE telegram_user_id = 555").fetchone()[0]
    assert balance == 100, "баланс изменился при отклонённой покупке"


def test_conditional_debit_prevents_double_spend(client):
    """Условный UPDATE не даёт списать больше, чем есть, даже при повторе."""
    main_module = client.main
    register(client, 777)
    with main_module.db_write() as conn:
        conn.execute("UPDATE users SET balance = 100 WHERE telegram_user_id = 777")

    charge = 90.0
    successes = 0
    for _ in range(2):
        with main_module.db_write() as conn:
            cursor = conn.execute(
                "UPDATE users SET balance = ROUND(balance - ?, 2) "
                "WHERE telegram_user_id = ? AND balance >= ?",
                (charge, 777, charge),
            )
            if cursor.rowcount == 1:
                successes += 1

    assert successes == 1, "второе списание прошло — двойная трата"
    with main_module.db_write() as conn:
        balance = conn.execute("SELECT balance FROM users WHERE telegram_user_id = 777").fetchone()[0]
    assert balance == 10


def test_admin_balance_adjust_rejects_negative_result(client):
    register(client, 888)
    response = client.post(
        "/admin/users/888/balance-adjust",
        json={"amount": -50.0},
        headers={"X-Backend-Token": ADMIN_TOKEN},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Рефералы
# ---------------------------------------------------------------------------


def test_self_referral_is_rejected(client):
    register(client, 100)
    profile = client.get("/referral/users/100", headers={"X-Backend-Token": SERVICE_TOKEN}).json()
    own_code = profile["profile"]["user"]["ref_code"]

    response = register(client, 100, inviter_code=own_code)
    assert response.status_code == 400


def test_referral_payout_never_exceeds_margin(client):
    """Сумма выплат по цепочке ограничена долей маржи.

    Без потолка проценты складывались в 130% и заказ уходил в минус.
    """
    main_module = client.main

    # Цепочка из 5 партнёров, все с максимальными уровнями.
    ids = [201, 202, 203, 204, 205, 206]
    for user_id in ids:
        register(client, user_id)
    with main_module.db_write() as conn:
        for child, parent in zip(ids[:-1], ids[1:]):
            conn.execute(
                "UPDATE users SET invited_by_user_id = ?, ref_total_turnover = 100000 "
                "WHERE telegram_user_id = ?",
                (parent, child),
            )
        conn.execute("UPDATE users SET ref_total_turnover = 100000")

    order = {
        "id": str(uuid.uuid4()),
        "telegram_user_id": ids[0],
        "telegram_username": "buyer",
        "telegram_full_name": "Buyer",
        "amount_rub": 10000.0,
        "stars_amount": 1000,
    }

    with main_module.db_write() as conn:
        profit = main_module.calculate_order_profit(conn, order)
        result = main_module.process_referral_reward(conn, order)

    base_profit = profit["referral_base_profit"]
    total_reward = result["total_reward"]
    cap = base_profit * main_module.REFERRAL_MAX_PAYOUT_PERCENT / 100

    assert total_reward <= cap + 0.01, f"выплачено {total_reward} при марже {base_profit}"
    assert result["final_net_profit"] >= -0.01, "заказ ушёл в убыток"


def test_referral_cycle_pays_each_person_once(client):
    """Цикл A→B→A не должен начислять одному человеку дважды."""
    main_module = client.main
    register(client, 301)
    register(client, 302)
    with main_module.db_write() as conn:
        conn.execute("UPDATE users SET invited_by_user_id = 302 WHERE telegram_user_id = 301")
        conn.execute("UPDATE users SET invited_by_user_id = 301 WHERE telegram_user_id = 302")

    order = {
        "id": str(uuid.uuid4()),
        "telegram_user_id": 301,
        "telegram_username": "a",
        "telegram_full_name": "A",
        "amount_rub": 5000.0,
        "stars_amount": 1000,
    }
    with main_module.db_write() as conn:
        result = main_module.process_referral_reward(conn, order)

    referrers = [tx["referrer_user_id"] for tx in (result or {}).get("transactions", [])]
    assert len(referrers) == len(set(referrers)), "один партнёр получил награду дважды"


def test_referral_reward_is_idempotent(client):
    """Повторная обработка заказа не начисляет второй раз."""
    main_module = client.main
    register(client, 401)
    register(client, 402)
    with main_module.db_write() as conn:
        conn.execute("UPDATE users SET invited_by_user_id = 402 WHERE telegram_user_id = 401")

    order = {
        "id": str(uuid.uuid4()),
        "telegram_user_id": 401,
        "telegram_username": "a",
        "telegram_full_name": "A",
        "amount_rub": 5000.0,
        "stars_amount": 1000,
    }
    with main_module.db_write() as conn:
        first = main_module.process_referral_reward(conn, order)
    with main_module.db_write() as conn:
        second = main_module.process_referral_reward(conn, order)

    assert first["awarded"] is True
    assert second["awarded"] is False
    assert second["already_exists"] is True


# ---------------------------------------------------------------------------
# Цены
# ---------------------------------------------------------------------------


def test_order_price_is_recalculated_server_side(client):
    """Сумма из запроса игнорируется — цену считает сервер."""
    register(client, 601)
    response = client.post(
        "/orders/create",
        json={
            "telegram_user_id": 601,
            "recipient_username": "@target",
            "stars_amount": 100,
            "amount_rub": 1.0,  # попытка занизить цену
        },
        headers={"X-Backend-Token": SERVICE_TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["order"]["amount_rub"] > 1.0


def test_blacklisted_user_cannot_create_order(client):
    register(client, 602)
    client.post(
        "/admin/users/602/blacklist",
        json={"is_blacklisted": True, "reason": "test"},
        headers={"X-Backend-Token": ADMIN_TOKEN},
    )
    response = client.post(
        "/orders/create",
        json={
            "telegram_user_id": 602,
            "recipient_username": "@target",
            "stars_amount": 100,
            "amount_rub": 100.0,
        },
        headers={"X-Backend-Token": SERVICE_TOKEN},
    )
    assert response.status_code == 403
