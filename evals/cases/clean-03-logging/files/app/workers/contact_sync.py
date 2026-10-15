from __future__ import annotations

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
