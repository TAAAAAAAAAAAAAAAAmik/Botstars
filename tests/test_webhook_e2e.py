"""End-to-end: a signed bank callback must settle a pending order."""
import hashlib, hmac, json, os, sqlite3, sys, tempfile, uuid
from datetime import datetime, timezone

tmp = tempfile.mkdtemp(prefix="arastars-e2e-")
DB = os.path.join(tmp, "orders.db")
SECRET = "webhook-test-secret"

os.environ.update({
    "ORDERS_DB_PATH": DB,
    "PAYMENT_PROVIDER": "webhook",
    "PAYMENT_WEBHOOK_SECRET": SECRET,
    "PAYMENT_WEBHOOK_EVENTS_PATH": "Data.Transaction",
    "PAYMENT_WEBHOOK_FIELD_ID": "transactionId",
    "PAYMENT_WEBHOOK_FIELD_AMOUNT": "Amount.amount",
    "PAYMENT_WEBHOOK_FIELD_COMMENT": "description",
    "PAYMENT_WEBHOOK_FIELD_TIME": "bookingTimestamp",
    "PAYMENT_WEBHOOK_AMOUNT_UNIT": "rub",
    "PAYMENT_WEBHOOK_LABEL": "tochka",
    "OZON_USE_SELENIUM": "false",
    "OZON_COOKIES_JSON": "",
})

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import main
from fastapi.testclient import TestClient

fails = []
def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")

# Replace the real settlement side effects (Telegram notify + Fragment buy)
# with a recorder: this test covers the new plumbing, not the existing flow.
settled = []
async def fake_process(payment, operation):
    settled.append((payment["id"], main.make_operation_id(operation)))
    main.update_payment_audit(
        payment["table"], payment["id"],
        paid_operation_id=main.make_operation_id(operation),
        paid_operation_time=str(operation.get("time") or ""),
        processing_notified_at=main.now_iso(),
    )
main.process_matched_payment = fake_process

COMMENT = "ARA-DEADBEEF"
AMOUNT = 1234.56

def insert_pending_order(comment=COMMENT, amount=AMOUNT):
    oid = uuid.uuid4().hex
    now = main.now_iso()
    conn = sqlite3.connect(DB)
    conn.execute(
        """INSERT INTO orders (id, order_id, telegram_user_id, telegram_username,
           telegram_full_name, recipient_username, stars_amount, stars_count, stars,
           amount_rub, amount, status, payment_source, balance_charge_amount,
           payment_comment, payable_amount, payment_match_mode, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (oid, oid[:8], 111, "tester", "Tester", "recipient", 100, 100, 100,
         amount, amount, main.OrderStatus.payment_pending.value, "card", 0,
         comment, amount, "comment", now, now),
    )
    conn.commit(); conn.close()
    return oid

def body_for(comment, amount, txid="tx-1"):
    return json.dumps({"Data": {"Transaction": [{
        "transactionId": txid,
        "Amount": {"amount": f"{amount:.2f}"},
        "description": f"Перевод СБП {comment}",
        "bookingTimestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }]}}).encode("utf-8")

def sign(body):
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

with TestClient(main.app) as client:
    # provider wired at startup?
    h = client.get("/payments/health").json()
    check("provider name", h.get("provider"), "webhook")
    check("provider mode", h.get("mode"), "push")
    check("provider label", h.get("label"), "tochka")
    check("secret configured", h.get("secret_configured"), True)

    # the Ozon poller must not be running under a push provider
    check("ozon watcher not started", main.PAYMENT_WATCHER_TASK.done(), True)

    # 1. unsigned request rejected
    oid = insert_pending_order()
    body = body_for(COMMENT, AMOUNT)
    r = client.post("/payments/webhook", content=body)
    check("unsigned -> 401", r.status_code, 401)
    check("unsigned settles nothing", len(settled), 0)

    # 2. wrong signature rejected
    r = client.post("/payments/webhook", content=body,
                    headers={"X-Signature": sign(b"other body")})
    check("bad sig -> 401", r.status_code, 401)
    check("bad sig settles nothing", len(settled), 0)

    # 3. tampered body rejected (signature valid for the original)
    good_sig = sign(body)
    r = client.post("/payments/webhook", content=body_for(COMMENT, 9999.00),
                    headers={"X-Signature": good_sig})
    check("tampered -> 401", r.status_code, 401)

    # 4. correctly signed request settles the order
    r = client.post("/payments/webhook", content=body, headers={"X-Signature": good_sig})
    check("signed -> 200", r.status_code, 200)
    data = r.json()
    check("received", data.get("received"), 1)
    check("matched", data.get("matched"), 1)
    check("settled once", len(settled), 1)
    check("settled right order", settled[0][0], oid)

    # 5. replaying the same delivery must not double-credit
    r = client.post("/payments/webhook", content=body, headers={"X-Signature": good_sig})
    check("replay -> 200", r.status_code, 200)
    check("replay matched 0", r.json().get("matched"), 0)
    check("replay settles nothing new", len(settled), 1)

    # 6. wrong amount must not settle
    oid2 = insert_pending_order(comment="ARA-CAFEBABE", amount=500.00)
    wrong = body_for("ARA-CAFEBABE", 499.00, txid="tx-2")
    r = client.post("/payments/webhook", content=wrong, headers={"X-Signature": sign(wrong)})
    check("wrong amount matched 0", r.json().get("matched"), 0)

    # 7. right amount, wrong comment must not settle
    wrong2 = body_for("ARA-NOTMINE", 500.00, txid="tx-3")
    r = client.post("/payments/webhook", content=wrong2, headers={"X-Signature": sign(wrong2)})
    check("wrong comment matched 0", r.json().get("matched"), 0)

    # 8. exact match on the second order settles it
    right = body_for("ARA-CAFEBABE", 500.00, txid="tx-4")
    r = client.post("/payments/webhook", content=right, headers={"X-Signature": sign(right)})
    check("second order matched", r.json().get("matched"), 1)
    check("two settled total", len(settled), 2)

    # 9. non-creditable delivery (a debit / fee) answers 200, settles nothing
    empty = json.dumps({"Data": {"Transaction": [
        {"transactionId": "tx-5", "Amount": {"amount": "0.00"},
         "description": "Комиссия банка", "bookingTimestamp": "2026-11-12T10:00:00Z"}]}}).encode()
    r = client.post("/payments/webhook", content=empty, headers={"X-Signature": sign(empty)})
    check("zero-amount -> 200", r.status_code, 200)
    check("zero-amount received 0", r.json().get("received"), 0)

    # 10. malformed JSON with a valid signature -> 400, not 500
    bad = b"{not json"
    r = client.post("/payments/webhook", content=bad, headers={"X-Signature": sign(bad)})
    check("bad json -> 400", r.status_code, 400)

    # 11. DB reflects settlement
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT paid_operation_id FROM orders WHERE id = ?", (oid,)).fetchone()
    check("order 1 has operation id", row["paid_operation_id"], "tx-1")
    row2 = conn.execute("SELECT paid_operation_id FROM orders WHERE id = ?", (oid2,)).fetchone()
    check("order 2 has operation id", row2["paid_operation_id"], "tx-4")
    conn.close()

print(f"ran checks, {len(fails)} failure(s)")
for f in fails:
    print("  FAIL", f)
sys.exit(1 if fails else 0)
