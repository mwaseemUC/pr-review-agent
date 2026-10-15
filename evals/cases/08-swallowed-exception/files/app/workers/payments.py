from __future__ import annotations

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
