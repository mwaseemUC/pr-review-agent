"""Hand-written eval cases: 20 planted-bug diffs and 5 clean diffs.

Each bug is located by an anchor substring that must occur exactly once in the "after" source;
the line number is derived from it so labels cannot drift when the code is edited.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FileChange:
    before: str
    after: str


@dataclass(frozen=True)
class PlantedBug:
    file: str
    line: int
    category: str
    explanation: str


@dataclass(frozen=True)
class Case:
    id: str
    description: str
    files: dict[str, FileChange]
    bugs: list[PlantedBug] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "bug" if self.bugs else "clean"

    @property
    def language(self) -> str:
        suffixes = {path.rsplit(".", 1)[-1] for path in self.files}
        if suffixes & {"ts", "tsx"}:
            return "typescript"
        if suffixes & {"js", "jsx"}:
            return "javascript"
        return "python"


def _bug(
    files: dict[str, FileChange], path: str, anchor: str, category: str, explanation: str
) -> PlantedBug:
    lines = files[path].after.splitlines()
    hits = [number for number, text in enumerate(lines, start=1) if anchor in text]
    if len(hits) != 1:
        raise ValueError(f"anchor {anchor!r} matched {len(hits)} lines in {path}")
    return PlantedBug(file=path, line=hits[0], category=category, explanation=explanation)


def _case(
    case_id: str,
    description: str,
    files: dict[str, FileChange],
    bug: tuple[str, str, str, str] | None = None,
) -> Case:
    bugs = [_bug(files, *bug)] if bug else []
    return Case(id=case_id, description=description, files=files, bugs=bugs)


# --------------------------------------------------------------------------------------------
# 01 off-by-one
# --------------------------------------------------------------------------------------------

_STATS_BEFORE = r"""from __future__ import annotations

from collections.abc import Sequence


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean() of empty sequence")
    return sum(values) / len(values)


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        raise ValueError("percentile() of empty sequence")
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)
"""

_STATS_AFTER = (
    _STATS_BEFORE
    + r'''

def rolling_mean(series: Sequence[float], window: int) -> list[float]:
    """Mean of every consecutive `window`-sized slice of `series`, oldest first."""
    if window <= 0:
        raise ValueError("window must be positive")
    if window > len(series):
        return []
    return [mean(series[i : i + window]) for i in range(len(series) - window)]


def summarize_latencies(samples_ms: Sequence[float], window: int = 5) -> dict[str, object]:
    if not samples_ms:
        return {"count": 0, "mean": None, "p95": None, "rolling": []}
    return {
        "count": len(samples_ms),
        "mean": round(mean(samples_ms), 2),
        "p95": round(percentile(samples_ms, 95), 2),
        "rolling": [round(value, 2) for value in rolling_mean(samples_ms, window)],
    }
'''
)

_FILES_01 = {"app/metrics/stats.py": FileChange(_STATS_BEFORE, _STATS_AFTER)}

# --------------------------------------------------------------------------------------------
# 02 sql-injection
# --------------------------------------------------------------------------------------------

_USERS_REPO_BEFORE = r"""from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str
    is_active: bool


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_active=bool(row["is_active"]),
    )


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute(
        "SELECT id, email, name, is_active FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return _row_to_user(row) if row else None


def deactivate_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
    conn.commit()
"""

_USERS_REPO_AFTER = (
    _USERS_REPO_BEFORE
    + r"""

def search_users(
    conn: sqlite3.Connection, query: str, *, active_only: bool = True, limit: int = 50
) -> list[User]:
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(limit, 200))
    clauses = [f"(name LIKE '%{query}%' OR email LIKE '%{query}%')"]
    if active_only:
        clauses.append("is_active = 1")
    sql = (
        "SELECT id, email, name, is_active FROM users WHERE "
        + " AND ".join(clauses)
        + " ORDER BY name LIMIT ?"
    )
    rows = conn.execute(sql, (limit,)).fetchall()
    return [_row_to_user(row) for row in rows]
"""
)

_FILES_02 = {"app/repositories/users.py": FileChange(_USERS_REPO_BEFORE, _USERS_REPO_AFTER)}

# --------------------------------------------------------------------------------------------
# 03 null-check (TypeScript)
# --------------------------------------------------------------------------------------------

_DISPLAY_BEFORE = r"""export interface Profile {
  nickname?: string;
  avatarUrl?: string;
  timezone: string;
}

export interface User {
  id: string;
  email: string;
  profile?: Profile;
  createdAt: string;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}
"""

_DISPLAY_AFTER = (
    _DISPLAY_BEFORE
    + r"""
export interface UserCard {
  id: string;
  label: string;
  initials: string;
  avatarUrl: string | null;
}

export function displayName(user: User): string {
  const nickname = user.profile.nickname?.trim();
  if (nickname) {
    return nickname;
  }
  return user.email.split("@")[0];
}

export function toUserCard(user: User): UserCard {
  const label = displayName(user);
  return {
    id: user.id,
    label,
    initials: initials(label),
    avatarUrl: user.profile?.avatarUrl ?? null,
  };
}

export function toUserCards(users: User[]): UserCard[] {
  return users
    .map(toUserCard)
    .sort((a, b) => a.label.localeCompare(b.label));
}
"""
)

_FILES_03 = {"src/users/display.ts": FileChange(_DISPLAY_BEFORE, _DISPLAY_AFTER)}

# --------------------------------------------------------------------------------------------
# 04 race-condition
# --------------------------------------------------------------------------------------------

_INVENTORY_BEFORE = r'''from __future__ import annotations

import threading
from collections import defaultdict


class OutOfStock(Exception):
    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(f"{sku}: requested {requested}, available {available}")
        self.sku = sku
        self.requested = requested
        self.available = available


class InventoryStore:
    """In-process stock levels shared by the API worker threads."""

    def __init__(self) -> None:
        self._stock: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def restock(self, sku: str, quantity: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        with self._lock:
            self._stock[sku] += quantity

    def available(self, sku: str) -> int:
        with self._lock:
            return self._stock[sku]
'''

_INVENTORY_AFTER = (
    _INVENTORY_BEFORE
    + r"""
    def reserve(self, sku: str, quantity: int) -> int:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        current = self.available(sku)
        if current < quantity:
            raise OutOfStock(sku, quantity, current)
        with self._lock:
            self._stock[sku] = current - quantity
            return self._stock[sku]

    def reserve_many(self, items: dict[str, int]) -> None:
        reserved: list[tuple[str, int]] = []
        try:
            for sku, quantity in items.items():
                self.reserve(sku, quantity)
                reserved.append((sku, quantity))
        except Exception:
            for sku, quantity in reserved:
                self.restock(sku, quantity)
            raise
"""
)

_FILES_04 = {"app/inventory/store.py": FileChange(_INVENTORY_BEFORE, _INVENTORY_AFTER)}

# --------------------------------------------------------------------------------------------
# 05 path-traversal
# --------------------------------------------------------------------------------------------

_ATTACH_BEFORE = r"""from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify

bp = Blueprint("attachments", __name__, url_prefix="/attachments")


def _storage_root() -> Path:
    return Path(current_app.config["ATTACHMENT_ROOT"])


@bp.get("/")
def list_attachments():
    root = _storage_root()
    entries = [
        {"name": p.name, "size": p.stat().st_size}
        for p in sorted(root.iterdir())
        if p.is_file()
    ]
    return jsonify(entries)
"""

_ATTACH_AFTER = r"""from __future__ import annotations

import mimetypes
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, send_file

bp = Blueprint("attachments", __name__, url_prefix="/attachments")


def _storage_root() -> Path:
    return Path(current_app.config["ATTACHMENT_ROOT"])


@bp.get("/")
def list_attachments():
    root = _storage_root()
    entries = [
        {"name": p.name, "size": p.stat().st_size}
        for p in sorted(root.iterdir())
        if p.is_file()
    ]
    return jsonify(entries)


@bp.get("/usage")
def storage_usage():
    files = [p for p in _storage_root().iterdir() if p.is_file()]
    return jsonify({"files": len(files), "bytes": sum(p.stat().st_size for p in files)})


@bp.get("/<path:filename>")
def download_attachment(filename: str):
    target = _storage_root() / filename
    if not target.is_file():
        abort(404)
    mimetype, _ = mimetypes.guess_type(target.name)
    return send_file(
        target,
        mimetype=mimetype or "application/octet-stream",
        as_attachment=True,
        download_name=target.name,
    )
"""

_FILES_05 = {"app/api/attachments.py": FileChange(_ATTACH_BEFORE, _ATTACH_AFTER)}

# --------------------------------------------------------------------------------------------
# 06 resource-leak
# --------------------------------------------------------------------------------------------

_REPORTS_REPO_BEFORE = r"""from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg2.pool import SimpleConnectionPool

_ORDER_COUNT_SQL = "SELECT count(*) FROM orders WHERE created_at::date = %s"


@dataclass(frozen=True)
class DailyTotal:
    day: date
    orders: int
    revenue_cents: int


class ReportRepository:
    def __init__(self, pool: SimpleConnectionPool) -> None:
        self._pool = pool

    def order_count(self, day: date) -> int:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_ORDER_COUNT_SQL, (day,))
                return cur.fetchone()[0]
        finally:
            self._pool.putconn(conn)
"""

_REPORTS_REPO_AFTER = r"""from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from psycopg2.extensions import connection
from psycopg2.pool import SimpleConnectionPool

_ORDER_COUNT_SQL = "SELECT count(*) FROM orders WHERE created_at::date = %s"

_DAILY_TOTALS_SQL = (
    "SELECT created_at::date AS day, count(*), coalesce(sum(total_cents), 0) "
    "FROM orders WHERE created_at::date BETWEEN %s AND %s "
    "GROUP BY day ORDER BY day"
)


@dataclass(frozen=True)
class DailyTotal:
    day: date
    orders: int
    revenue_cents: int


class ReportRepository:
    def __init__(self, pool: SimpleConnectionPool) -> None:
        self._pool = pool

    def order_count(self, day: date) -> int:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(_ORDER_COUNT_SQL, (day,))
                return cur.fetchone()[0]
        finally:
            self._pool.putconn(conn)

    def daily_totals(self, start: date, end: date) -> list[DailyTotal]:
        if start > end:
            raise ValueError("start must be on or before end")
        conn = self._pool.getconn()
        rows = self._fetch_daily_rows(conn, start, end)
        if not rows:
            return []
        self._pool.putconn(conn)
        return [DailyTotal(day=r[0], orders=r[1], revenue_cents=r[2]) for r in rows]

    @staticmethod
    def _fetch_daily_rows(conn: connection, start: date, end: date) -> list[tuple]:
        with conn.cursor() as cur:
            cur.execute(_DAILY_TOTALS_SQL, (start, end))
            return cur.fetchall()
"""

_FILES_06 = {"app/reports/repository.py": FileChange(_REPORTS_REPO_BEFORE, _REPORTS_REPO_AFTER)}

# --------------------------------------------------------------------------------------------
# 07 mutable-default
# --------------------------------------------------------------------------------------------

_BILLING_CLIENT_AFTER = r"""from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


def build_headers(token: str, extra: dict[str, str] = {}) -> dict[str, str]:
    extra["Authorization"] = f"Bearer {token}"
    extra.setdefault("Accept", "application/json")
    return extra


class BillingClient:
    def __init__(self, base_url: str, token: str, *, timeout: httpx.Timeout = DEFAULT_TIMEOUT):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._token = token

    def get_invoice(self, invoice_id: str) -> dict:
        response = self._client.get(
            f"/v1/invoices/{invoice_id}", headers=build_headers(self._token)
        )
        response.raise_for_status()
        return response.json()

    def list_invoices(self, customer_id: str, *, limit: int = 20) -> list[dict]:
        response = self._client.get(
            "/v1/invoices",
            params={"customer": customer_id, "limit": limit},
            headers=build_headers(self._token),
        )
        response.raise_for_status()
        return response.json()["data"]

    def create_refund(self, invoice_id: str, amount_cents: int, idempotency_key: str) -> dict:
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
        response = self._client.post(
            f"/v1/invoices/{invoice_id}/refunds",
            json={"amount": amount_cents},
            headers=build_headers(self._token, {"Idempotency-Key": idempotency_key}),
        )
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()
"""

_FILES_07 = {"app/clients/billing.py": FileChange("", _BILLING_CLIENT_AFTER)}

# --------------------------------------------------------------------------------------------
# 08 swallowed-exception
# --------------------------------------------------------------------------------------------

_CAPTURE_BEFORE = r"""from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

logger = logging.getLogger(__name__)

AUTHORIZATION_TTL = timedelta(days=7)


@dataclass(frozen=True)
class Order:
    id: str
    payment_intent_id: str
    total_cents: int
    authorized_at: datetime


class OrderRepository(Protocol):
    def authorized_before(self, cutoff: datetime) -> list[Order]: ...
    def pending_capture(self) -> list[Order]: ...
    def mark_paid(self, order_id: str) -> None: ...
    def mark_expired(self, order_id: str) -> None: ...


class PaymentGateway(Protocol):
    def capture(self, payment_intent_id: str, amount_cents: int) -> None: ...
    def cancel(self, payment_intent_id: str) -> None: ...


def expire_stale_authorizations(orders: OrderRepository, gateway: PaymentGateway) -> int:
    cutoff = datetime.now(timezone.utc) - AUTHORIZATION_TTL
    expired = 0
    for order in orders.authorized_before(cutoff):
        gateway.cancel(order.payment_intent_id)
        orders.mark_expired(order.id)
        expired += 1
    logger.info("expired %d stale authorizations", expired)
    return expired
"""

_CAPTURE_AFTER = (
    _CAPTURE_BEFORE
    + r"""

def capture_pending(orders: OrderRepository, gateway: PaymentGateway) -> int:
    captured = 0
    for order in orders.pending_capture():
        try:
            gateway.capture(order.payment_intent_id, order.total_cents)
        except Exception:
            logger.warning("capture failed for order %s", order.id)
        orders.mark_paid(order.id)
        captured += 1
    logger.info("captured %d orders", captured)
    return captured
"""
)

_FILES_08 = {"app/workers/payments.py": FileChange(_CAPTURE_BEFORE, _CAPTURE_AFTER)}

# --------------------------------------------------------------------------------------------
# 09 wrong-operator (JavaScript)
# --------------------------------------------------------------------------------------------

_AUTH_MW_BEFORE = r"""const jwt = require("jsonwebtoken");

const ROLE_RANK = { viewer: 0, editor: 1, admin: 2 };

function requireAuth(secret) {
  return function authenticate(req, res, next) {
    const header = req.headers.authorization || "";
    const [scheme, token] = header.split(" ");
    if (scheme !== "Bearer" || !token) {
      return res.status(401).json({ error: "authentication required" });
    }
    try {
      req.user = jwt.verify(token, secret);
    } catch (err) {
      return res.status(401).json({ error: "invalid token" });
    }
    return next();
  };
}

module.exports = { requireAuth, ROLE_RANK };
"""

_AUTH_MW_AFTER = r"""const jwt = require("jsonwebtoken");

const ROLE_RANK = { viewer: 0, editor: 1, admin: 2 };

function requireAuth(secret) {
  return function authenticate(req, res, next) {
    const header = req.headers.authorization || "";
    const [scheme, token] = header.split(" ");
    if (scheme !== "Bearer" || !token) {
      return res.status(401).json({ error: "authentication required" });
    }
    try {
      req.user = jwt.verify(token, secret);
    } catch (err) {
      return res.status(401).json({ error: "invalid token" });
    }
    return next();
  };
}

function requireRole(minimum) {
  if (!(minimum in ROLE_RANK)) {
    throw new Error(`unknown role: ${minimum}`);
  }
  return function checkRole(req, res, next) {
    const user = req.user;
    if (!user) {
      return res.status(401).json({ error: "authentication required" });
    }
    if (user.role = "owner") {
      return next();
    }
    const rank = ROLE_RANK[user.role] ?? -1;
    if (rank < ROLE_RANK[minimum]) {
      return res.status(403).json({ error: "insufficient role" });
    }
    return next();
  };
}

module.exports = { requireAuth, requireRole, ROLE_RANK };
"""

_FILES_09 = {"src/middleware/auth.js": FileChange(_AUTH_MW_BEFORE, _AUTH_MW_AFTER)}

# --------------------------------------------------------------------------------------------
# 10 integer-division
# --------------------------------------------------------------------------------------------

_PROGRESS_AFTER = r"""from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class JobProgress:
    job_id: str
    total_items: int
    processed_items: int = 0
    failed_items: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def record(self, *, ok: bool) -> None:
        if self.processed_items >= self.total_items:
            raise ValueError(f"job {self.job_id} already processed all {self.total_items} items")
        self.processed_items += 1
        if not ok:
            self.failed_items += 1

    @property
    def done(self) -> bool:
        return self.processed_items >= self.total_items

    @property
    def percent_complete(self) -> int:
        if self.total_items == 0:
            return 100
        return self.processed_items // self.total_items * 100

    def to_status(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "state": "done" if self.done else "running",
            "percent": self.percent_complete,
            "processed": self.processed_items,
            "failed": self.failed_items,
            "started_at": self.started_at.isoformat(),
        }
"""

_FILES_10 = {"app/jobs/progress.py": FileChange("", _PROGRESS_AFTER)}

# --------------------------------------------------------------------------------------------
# 11 naive-datetime
# --------------------------------------------------------------------------------------------

_SUBS_BEFORE = r'''from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TRIAL_LENGTH = timedelta(days=14)


@dataclass
class Subscription:
    id: str
    plan: str
    started_at: datetime
    expires_at: datetime


def start_trial(subscription_id: str, plan: str) -> Subscription:
    now = datetime.now(timezone.utc)
    return Subscription(
        id=subscription_id, plan=plan, started_at=now, expires_at=now + TRIAL_LENGTH
    )


def from_row(row: dict[str, str]) -> Subscription:
    """Rows store ISO-8601 timestamps with an explicit +00:00 offset."""
    return Subscription(
        id=row["id"],
        plan=row["plan"],
        started_at=datetime.fromisoformat(row["started_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )
'''

_SUBS_AFTER = r'''from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TRIAL_LENGTH = timedelta(days=14)
GRACE_PERIOD = timedelta(days=3)
BILLING_PERIOD = timedelta(days=30)


@dataclass
class Subscription:
    id: str
    plan: str
    started_at: datetime
    expires_at: datetime


def start_trial(subscription_id: str, plan: str) -> Subscription:
    now = datetime.now(timezone.utc)
    return Subscription(
        id=subscription_id, plan=plan, started_at=now, expires_at=now + TRIAL_LENGTH
    )


def from_row(row: dict[str, str]) -> Subscription:
    """Rows store ISO-8601 timestamps with an explicit +00:00 offset."""
    return Subscription(
        id=row["id"],
        plan=row["plan"],
        started_at=datetime.fromisoformat(row["started_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )


def is_active(subscription: Subscription) -> bool:
    return datetime.now() < subscription.expires_at + GRACE_PERIOD


def days_remaining(subscription: Subscription, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return max(0, (subscription.expires_at - now).days)


def renew(subscription: Subscription, periods: int = 1) -> Subscription:
    if periods < 1:
        raise ValueError("periods must be at least 1")
    base = max(subscription.expires_at, datetime.now(timezone.utc))
    return Subscription(
        id=subscription.id,
        plan=subscription.plan,
        started_at=subscription.started_at,
        expires_at=base + BILLING_PERIOD * periods,
    )
'''

_FILES_11 = {"app/billing/subscriptions.py": FileChange(_SUBS_BEFORE, _SUBS_AFTER)}

# --------------------------------------------------------------------------------------------
# 12 hardcoded-secret (the key is fake and matches no real provider format)
# --------------------------------------------------------------------------------------------

_MAILER_AFTER = r"""from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

MAIL_API_BASE = os.environ.get("MAIL_API_BASE", "https://api.mailrelay.example/v3")
MAIL_API_KEY = os.environ.get("MAIL_API_KEY", "mr_live_9f8e7d6c5b4a39281706f5e4d3c2b1a0")
DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "no-reply@example.com")


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str | None = None


def send_message(message: Message, *, client: httpx.Client | None = None) -> str:
    payload: dict[str, object] = {
        "from": DEFAULT_SENDER,
        "to": [message.to],
        "subject": message.subject,
        "text": message.text,
    }
    if message.html is not None:
        payload["html"] = message.html
    http = client or httpx.Client(base_url=MAIL_API_BASE, timeout=10.0)
    try:
        response = http.post(
            "/messages",
            json=payload,
            headers={"Authorization": f"Bearer {MAIL_API_KEY}"},
        )
        response.raise_for_status()
    finally:
        if client is None:
            http.close()
    message_id = response.json()["id"]
    logger.info("queued message %s", message_id)
    return message_id
"""

_FILES_12 = {"app/integrations/mailer.py": FileChange("", _MAILER_AFTER)}

# --------------------------------------------------------------------------------------------
# 13 unchecked-return
# --------------------------------------------------------------------------------------------

_BACKUP_AFTER = r'''from __future__ import annotations

import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)


def upload_file(path: Path, bucket: str, key: str) -> None:
    boto3.client("s3").upload_file(str(path), bucket, key)


def create_backup(db_name: str, bucket: str, *, now: datetime | None = None) -> str:
    """Dump `db_name` with pg_dump (connection settings come from PG* env vars) and upload it."""
    now = now or datetime.now(timezone.utc)
    key = f"postgres/{db_name}/{now:%Y/%m/%d}/{now:%H%M%S}.dump"
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "backup.dump"
        command = ["pg_dump", "--format=custom", "--file", str(dump_path), db_name]
        subprocess.run(command, capture_output=True, text=True)
        upload_file(dump_path, bucket, key)
    logger.info("uploaded backup of %s to s3://%s/%s", db_name, bucket, key)
    return key
'''

_FILES_13 = {"app/ops/backup.py": FileChange("", _BACKUP_AFTER)}

# --------------------------------------------------------------------------------------------
# 14 closure-late-binding
# --------------------------------------------------------------------------------------------

_EVENTS_BEFORE = r"""from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        for handler in self._handlers.get(event, []):
            try:
                handler(payload)
            except Exception:
                logger.exception("handler for %s failed", event)
"""

_EVENTS_AFTER = r"""from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]

AUDITED_EVENTS = ("user.created", "user.deleted", "role.granted", "role.revoked")


class AuditSink(Protocol):
    def record(self, event: str, payload: dict[str, Any]) -> None: ...


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._handlers[event].append(handler)

    def publish(self, event: str, payload: dict[str, Any]) -> None:
        for handler in self._handlers.get(event, []):
            try:
                handler(payload)
            except Exception:
                logger.exception("handler for %s failed", event)


def register_audit_handlers(
    bus: EventBus, sink: AuditSink, events: Iterable[str] = AUDITED_EVENTS
) -> None:
    for event in events:
        bus.subscribe(event, lambda payload: sink.record(event, payload))
"""

_FILES_14 = {"app/events/bus.py": FileChange(_EVENTS_BEFORE, _EVENTS_AFTER)}

# --------------------------------------------------------------------------------------------
# 15 missing-await (TypeScript)
# --------------------------------------------------------------------------------------------

_SIGNUP_BEFORE = r"""import { hashPassword } from "../auth/passwords";

export interface User {
  id: string;
  email: string;
  displayName: string;
  createdAt: Date;
}

export interface NewUser {
  email: string;
  passwordHash: string;
  displayName: string;
}

export interface UserRepository {
  findByEmail(email: string): Promise<User | null>;
  insert(user: NewUser): Promise<User>;
}

export class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}

export class EmailTakenError extends Error {
  constructor(email: string) {
    super(`email already registered: ${email}`);
    this.name = "EmailTakenError";
  }
}
"""

_SIGNUP_AFTER = (
    _SIGNUP_BEFORE
    + r"""
export interface SignupInput {
  email: string;
  password: string;
  displayName?: string;
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD_LENGTH = 12;

export async function registerUser(
  repo: UserRepository,
  input: SignupInput,
): Promise<{ id: string }> {
  const email = input.email.trim().toLowerCase();
  if (!EMAIL_PATTERN.test(email)) {
    throw new ValidationError("invalid email address");
  }
  if (input.password.length < MIN_PASSWORD_LENGTH) {
    throw new ValidationError(`password must be at least ${MIN_PASSWORD_LENGTH} characters`);
  }
  const existing = repo.findByEmail(email);
  if (existing) {
    throw new EmailTakenError(email);
  }
  const passwordHash = await hashPassword(input.password);
  const user = await repo.insert({
    email,
    passwordHash,
    displayName: input.displayName?.trim() || email.split("@")[0],
  });
  return { id: user.id };
}
"""
)

_FILES_15 = {"src/services/signup.ts": FileChange(_SIGNUP_BEFORE, _SIGNUP_AFTER)}

# --------------------------------------------------------------------------------------------
# 16 wrong-variable
# --------------------------------------------------------------------------------------------

_REPORTS_API_BEFORE = r"""from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify

from app.reports.service import ReportService

bp = Blueprint("reports", __name__, url_prefix="/reports")
service = ReportService.from_app_config()


@bp.get("/today")
def today_summary():
    summary = service.summary_for(date.today(), date.today())
    return jsonify(summary.to_dict())
"""

_REPORTS_API_AFTER = r"""from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest

from app.reports.service import ReportService

bp = Blueprint("reports", __name__, url_prefix="/reports")
service = ReportService.from_app_config()

MAX_RANGE_DAYS = 366


def parse_range(params: Mapping[str, str]) -> tuple[date, date]:
    try:
        start = date.fromisoformat(params["start"])
        end = date.fromisoformat(params["start"])
    except KeyError as exc:
        raise BadRequest(f"missing query parameter: {exc.args[0]}") from exc
    except ValueError as exc:
        raise BadRequest("dates must be formatted as YYYY-MM-DD") from exc
    if end < start:
        raise BadRequest("end must not be before start")
    if (end - start).days > MAX_RANGE_DAYS:
        raise BadRequest(f"range may not exceed {MAX_RANGE_DAYS} days")
    return start, end


@bp.get("/today")
def today_summary():
    summary = service.summary_for(date.today(), date.today())
    return jsonify(summary.to_dict())


@bp.get("/range")
def range_summary():
    start, end = parse_range(request.args)
    summary = service.summary_for(start, end)
    return jsonify(summary.to_dict())
"""

_FILES_16 = {"app/api/reports.py": FileChange(_REPORTS_API_BEFORE, _REPORTS_API_AFTER)}

# --------------------------------------------------------------------------------------------
# 17 inverted-condition
# --------------------------------------------------------------------------------------------

_TTL_CACHE_AFTER = r"""from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_MISSING: Any = object()


@dataclass
class _Entry(Generic[V]):
    value: V
    stored_at: float


class TTLCache(Generic[K, V]):
    def __init__(
        self,
        ttl_seconds: float,
        maxsize: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or maxsize <= 0:
            raise ValueError("ttl_seconds and maxsize must be positive")
        self._ttl = ttl_seconds
        self._maxsize = maxsize
        self._clock = clock
        self._data: OrderedDict[K, _Entry[V]] = OrderedDict()

    def _lookup(self, key: K) -> Any:
        entry = self._data.get(key)
        if entry is None:
            return _MISSING
        if self._clock() - entry.stored_at < self._ttl:
            del self._data[key]
            return _MISSING
        self._data.move_to_end(key)
        return entry.value

    def get(self, key: K, default: V | None = None) -> V | None:
        value = self._lookup(key)
        return default if value is _MISSING else value

    def set(self, key: K, value: V) -> None:
        self._data[key] = _Entry(value, self._clock())
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def get_or_load(self, key: K, loader: Callable[[K], V]) -> V:
        value = self._lookup(key)
        if value is _MISSING:
            value = loader(key)
            self.set(key, value)
        return value

    def __len__(self) -> int:
        return len(self._data)
"""

_FILES_17 = {"app/cache/ttl.py": FileChange("", _TTL_CACHE_AFTER)}

# --------------------------------------------------------------------------------------------
# 18 unbounded-loop
# --------------------------------------------------------------------------------------------

_RETRY_AFTER = r'''from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TransientError(Exception):
    """Raised by callers for failures that are safe to retry (timeouts, 502/503/504)."""


class RetryExhausted(Exception):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay: float = 0.2
    max_delay: float = 5.0

    def delay_for(self, attempt: int) -> float:
        return min(self.max_delay, self.base_delay * 2**attempt)


def call_with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    policy = policy or RetryPolicy()
    attempt = 0
    last_error: TransientError | None = None
    while attempt < policy.max_attempts:
        try:
            return fn()
        except TransientError as exc:
            last_error = exc
            logger.warning("attempt %d failed, retrying: %s", attempt + 1, exc)
            sleep(policy.delay_for(attempt))
    raise RetryExhausted(f"gave up after {policy.max_attempts} attempts") from last_error
'''

_FILES_18 = {"app/clients/retry.py": FileChange("", _RETRY_AFTER)}

# --------------------------------------------------------------------------------------------
# 19 xss (JavaScript)
# --------------------------------------------------------------------------------------------

_COMMENTS_BEFORE = r"""const RELATIVE = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

export function formatRelative(isoTimestamp, now = Date.now()) {
  const seconds = Math.round((new Date(isoTimestamp).getTime() - now) / 1000);
  const units = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) {
      return RELATIVE.format(Math.round(seconds / size), unit);
    }
  }
  return RELATIVE.format(seconds, "second");
}

export async function fetchComments(postId) {
  const response = await fetch(`/api/posts/${encodeURIComponent(postId)}/comments`);
  if (!response.ok) {
    throw new Error(`failed to load comments: ${response.status}`);
  }
  return response.json();
}
"""

_COMMENTS_AFTER = (
    _COMMENTS_BEFORE
    + r"""
export function renderComments(container, comments) {
  container.replaceChildren();
  if (comments.length === 0) {
    const empty = document.createElement("p");
    empty.className = "comments-empty";
    empty.textContent = "No comments yet.";
    container.append(empty);
    return;
  }
  const list = document.createElement("ul");
  list.className = "comment-list";
  for (const comment of comments) {
    const item = document.createElement("li");
    item.className = "comment";
    item.innerHTML = `
      <span class="comment-author">${comment.authorName}</span>
      <time datetime="${comment.createdAt}">${formatRelative(comment.createdAt)}</time>
      <p class="comment-body">${comment.body}</p>`;
    list.append(item);
  }
  container.append(list);
}

export async function loadComments(container, postId) {
  container.setAttribute("aria-busy", "true");
  try {
    renderComments(container, await fetchComments(postId));
  } finally {
    container.removeAttribute("aria-busy");
  }
}
"""
)

_FILES_19 = {"src/ui/comments.js": FileChange(_COMMENTS_BEFORE, _COMMENTS_AFTER)}

# --------------------------------------------------------------------------------------------
# 20 float-money
# --------------------------------------------------------------------------------------------

_INVOICES_BEFORE = r"""from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LineItem:
    sku: str
    description: str
    unit_price: Decimal
    quantity: int


def format_amount(amount: Decimal, currency: str = "USD") -> str:
    return f"{amount:,.2f} {currency}"
"""

_INVOICES_AFTER = r"""from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LineItem:
    sku: str
    description: str
    unit_price: Decimal
    quantity: int


@dataclass(frozen=True)
class InvoiceTotals:
    subtotal: Decimal
    tax: Decimal
    total: Decimal


def format_amount(amount: Decimal, currency: str = "USD") -> str:
    return f"{amount:,.2f} {currency}"


def compute_totals(lines: Iterable[LineItem], tax_rate: Decimal) -> InvoiceTotals:
    items = list(lines)
    if any(item.quantity <= 0 for item in items):
        raise ValueError("line item quantities must be positive")
    subtotal = sum(float(item.unit_price) * item.quantity for item in items)
    tax = subtotal * float(tax_rate)
    return InvoiceTotals(
        subtotal=Decimal(str(round(subtotal, 2))),
        tax=Decimal(str(round(tax, 2))),
        total=Decimal(str(round(subtotal + tax, 2))),
    )
"""

_FILES_20 = {"app/billing/invoices.py": FileChange(_INVOICES_BEFORE, _INVOICES_AFTER)}

# --------------------------------------------------------------------------------------------
# clean-01 refactor: extract a shared helper, behaviour unchanged
# --------------------------------------------------------------------------------------------

_NOTIFY_BEFORE = r"""from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Payment:
    customer_name: str
    amount: Decimal
    currency: str
    invoice_number: str


def receipt_subject(payment: Payment) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(payment.currency)
    if symbol:
        amount = f"{symbol}{payment.amount:,.2f}"
    else:
        amount = f"{payment.amount:,.2f} {payment.currency}"
    return f"Receipt for {amount} (invoice {payment.invoice_number})"


def receipt_body(payment: Payment) -> str:
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(payment.currency)
    if symbol:
        amount = f"{symbol}{payment.amount:,.2f}"
    else:
        amount = f"{payment.amount:,.2f} {payment.currency}"
    return (
        f"Hi {payment.customer_name},\n\n"
        f"We received your payment of {amount} for invoice {payment.invoice_number}.\n"
        "Thank you for your business."
    )
"""

_NOTIFY_AFTER = r"""from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


@dataclass(frozen=True)
class Payment:
    customer_name: str
    amount: Decimal
    currency: str
    invoice_number: str


def format_money(amount: Decimal, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency)
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def receipt_subject(payment: Payment) -> str:
    amount = format_money(payment.amount, payment.currency)
    return f"Receipt for {amount} (invoice {payment.invoice_number})"


def receipt_body(payment: Payment) -> str:
    amount = format_money(payment.amount, payment.currency)
    return (
        f"Hi {payment.customer_name},\n\n"
        f"We received your payment of {amount} for invoice {payment.invoice_number}.\n"
        "Thank you for your business."
    )
"""

_FILES_C1 = {"app/notifications/receipts.py": FileChange(_NOTIFY_BEFORE, _NOTIFY_AFTER)}

# --------------------------------------------------------------------------------------------
# clean-02 new endpoint with validation
# --------------------------------------------------------------------------------------------

_PROJECTS_BEFORE = r"""from __future__ import annotations

from flask import Blueprint, abort, jsonify

from app.db import get_db

bp = Blueprint("projects", __name__, url_prefix="/projects")


def _project_to_dict(row) -> dict[str, object]:
    return {"id": row["id"], "name": row["name"], "slug": row["slug"]}


@bp.get("/<int:project_id>")
def get_project(project_id: int):
    row = get_db().execute(
        "SELECT id, name, slug FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(_project_to_dict(row))
"""

_PROJECTS_AFTER = r"""from __future__ import annotations

import re
import sqlite3

from flask import Blueprint, abort, jsonify, request

from app.db import get_db

bp = Blueprint("projects", __name__, url_prefix="/projects")

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 120
MAX_SLUG_LENGTH = 64


def _project_to_dict(row) -> dict[str, object]:
    return {"id": row["id"], "name": row["name"], "slug": row["slug"]}


def _validate_new_project(data: object) -> tuple[str, str]:
    if not isinstance(data, dict):
        abort(400, description="request body must be a JSON object")
    name = data.get("name")
    slug = data.get("slug")
    if not isinstance(name, str) or not name.strip():
        abort(400, description="name is required")
    if len(name.strip()) > MAX_NAME_LENGTH:
        abort(400, description=f"name must be at most {MAX_NAME_LENGTH} characters")
    if not isinstance(slug, str) or not SLUG_PATTERN.fullmatch(slug):
        abort(400, description="slug must be lowercase letters, digits and single hyphens")
    if len(slug) > MAX_SLUG_LENGTH:
        abort(400, description=f"slug must be at most {MAX_SLUG_LENGTH} characters")
    return name.strip(), slug


@bp.get("/<int:project_id>")
def get_project(project_id: int):
    row = get_db().execute(
        "SELECT id, name, slug FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(_project_to_dict(row))


@bp.post("/")
def create_project():
    name, slug = _validate_new_project(request.get_json(silent=True))
    db = get_db()
    try:
        cursor = db.execute("INSERT INTO projects (name, slug) VALUES (?, ?)", (name, slug))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        abort(409, description=f"slug {slug!r} is already taken")
    project = {"id": cursor.lastrowid, "name": name, "slug": slug}
    return jsonify(project), 201
"""

_FILES_C2 = {"app/api/projects.py": FileChange(_PROJECTS_BEFORE, _PROJECTS_AFTER)}

# --------------------------------------------------------------------------------------------
# clean-03 logging added to a worker
# --------------------------------------------------------------------------------------------

_SYNC_BEFORE = r"""from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class Contact(Protocol):
    id: str
    email: str


class CrmClient(Protocol):
    def updated_contacts(self, since: str) -> Iterable[Contact]: ...


class ContactStore(Protocol):
    def upsert(self, contact: Contact) -> bool: ...
    def get_cursor(self) -> str: ...
    def set_cursor(self, cursor: str) -> None: ...


def sync_contacts(crm: CrmClient, store: ContactStore, now_iso: str) -> int:
    created = 0
    for contact in crm.updated_contacts(since=store.get_cursor()):
        if store.upsert(contact):
            created += 1
    store.set_cursor(now_iso)
    return created
"""

_SYNC_AFTER = r"""from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import Protocol

logger = logging.getLogger(__name__)


class Contact(Protocol):
    id: str
    email: str


class CrmClient(Protocol):
    def updated_contacts(self, since: str) -> Iterable[Contact]: ...


class ContactStore(Protocol):
    def upsert(self, contact: Contact) -> bool: ...
    def get_cursor(self) -> str: ...
    def set_cursor(self, cursor: str) -> None: ...


def sync_contacts(crm: CrmClient, store: ContactStore, now_iso: str) -> int:
    cursor = store.get_cursor()
    logger.info("contact sync starting", extra={"sync_since": cursor})
    started = time.perf_counter()
    created = 0
    seen = 0
    try:
        for contact in crm.updated_contacts(since=cursor):
            seen += 1
            if store.upsert(contact):
                created += 1
            logger.debug("upserted contact", extra={"contact_id": contact.id})
    except Exception:
        logger.exception("contact sync failed after %d contacts", seen)
        raise
    store.set_cursor(now_iso)
    logger.info(
        "contact sync finished",
        extra={
            "seen_count": seen,
            "created_count": created,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return created
"""

_FILES_C3 = {"app/workers/contact_sync.py": FileChange(_SYNC_BEFORE, _SYNC_AFTER)}

# --------------------------------------------------------------------------------------------
# clean-04 config loaded from the environment
# --------------------------------------------------------------------------------------------

_CONFIG_BEFORE = r"""from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    debug: bool


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        debug=os.environ.get("DEBUG", "") == "1",
    )
"""

_CONFIG_AFTER = r"""from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    debug: bool
    request_timeout_s: float
    worker_concurrency: int
    allowed_origins: tuple[str, ...]


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _parse_positive_int(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ConfigError(f"{name} must be at least 1, got {value}")
    return value


def _parse_timeout(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if not 0 < value <= 300:
        raise ConfigError(f"{name} must be between 0 and 300 seconds, got {value}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError("DATABASE_URL is required")
    origins = env.get("ALLOWED_ORIGINS", "")
    return Settings(
        database_url=database_url,
        debug=_parse_bool("DEBUG", env.get("DEBUG", "")),
        request_timeout_s=_parse_timeout("REQUEST_TIMEOUT_S", env.get("REQUEST_TIMEOUT_S", "10")),
        worker_concurrency=_parse_positive_int(
            "WORKER_CONCURRENCY", env.get("WORKER_CONCURRENCY", "4")
        ),
        allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
    )
"""

_FILES_C4 = {"app/config.py": FileChange(_CONFIG_BEFORE, _CONFIG_AFTER)}

# --------------------------------------------------------------------------------------------
# clean-05 test-only change
# --------------------------------------------------------------------------------------------

_SLUG_TESTS_BEFORE = r"""from app.text import slugify


def test_lowercases_and_hyphenates():
    assert slugify("Hello World") == "hello-world"
"""

_SLUG_TESTS_AFTER = r"""import pytest

from app.text import slugify


def test_lowercases_and_hyphenates():
    assert slugify("Hello World") == "hello-world"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  padded  ", "padded"),
        ("multiple   spaces", "multiple-spaces"),
        ("Already-Slugged", "already-slugged"),
        ("punctuation, removed!", "punctuation-removed"),
        ("trailing---hyphens---", "trailing-hyphens"),
        ("Café au lait", "cafe-au-lait"),
    ],
)
def test_normalizes_input(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_empty_string_stays_empty() -> None:
    assert slugify("") == ""


def test_respects_max_length_without_trailing_hyphen() -> None:
    slug = slugify("a very long title that keeps going", max_length=12)
    assert len(slug) <= 12
    assert not slug.endswith("-")


def test_rejects_non_positive_max_length() -> None:
    with pytest.raises(ValueError):
        slugify("anything", max_length=0)
"""

_FILES_C5 = {"tests/test_text.py": FileChange(_SLUG_TESTS_BEFORE, _SLUG_TESTS_AFTER)}

# --------------------------------------------------------------------------------------------

CASES: list[Case] = [
    _case(
        "01-off-by-one",
        "Add rolling_mean and summarize_latencies to a stats module",
        _FILES_01,
        (
            "app/metrics/stats.py",
            "for i in range(len(series) - window)",
            "off-by-one",
            "range stops one short: the final window is dropped and window == len(series) "
            "returns [] instead of one value. Should be range(len(series) - window + 1).",
        ),
    ),
    _case(
        "02-sql-injection",
        "Add a user search function to the users repository",
        _FILES_02,
        (
            "app/repositories/users.py",
            'clauses = [f"(name LIKE',
            "sql-injection",
            "User-supplied query is interpolated into the SQL string; should be a bound "
            "parameter (LIKE ? with '%' + query + '%').",
        ),
    ),
    _case(
        "03-null-check",
        "Add display-name and user-card helpers (TypeScript)",
        _FILES_03,
        (
            "src/users/display.ts",
            "const nickname = user.profile.nickname",
            "null-check",
            "profile is optional, so user.profile.nickname throws a TypeError for users "
            "without a profile; needs user.profile?.nickname.",
        ),
    ),
    _case(
        "04-race-condition",
        "Add stock reservation to a thread-shared inventory store",
        _FILES_04,
        (
            "app/inventory/store.py",
            "if current < quantity:",
            "race-condition",
            "Stock is read and checked outside the lock, then written as current - quantity "
            "under the lock; concurrent reservations both pass the check and overwrite each "
            "other (oversell / lost update). Check and write must happen under one lock.",
        ),
    ),
    _case(
        "05-path-traversal",
        "Add download and usage endpoints to an attachments blueprint",
        _FILES_05,
        (
            "app/api/attachments.py",
            "target = _storage_root() / filename",
            "path-traversal",
            "filename comes from the URL (<path:...>) and is joined without normalising or "
            "checking it stays under the storage root, so ../ sequences read arbitrary files. "
            "Use send_from_directory / safe_join or resolve() and check is_relative_to(root).",
        ),
    ),
    _case(
        "06-resource-leak",
        "Add a daily totals query to the report repository",
        _FILES_06,
        (
            "app/reports/repository.py",
            "        if not rows:",
            "resource-leak",
            "The early return for empty results (and any exception from the query) skips "
            "putconn, leaking a pooled connection until the pool is exhausted. Needs "
            "try/finally like order_count.",
        ),
    ),
    _case(
        "07-mutable-default",
        "Add a billing API client with shared header builder",
        _FILES_07,
        (
            "app/clients/billing.py",
            "def build_headers(",
            "mutable-default",
            "extra defaults to a shared dict that is then mutated and returned, so every call "
            "without extra returns the same object; concurrent clients can send each other's "
            "Authorization header. Use None and build a new dict.",
        ),
    ),
    _case(
        "08-swallowed-exception",
        "Add a capture job to the payments worker",
        _FILES_08,
        (
            "app/workers/payments.py",
            "        except Exception:",
            "swallowed-exception",
            "A failed capture is logged and swallowed, then the order is still marked paid and "
            "counted as captured. The failure must skip mark_paid (continue) or propagate.",
        ),
    ),
    _case(
        "09-wrong-operator",
        "Add role-based authorization middleware (JavaScript)",
        _FILES_09,
        (
            "src/middleware/auth.js",
            'if (user.role = "owner")',
            "wrong-operator",
            "Assignment instead of comparison: the condition is always truthy, so every "
            "authenticated user is treated as owner and bypasses the role check. Use ===.",
        ),
    ),
    _case(
        "10-integer-division",
        "Add a job progress tracker with a status payload",
        _FILES_10,
        (
            "app/jobs/progress.py",
            "return self.processed_items // self.total_items * 100",
            "integer-division",
            "Floor division happens before the multiply, so percent_complete is 0 until the "
            "job finishes and then jumps to 100. Should be processed * 100 // total.",
        ),
    ),
    _case(
        "11-naive-datetime",
        "Add active/remaining/renew helpers to subscriptions",
        _FILES_11,
        (
            "app/billing/subscriptions.py",
            "return datetime.now() < subscription.expires_at",
            "naive-datetime",
            "datetime.now() is naive while expires_at is timezone-aware, so the comparison "
            "raises TypeError (and would use local time even if it did not). Use "
            "datetime.now(timezone.utc) like the rest of the module.",
        ),
    ),
    _case(
        "12-hardcoded-secret",
        "Add a transactional mail integration",
        _FILES_12,
        (
            "app/integrations/mailer.py",
            "MAIL_API_KEY = os.environ.get(",
            "hardcoded-secret",
            "A live API key is committed as the fallback default for MAIL_API_KEY. Secrets "
            "must come only from the environment; fail if it is missing. (Key is fake.)",
        ),
    ),
    _case(
        "13-unchecked-return",
        "Add a nightly Postgres backup job",
        _FILES_13,
        (
            "app/ops/backup.py",
            "subprocess.run(command, capture_output=True, text=True)",
            "unchecked-return",
            "pg_dump's exit status is ignored (no check=True, result discarded), so a failed "
            "or partial dump is uploaded and reported as a successful backup.",
        ),
    ),
    _case(
        "14-closure-late-binding",
        "Register audit handlers on the event bus",
        _FILES_14,
        (
            "app/events/bus.py",
            "bus.subscribe(event, lambda payload: sink.record(event, payload))",
            "closure-late-binding",
            "The lambda closes over the loop variable, so every handler records the last event "
            "name in the list. Bind it with a default argument (lambda payload, event=event).",
        ),
    ),
    _case(
        "15-missing-await",
        "Add user registration service (TypeScript)",
        _FILES_15,
        (
            "src/services/signup.ts",
            "const existing = repo.findByEmail(email);",
            "missing-await",
            "findByEmail returns a Promise that is not awaited; a Promise is always truthy, "
            "so every signup throws EmailTakenError.",
        ),
    ),
    _case(
        "16-wrong-variable",
        "Add a date-range report endpoint",
        _FILES_16,
        (
            "app/api/reports.py",
            'end = date.fromisoformat(params["start"])',
            "wrong-variable",
            "end is parsed from the 'start' parameter, so the 'end' query parameter is ignored "
            "and every report covers a single day.",
        ),
    ),
    _case(
        "17-inverted-condition",
        "Add an LRU cache with time-based expiry",
        _FILES_17,
        (
            "app/cache/ttl.py",
            "if self._clock() - entry.stored_at < self._ttl:",
            "inverted-condition",
            "Expiry check is inverted: fresh entries are evicted and returned as misses while "
            "stale entries are served forever. Should be >= self._ttl.",
        ),
    ),
    _case(
        "18-unbounded-loop",
        "Add a retry helper with exponential backoff",
        _FILES_18,
        (
            "app/clients/retry.py",
            "except TransientError as exc:",
            "unbounded-loop",
            "attempt is never incremented, so a persistently failing call retries forever "
            "(at a constant base delay) instead of raising RetryExhausted.",
        ),
    ),
    _case(
        "19-xss",
        "Render post comments in the browser (JavaScript)",
        _FILES_19,
        (
            "src/ui/comments.js",
            "item.innerHTML = `",
            "xss",
            "Comment author and body (user-controlled) are interpolated into innerHTML without "
            "escaping, allowing stored XSS. Build nodes with textContent instead.",
        ),
    ),
    _case(
        "20-float-money",
        "Add invoice subtotal/tax/total computation",
        _FILES_20,
        (
            "app/billing/invoices.py",
            "subtotal = sum(float(item.unit_price) * item.quantity",
            "float-money",
            "Decimal prices are converted to float for the arithmetic, introducing binary "
            "rounding errors (and round() uses banker's rounding on floats). Keep Decimal and "
            "quantize with ROUND_HALF_UP.",
        ),
    ),
    _case(
        "clean-01-refactor",
        "Extract duplicated currency formatting into a helper (no behaviour change)",
        _FILES_C1,
    ),
    _case(
        "clean-02-validated-endpoint",
        "Add a create-project endpoint with input validation and conflict handling",
        _FILES_C2,
    ),
    _case(
        "clean-03-logging",
        "Add structured logging and timing to the contact sync worker",
        _FILES_C3,
    ),
    _case(
        "clean-04-config",
        "Load typed, validated settings from the environment",
        _FILES_C4,
    ),
    _case(
        "clean-05-tests",
        "Add parametrized tests for slugify (test-only change)",
        _FILES_C5,
    ),
]
