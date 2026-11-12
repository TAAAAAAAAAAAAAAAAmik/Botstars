"""The provider layer must change nothing on an .env that predates it."""
import importlib, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
fails = []
def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")

def load_main(env):
    for key in list(os.environ):
        if key.startswith(("PAYMENT_", "OZON_", "ORDERS_DB")):
            del os.environ[key]
    os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
    os.environ.update(env)
    for mod in ("main", "payment_providers"):
        sys.modules.pop(mod, None)
    return importlib.import_module("main")

from fastapi.testclient import TestClient

# ---- Case A: legacy .env with Ozon cookies, no PAYMENT_PROVIDER ----
m = load_main({"OZON_COOKIES_JSON": '{"a":"b"}'})
check("A resolved name", m.PAYMENT_PROVIDER_NAME, "ozon")
check("A ozon enabled", m.OZON_ENABLED, True)
with TestClient(m.app) as c:
    h = c.get("/payments/health").json()
    check("A provider", h.get("provider"), "ozon")
    check("A mode is pull", h.get("mode"), "pull")
    # watcher must be alive, i.e. still polling as before
    check("A watcher running", m.PAYMENT_WATCHER_TASK.done(), False)
    # webhook endpoint must be closed under a pull provider
    check("A webhook 404", c.post("/payments/webhook", content=b"{}").status_code, 404)

# ---- Case B: legacy .env with Selenium, no PAYMENT_PROVIDER ----
m = load_main({"OZON_USE_SELENIUM": "true"})
check("B resolved name", m.PAYMENT_PROVIDER_NAME, "ozon")
with TestClient(m.app) as c:
    check("B mode is pull", c.get("/payments/health").json().get("mode"), "pull")
    check("B watcher running", m.PAYMENT_WATCHER_TASK.done(), False)

# ---- Case C: no Ozon config at all, no PAYMENT_PROVIDER -> manual ----
m = load_main({})
check("C resolved name", m.PAYMENT_PROVIDER_NAME, "manual")
with TestClient(m.app) as c:
    h = c.get("/payments/health").json()
    check("C provider", h.get("provider"), "manual")
    check("C mode", h.get("mode"), "manual")
    check("C watcher stopped", m.PAYMENT_WATCHER_TASK.done(), True)
    check("C webhook 404", c.post("/payments/webhook", content=b"{}").status_code, 404)

# ---- Case D: explicit override wins over Ozon config being present ----
m = load_main({"OZON_COOKIES_JSON": '{"a":"b"}', "PAYMENT_PROVIDER": "manual"})
check("D resolved name", m.PAYMENT_PROVIDER_NAME, "manual")
with TestClient(m.app) as c:
    check("D provider", c.get("/payments/health").json().get("provider"), "manual")
    check("D watcher stopped", m.PAYMENT_WATCHER_TASK.done(), True)

# ---- Case E: webhook requested but no secret -> manual, never unsigned ----
m = load_main({"PAYMENT_PROVIDER": "webhook"})
with TestClient(m.app) as c:
    check("E falls back to manual", c.get("/payments/health").json().get("provider"), "manual")
    check("E webhook closed", c.post("/payments/webhook", content=b"{}").status_code, 404)

# ---- Case F: legacy ozon endpoints still present ----
m = load_main({"OZON_COOKIES_JSON": '{"a":"b"}'})
with TestClient(m.app) as c:
    check("F ozon health still 200", c.get("/payments/ozon/health").status_code, 200)

print(f"ran checks, {len(fails)} failure(s)")
for f in fails:
    print("  FAIL", f)
sys.exit(1 if fails else 0)
