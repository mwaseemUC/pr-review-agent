from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    database_url: str
    debug: bool
    request_timeout_s: float
    worker_concurrency: int
    allowed_origins: tuple[str, ...]


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _parse_positive_int(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ConfigError(f"{name} must be at least 1, got {value}")
    return value


def _parse_timeout(name: str, raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if not 0 < value <= 300:
        raise ConfigError(f"{name} must be between 0 and 300 seconds, got {value}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    database_url = env.get("DATABASE_URL", "").strip()
    if not database_url:
        raise ConfigError("DATABASE_URL is required")
    origins = env.get("ALLOWED_ORIGINS", "")
    return Settings(
        database_url=database_url,
        debug=_parse_bool("DEBUG", env.get("DEBUG", "")),
        request_timeout_s=_parse_timeout("REQUEST_TIMEOUT_S", env.get("REQUEST_TIMEOUT_S", "10")),
        worker_concurrency=_parse_positive_int(
            "WORKER_CONCURRENCY", env.get("WORKER_CONCURRENCY", "4")
        ),
        allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
    )
