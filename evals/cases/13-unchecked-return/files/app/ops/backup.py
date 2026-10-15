from __future__ import annotations

import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3

logger = logging.getLogger(__name__)


def upload_file(path: Path, bucket: str, key: str) -> None:
    boto3.client("s3").upload_file(str(path), bucket, key)


def create_backup(db_name: str, bucket: str, *, now: datetime | None = None) -> str:
    """Dump `db_name` with pg_dump (connection settings come from PG* env vars) and upload it."""
    now = now or datetime.now(timezone.utc)
    key = f"postgres/{db_name}/{now:%Y/%m/%d}/{now:%H%M%S}.dump"
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "backup.dump"
        command = ["pg_dump", "--format=custom", "--file", str(dump_path), db_name]
        subprocess.run(command, capture_output=True, text=True)
        upload_file(dump_path, bucket, key)
    logger.info("uploaded backup of %s to s3://%s/%s", db_name, bucket, key)
    return key
