from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str
    is_active: bool


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_active=bool(row["is_active"]),
    )


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute(
        "SELECT id, email, name, is_active FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return _row_to_user(row) if row else None


def deactivate_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))
    conn.commit()


def search_users(
    conn: sqlite3.Connection, query: str, *, active_only: bool = True, limit: int = 50
) -> list[User]:
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(limit, 200))
    clauses = [f"(name LIKE '%{query}%' OR email LIKE '%{query}%')"]
    if active_only:
        clauses.append("is_active = 1")
    sql = (
        "SELECT id, email, name, is_active FROM users WHERE "
        + " AND ".join(clauses)
        + " ORDER BY name LIMIT ?"
    )
    rows = conn.execute(sql, (limit,)).fetchall()
    return [_row_to_user(row) for row in rows]
