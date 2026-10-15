from __future__ import annotations

import re
import sqlite3

from flask import Blueprint, abort, jsonify, request

from app.db import get_db

bp = Blueprint("projects", __name__, url_prefix="/projects")

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 120
MAX_SLUG_LENGTH = 64


def _project_to_dict(row) -> dict[str, object]:
    return {"id": row["id"], "name": row["name"], "slug": row["slug"]}


def _validate_new_project(data: object) -> tuple[str, str]:
    if not isinstance(data, dict):
        abort(400, description="request body must be a JSON object")
    name = data.get("name")
    slug = data.get("slug")
    if not isinstance(name, str) or not name.strip():
        abort(400, description="name is required")
    if len(name.strip()) > MAX_NAME_LENGTH:
        abort(400, description=f"name must be at most {MAX_NAME_LENGTH} characters")
    if not isinstance(slug, str) or not SLUG_PATTERN.fullmatch(slug):
        abort(400, description="slug must be lowercase letters, digits and single hyphens")
    if len(slug) > MAX_SLUG_LENGTH:
        abort(400, description=f"slug must be at most {MAX_SLUG_LENGTH} characters")
    return name.strip(), slug


@bp.get("/<int:project_id>")
def get_project(project_id: int):
    row = get_db().execute(
        "SELECT id, name, slug FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(_project_to_dict(row))


@bp.post("/")
def create_project():
    name, slug = _validate_new_project(request.get_json(silent=True))
    db = get_db()
    try:
        cursor = db.execute("INSERT INTO projects (name, slug) VALUES (?, ?)", (name, slug))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        abort(409, description=f"slug {slug!r} is already taken")
    project = {"id": cursor.lastrowid, "name": name, "slug": slug}
    return jsonify(project), 201
