import argparse
import json
import os
import sys
import time
from http.cookies import SimpleCookie


DEFAULT_PROFILE_DIR = "/root/AraStars/ozon_chrome_profile"


def parse_cookie_input(raw: str) -> dict[str, str]:
    raw = raw.strip()
    if not raw:
        raise RuntimeError("Cookie input is empty")

    if raw.startswith("OZON_COOKIES_JSON="):
        raw = raw.split("=", 1)[1].strip()
    if raw.startswith("Cookie:"):
        raw = raw.split(":", 1)[1].strip()

    if raw.startswith("{"):
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("Cookie JSON must be an object")
        return {str(key): str(value) for key, value in data.items()}

    result = {}
    try:
        cookie = SimpleCookie()
        cookie.load(raw)
        result = {key: morsel.value for key, morsel in cookie.items()}
    except Exception:
        result = {}
    if not result:
        for chunk in raw.split(";"):
            if "=" not in chunk:
                continue
            name, value = chunk.split("=", 1)
            name = name.strip()
            if name:
                result[name] = value.strip()
    if not result:
        raise RuntimeError("Could not parse Cookie header")
    return result


def build_driver(profile_dir: str, headless: bool, timeout: int):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    os.makedirs(profile_dir, mode=0o700, exist_ok=True)
    os.chmod(profile_dir, 0o700)

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if headless:
        options.add_argument("--headless=new")

    service = Service("/usr/bin/chromedriver") if os.path.exists("/usr/bin/chromedriver") else None
    driver = webdriver.Chrome(service=service, options=options) if service else webdriver.Chrome(options=options)
    driver.set_page_load_timeout(timeout)
    driver.set_script_timeout(timeout)
    return driver


def add_cookie(driver, name: str, value: str) -> bool:
    base = {
        "name": name,
        "value": value,
        "path": "/",
        "secure": True,
        "expiry": int(time.time()) + 7 * 24 * 60 * 60,
    }
    for domain in (".ozon.ru", "finance.ozon.ru", None):
        cookie = dict(base)
        if domain:
            cookie["domain"] = domain
        try:
            driver.add_cookie(cookie)
            return True
        except Exception:
            continue
    return False


def fetch_operations(driver, per_page: int) -> tuple[int, int, str]:
    payload = {
        "filter": {"categories": [], "effect": "EFFECT_UNKNOWN"},
        "cursorPagination": {"cursor": None, "perPage": per_page},
    }
    result = driver.execute_async_script(
        """
        const payload = arguments[0];
        const done = arguments[arguments.length - 1];
        fetch('/api/v4/operations/list', {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify(payload)
        })
            .then(async (response) => {
                const text = await response.text();
                done({ok: response.ok, status: response.status, url: response.url, text});
            })
            .catch((error) => done({ok: false, status: 0, url: '', text: String(error)}));
        """,
        payload,
    )
    status = int(result.get("status") or 0)
    text = str(result.get("text") or "")
    if not result.get("ok"):
        return status, 0, text[:500]
    data = json.loads(text)
    items = (
        data.get("data", {})
        .get("me", {})
        .get("client", {})
        .get("groupOperationsV2", {})
        .get("items", [])
    )
    return status, len(items), ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Ozon Bank cookies into a Selenium Chrome profile.")
    parser.add_argument("--profile-dir", default=os.getenv("OZON_CHROME_PROFILE_DIR", DEFAULT_PROFILE_DIR))
    parser.add_argument("--per-page", type=int, default=int(os.getenv("OZON_OPERATIONS_PER_PAGE", "30")))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--headed", action="store_true", help="Run Chrome with a visible window.")
    args = parser.parse_args()

    if sys.stdin.isatty():
        raw = input("Paste Ozon Cookie header or JSON: ").strip()
    else:
        raw = sys.stdin.read().strip()
    raw = raw or os.getenv("OZON_COOKIES_JSON", "")
    cookies = parse_cookie_input(raw)

    driver = build_driver(args.profile_dir, headless=not args.headed, timeout=args.timeout)
    try:
        driver.get("https://finance.ozon.ru/")
        driver.delete_all_cookies()
        added = 0
        for name, value in cookies.items():
            if add_cookie(driver, name, value):
                added += 1

        driver.get("https://finance.ozon.ru/lk/operations")
        status, count, error = fetch_operations(driver, args.per_page)
        print(f"imported_cookies={added}/{len(cookies)}")
        print(f"operations_status={status}")
        print(f"operations_count={count}")
        if error:
            print(f"operations_error={error}")
            return 2
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
