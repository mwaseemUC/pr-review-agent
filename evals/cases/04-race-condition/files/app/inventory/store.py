from __future__ import annotations

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
