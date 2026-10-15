from __future__ import annotations

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
