"""Pluggable sources of incoming-payment events.

The settlement logic in main.py is already source-independent: an order gets a
unique `payment_comment` plus an exact `payable_amount`, and
`operation_matches_payment()` pairs it with an incoming operation by
comment + amount + time. Only the *source* of those operations varies.

This module isolates that source behind one interface so the provider becomes
a line in .env instead of a rewrite:

    PAYMENT_PROVIDER=manual        # admin confirms in the bot (default, no deps)
    PAYMENT_PROVIDER=ozon          # existing Ozon scraper (pull)
    PAYMENT_PROVIDER=webhook       # bank or aggregator callback (push)

Every provider yields operations in one canonical shape, identical to what the
Ozon scraper already returns, so the matcher needs no changes:

    {
        "id":            str,            # provider's operation id (idempotency key)
        "time":          str,            # ISO 8601, UTC
        "comment":       str,            # payer's message / purpose
        "purpose":       str,            # same, second field the matcher scans
        "accountAmount": {"cents": int}, # credited amount in kopecks
    }

Pull providers implement `fetch_operations()`; push providers implement
`verify_signature()` + `normalize_event()` and are driven by an HTTP endpoint.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

UTC = timezone.utc

# Field names the matcher in main.py reads. Kept here so a provider can be
# checked against the contract without importing main.
CANONICAL_KEYS = ("id", "time", "comment", "purpose", "accountAmount")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def dotted_get(payload: Any, path: str, default: Any = None) -> Any:
    """Read a nested value by dotted path: "a.b.0.c".

    Bank payloads nest differently (Tochka wraps in `Data.Transaction`, Sber
    flattens), so the field locations are configuration rather than code.
    """
    if not path:
        return default
    current = payload
    for part in str(path).split("."):
        if current is None:
            return default
        if isinstance(current, Mapping):
            if part not in current:
                return default
            current = current[part]
            continue
        if isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
            continue
        return default
    return current if current is not None else default


def coerce_cents(value: Any, unit: str = "rub") -> int:
    """Normalize an amount to whole kopecks.

    `unit="rub"` accepts 1234.56, "1234.56", "1 234,56"; `unit="cents"` accepts
    an already-minor-unit integer. Returns 0 when unparseable, which makes the
    operation fail the matcher rather than match something wrong.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    raw = str(value).strip()
    if not raw:
        return 0
    # Thousands separators and decimal commas show up in bank JSON as strings.
    raw = raw.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        number = float(raw)
    except ValueError:
        return 0
    if unit == "cents":
        return int(round(number))
    # round() before int() so 0.1 + 0.2 style float error cannot drop a kopeck.
    return int(round(number * 100))


_TIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
)


def normalize_time(value: Any) -> str:
    """Return an ISO 8601 UTC string, or now() when the input is unusable.

    Falling back to now() is deliberate: the matcher rejects operations dated
    more than two minutes before the order, so a missing timestamp must not
    look like an ancient payment.
    """
    if value is None or str(value).strip() == "":
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    raw = str(value).strip()

    # Unix epoch, seconds or milliseconds.
    if raw.isdigit():
        number = int(raw)
        if number > 10_000_000_000:  # milliseconds
            number //= 1000
        try:
            return datetime.fromtimestamp(number, UTC).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
        for fmt in _TIME_FORMATS:
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        logger.warning("Could not parse payment time %r, using now()", raw)
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def make_operation(
    *,
    operation_id: Any,
    amount: Any,
    comment: Any = "",
    purpose: Any = "",
    time_value: Any = None,
    amount_unit: str = "rub",
) -> dict:
    """Build one operation in the canonical shape."""
    cents = coerce_cents(amount, amount_unit)
    return {
        "id": str(operation_id or "").strip(),
        "time": normalize_time(time_value),
        "comment": str(comment or ""),
        "purpose": str(purpose or ""),
        "accountAmount": {"cents": cents},
    }


def _decode_signature(raw: str) -> bytes:
    """Accept a signature as hex or base64, whichever the provider sends."""
    candidate = raw.strip()
    if not candidate:
        return b""
    # Strip a "sha256=" style prefix.
    if "=" in candidate and candidate.split("=", 1)[0].lower().startswith("sha"):
        candidate = candidate.split("=", 1)[1].strip()
    try:
        return bytes.fromhex(candidate)
    except ValueError:
        pass
    try:
        return base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError):
        return b""


def verify_hmac(
    body: bytes,
    provided: str | None,
    secret: str,
    *,
    algo: str = "sha256",
) -> bool:
    """Constant-time HMAC check over the exact request body.

    Fails closed: no secret or no signature means no match. A payment webhook
    without this check is an endpoint that credits orders for free.
    """
    if not secret or not provided:
        return False
    try:
        digestmod = getattr(hashlib, algo.lower())
    except AttributeError:
        logger.error("Unknown webhook signature algorithm %r", algo)
        return False
    expected = hmac.new(secret.encode("utf-8"), body, digestmod).digest()
    candidate = _decode_signature(provided)
    if not candidate:
        return False
    return hmac.compare_digest(expected, candidate)


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

class PaymentProvider:
    """Base class. `mode` tells main.py whether to poll or wait for a callback."""

    name = "base"
    mode = "manual"  # "pull" | "push" | "manual"

    async def fetch_operations(self) -> list[dict]:
        """Pull providers override this. Returns canonical operations."""
        return []

    def verify_signature(self, body: bytes, headers: Mapping[str, str]) -> bool:
        """Push providers override this. Must fail closed."""
        return False

    def normalize_event(self, payload: Any) -> list[dict]:
        """Push providers override this. Maps a callback body to operations."""
        return []

    def health(self) -> dict:
        return {"provider": self.name, "mode": self.mode, "enabled": True}

    def describe(self) -> str:
        return f"{self.name} ({self.mode})"


class ManualProvider(PaymentProvider):
    """No automatic settlement; the admin confirms each payment in the bot.

    This is the safe default. It has no credentials, no outbound calls and no
    endpoint, so a misconfigured deployment degrades to manual review rather
    than to an open or broken payment path.
    """

    name = "manual"
    mode = "manual"

    def health(self) -> dict:
        return {
            "provider": self.name,
            "mode": self.mode,
            "enabled": True,
            "note": "Payments are confirmed by the admin; no automatic matching.",
        }


class OzonProvider(PaymentProvider):
    """Existing Ozon Bank scraper, wrapped unchanged.

    The fetch callable is injected so this module never imports main.py.
    Kept for continuity only: it holds a live authenticated bank session and
    breaks on every anti-bot change upstream. Prefer `webhook`.
    """

    name = "ozon"
    mode = "pull"

    def __init__(self, fetcher: Callable[[], Awaitable[list[dict]]], health_source: Mapping | None = None):
        self._fetcher = fetcher
        self._health_source = health_source

    async def fetch_operations(self) -> list[dict]:
        return list(await self._fetcher())

    def health(self) -> dict:
        # The scraper's own health dict carries a "mode" key of its own
        # ("selenium" / "cookies"), so it is merged first and the provider's
        # contract keys are written last. Otherwise /payments/health would
        # report a scraping mode where callers expect pull/push/manual.
        base: dict = {}
        if self._health_source is not None:
            base.update(dict(self._health_source))
            if "mode" in base:
                base["ozon_mode"] = base.pop("mode")
        base.update({"provider": self.name, "mode": self.mode, "enabled": True})
        return base


class WebhookProvider(PaymentProvider):
    """Bank or aggregator callback.

    Field locations and the signature scheme come from .env, so one
    implementation covers Tochka, Sber, Alfa, T-Bank and any aggregator without
    bank-specific code that cannot be tested from here.
    """

    name = "webhook"
    mode = "push"

    def __init__(
        self,
        *,
        secret: str,
        signature_header: str = "X-Signature",
        signature_algo: str = "sha256",
        events_path: str = "",
        field_id: str = "id",
        field_amount: str = "amount",
        field_comment: str = "purpose",
        field_time: str = "time",
        amount_unit: str = "rub",
        label: str = "webhook",
    ):
        self.secret = secret
        self.signature_header = signature_header
        self.signature_algo = signature_algo
        self.events_path = events_path
        self.field_id = field_id
        self.field_amount = field_amount
        self.field_comment = field_comment
        self.field_time = field_time
        self.amount_unit = amount_unit
        self.label = label

    # -- security --------------------------------------------------------

    def verify_signature(self, body: bytes, headers: Mapping[str, str]) -> bool:
        provided = None
        wanted = self.signature_header.lower()
        for key, value in headers.items():
            if key.lower() == wanted:
                provided = value
                break
        return verify_hmac(body, provided, self.secret, algo=self.signature_algo)

    # -- payload mapping -------------------------------------------------

    def _events(self, payload: Any) -> list[Any]:
        """A callback may carry one operation or a batch."""
        if self.events_path:
            found = dotted_get(payload, self.events_path)
            if isinstance(found, Sequence) and not isinstance(found, (str, bytes)):
                return list(found)
            return [found] if found is not None else []
        if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            return list(payload)
        return [payload] if payload is not None else []

    def normalize_event(self, payload: Any) -> list[dict]:
        operations: list[dict] = []
        for event in self._events(payload):
            if not isinstance(event, (Mapping, Sequence)) or isinstance(event, (str, bytes)):
                continue
            amount = dotted_get(event, self.field_amount)
            comment = dotted_get(event, self.field_comment, "")
            operation = make_operation(
                operation_id=dotted_get(event, self.field_id),
                amount=amount,
                comment=comment,
                purpose=comment,
                time_value=dotted_get(event, self.field_time),
                amount_unit=self.amount_unit,
            )
            # An operation without an id cannot be de-duplicated; main.py
            # derives a fallback id from the contents, so keep it.
            if operation["accountAmount"]["cents"] <= 0:
                logger.warning("Webhook operation has no positive amount, skipping")
                continue
            operations.append(operation)
        return operations

    def health(self) -> dict:
        return {
            "provider": self.name,
            "mode": self.mode,
            "enabled": bool(self.secret),
            "label": self.label,
            "signature_header": self.signature_header,
            "signature_algo": self.signature_algo,
            "amount_unit": self.amount_unit,
            "secret_configured": bool(self.secret),
        }

    def describe(self) -> str:
        return f"{self.name}:{self.label} ({self.mode})"


# --------------------------------------------------------------------------
# factory
# --------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return (os.getenv(key, default) or "").strip()


def build_provider(
    *,
    name: str | None = None,
    ozon_fetcher: Callable[[], Awaitable[list[dict]]] | None = None,
    ozon_health: Mapping | None = None,
) -> PaymentProvider:
    """Create a provider by name, defaulting to PAYMENT_PROVIDER from the env.

    `name` lets the caller resolve the default from existing configuration --
    main.py passes "ozon" when the legacy Ozon settings are present -- so
    adding this module changes no behaviour on a deployment that predates it.

    Unknown or unusable configuration falls back to `manual` with a warning,
    never to an unverified automatic path.
    """
    requested = (name or _env("PAYMENT_PROVIDER", "manual")).strip().lower() or "manual"

    if requested in {"manual", "none", "off"}:
        return ManualProvider()

    if requested == "ozon":
        if ozon_fetcher is None:
            logger.error("PAYMENT_PROVIDER=ozon but no fetcher was wired; falling back to manual")
            return ManualProvider()
        return OzonProvider(ozon_fetcher, ozon_health)

    if requested in {"webhook", "bank", "bank_webhook", "aggregator"}:
        secret = _env("PAYMENT_WEBHOOK_SECRET")
        if not secret:
            logger.error(
                "PAYMENT_PROVIDER=%s but PAYMENT_WEBHOOK_SECRET is empty; "
                "falling back to manual rather than accepting unsigned callbacks",
                requested,
            )
            return ManualProvider()
        return WebhookProvider(
            secret=secret,
            signature_header=_env("PAYMENT_WEBHOOK_SIGNATURE_HEADER", "X-Signature"),
            signature_algo=_env("PAYMENT_WEBHOOK_SIGNATURE_ALGO", "sha256"),
            events_path=_env("PAYMENT_WEBHOOK_EVENTS_PATH"),
            field_id=_env("PAYMENT_WEBHOOK_FIELD_ID", "id"),
            field_amount=_env("PAYMENT_WEBHOOK_FIELD_AMOUNT", "amount"),
            field_comment=_env("PAYMENT_WEBHOOK_FIELD_COMMENT", "purpose"),
            field_time=_env("PAYMENT_WEBHOOK_FIELD_TIME", "time"),
            amount_unit=_env("PAYMENT_WEBHOOK_AMOUNT_UNIT", "rub"),
            label=_env("PAYMENT_WEBHOOK_LABEL", requested),
        )

    logger.error("Unknown PAYMENT_PROVIDER=%r; falling back to manual", requested)
    return ManualProvider()
