from __future__ import annotations

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
