from __future__ import annotations

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
