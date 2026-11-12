import hashlib, hmac, json, os, sys, base64
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import payment_providers as pp

fails = []
def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")

# ---- dotted_get ----
payload = {"Data": {"Transaction": [{"amount": "100.50", "purpose": "ARA-AB12CD34"}]}}
check("dotted nested list", pp.dotted_get(payload, "Data.Transaction.0.purpose"), "ARA-AB12CD34")
check("dotted missing", pp.dotted_get(payload, "Data.Nope.x", "dflt"), "dflt")
check("dotted empty path", pp.dotted_get(payload, "", "dflt"), "dflt")

# ---- coerce_cents ----
check("cents from float", pp.coerce_cents(1234.56), 123456)
check("cents from str comma", pp.coerce_cents("1 234,56"), 123456)
check("cents from nbsp", pp.coerce_cents("1 234.56"), 123456)
check("cents unit=cents", pp.coerce_cents(123456, "cents"), 123456)
check("cents garbage", pp.coerce_cents("abc"), 0)
check("cents None", pp.coerce_cents(None), 0)
check("cents bool", pp.coerce_cents(True), 0)
# float-error trap: 0.1+0.2 == 0.30000000000000004
check("cents float error", pp.coerce_cents(0.1 + 0.2), 30)
check("cents 19.99", pp.coerce_cents(19.99), 1999)

# ---- normalize_time ----
check("time Z", pp.normalize_time("2026-11-12T10:30:00Z"), "2026-11-12T10:30:00+00:00".replace("+00:00","Z"))
check("time offset->utc", pp.normalize_time("2026-11-12T13:30:00+03:00"), "2026-11-12T10:30:00Z")
check("time ru format", pp.normalize_time("12.11.2026 13:30:00"), "2026-11-12T13:30:00Z")
check("time epoch s", pp.normalize_time("1763000000"), "2025-11-13T02:13:20Z")
assert pp.normalize_time(None).endswith("Z"), "None time must still yield ISO Z"
assert pp.normalize_time("nonsense").endswith("Z"), "bad time must fall back to now"

# ---- verify_hmac ----
body = b'{"amount":"100.50"}'
secret = "s3cret"
hexsig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
b64sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
check("hmac hex", pp.verify_hmac(body, hexsig, secret), True)
check("hmac b64", pp.verify_hmac(body, b64sig, secret), True)
check("hmac prefixed", pp.verify_hmac(body, "sha256=" + hexsig, secret), True)
check("hmac wrong sig", pp.verify_hmac(body, "deadbeef", secret), False)
check("hmac no secret", pp.verify_hmac(body, hexsig, ""), False)
check("hmac no sig", pp.verify_hmac(body, None, secret), False)
check("hmac tampered body", pp.verify_hmac(b'{"amount":"999"}', hexsig, secret), False)
check("hmac bad algo", pp.verify_hmac(body, hexsig, secret, algo="nope"), False)

# ---- WebhookProvider: flat payload ----
flat = pp.WebhookProvider(secret=secret, field_id="id", field_amount="amount",
                          field_comment="purpose", field_time="time")
ops = flat.normalize_event({"id": "op1", "amount": "100.50", "purpose": "ARA-AB12CD34",
                            "time": "2026-11-12T10:30:00Z"})
check("flat count", len(ops), 1)
check("flat cents", ops[0]["accountAmount"]["cents"], 10050)
check("flat comment", ops[0]["comment"], "ARA-AB12CD34")
check("flat purpose mirrors comment", ops[0]["purpose"], "ARA-AB12CD34")
check("flat id", ops[0]["id"], "op1")
check("flat canonical keys", sorted(ops[0].keys()), sorted(pp.CANONICAL_KEYS))

# ---- WebhookProvider: nested batch (Tochka-ish) ----
nested = pp.WebhookProvider(secret=secret, events_path="Data.Transaction",
                            field_id="transactionId", field_amount="Amount.amount",
                            field_comment="description", field_time="bookingTimestamp")
ops = nested.normalize_event({"Data": {"Transaction": [
    {"transactionId": "t1", "Amount": {"amount": "500.00"}, "description": "ARA-11112222",
     "bookingTimestamp": "2026-11-12T10:30:00+03:00"},
    {"transactionId": "t2", "Amount": {"amount": "250.00"}, "description": "ARA-33334444",
     "bookingTimestamp": "2026-11-12T11:00:00+03:00"},
]}})
check("nested count", len(ops), 2)
check("nested cents", ops[0]["accountAmount"]["cents"], 50000)
check("nested tz converted", ops[0]["time"], "2026-11-12T07:30:00Z")
check("nested second id", ops[1]["id"], "t2")

# ---- WebhookProvider: top-level list ----
aslist = pp.WebhookProvider(secret=secret)
ops = aslist.normalize_event([{"id": "a", "amount": 10, "purpose": "X"},
                              {"id": "b", "amount": 20, "purpose": "Y"}])
check("list count", len(ops), 2)

# ---- WebhookProvider: zero/negative amounts dropped ----
ops = flat.normalize_event({"id": "z", "amount": "0", "purpose": "X"})
check("zero amount dropped", len(ops), 0)
ops = flat.normalize_event({"id": "n", "amount": "-5", "purpose": "X"})
check("negative amount dropped", len(ops), 0)
ops = flat.normalize_event({"id": "g", "amount": "garbage", "purpose": "X"})
check("garbage amount dropped", len(ops), 0)

# ---- WebhookProvider: header lookup is case-insensitive ----
check("sig header lower", flat.verify_signature(body, {"x-signature": hexsig}), True)
check("sig header mixed", flat.verify_signature(body, {"X-Signature": hexsig}), True)
check("sig header absent", flat.verify_signature(body, {"other": hexsig}), False)

# ---- cents unit ----
centsprov = pp.WebhookProvider(secret=secret, amount_unit="cents")
ops = centsprov.normalize_event({"id": "c", "amount": 10050, "purpose": "X"})
check("unit cents", ops[0]["accountAmount"]["cents"], 10050)

# ---- factory ----
for key in list(os.environ):
    if key.startswith("PAYMENT_"):
        del os.environ[key]
check("default manual", pp.build_provider().name, "manual")
os.environ["PAYMENT_PROVIDER"] = "webhook"
check("webhook without secret -> manual", pp.build_provider().name, "manual")
os.environ["PAYMENT_WEBHOOK_SECRET"] = secret
p = pp.build_provider()
check("webhook with secret", p.name, "webhook")
check("webhook mode", p.mode, "push")
os.environ["PAYMENT_PROVIDER"] = "bogus"
check("bogus -> manual", pp.build_provider().name, "manual")
os.environ["PAYMENT_PROVIDER"] = "ozon"
check("ozon without fetcher -> manual", pp.build_provider().name, "manual")

async def fake_fetch():
    return [{"id": "o1"}]
p = pp.build_provider(ozon_fetcher=fake_fetch, ozon_health={"last_error": None})
check("ozon with fetcher", p.name, "ozon")
check("ozon mode", p.mode, "pull")
check("ozon health merged", p.health().get("last_error"), None)

import asyncio
check("ozon fetch passthrough", asyncio.run(p.fetch_operations()), [{"id": "o1"}])

# ---- manual provider yields nothing and verifies nothing ----
m = pp.ManualProvider()
check("manual fetch empty", asyncio.run(m.fetch_operations()), [])
check("manual verify false", m.verify_signature(b"x", {"X-Signature": hexsig}), False)
check("manual normalize empty", m.normalize_event({"amount": 100}), [])

print(f"ran checks, {len(fails)} failure(s)")
for f in fails:
    print("  FAIL", f)
sys.exit(1 if fails else 0)
