from __future__ import annotations

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
