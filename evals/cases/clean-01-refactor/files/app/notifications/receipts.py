from __future__ import annotations

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
