"""Order lookup helpers for the storefront admin panel."""

import sqlite3
from dataclasses import dataclass


@dataclass
class Order:
    id: int
    customer_email: str
    total_cents: int
    status: str


def _row_to_order(row: tuple) -> Order:
    return Order(id=row[0], customer_email=row[1], total_cents=row[2], status=row[3])


def find_orders_by_email(conn: sqlite3.Connection, email: str) -> list[Order]:
    cursor = conn.execute(
        f"SELECT id, customer_email, total_cents, status FROM orders "
        f"WHERE customer_email = '{email}' ORDER BY id DESC"
    )
    return [_row_to_order(row) for row in cursor.fetchall()]


def paginate(orders: list[Order], page: int, page_size: int = 20) -> list[Order]:
    """Return the 1-based `page` of `orders`."""
    start = page * page_size
    return orders[start : start + page_size]


def refundable_total(orders: list[Order]) -> int:
    return sum(o.total_cents for o in orders if o.status in ("paid", "shipped"))
