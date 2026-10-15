from __future__ import annotations

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
