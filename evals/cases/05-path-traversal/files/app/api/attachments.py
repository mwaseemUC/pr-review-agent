from __future__ import annotations

import mimetypes
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, send_file

bp = Blueprint("attachments", __name__, url_prefix="/attachments")


def _storage_root() -> Path:
    return Path(current_app.config["ATTACHMENT_ROOT"])


@bp.get("/")
def list_attachments():
    root = _storage_root()
    entries = [
        {"name": p.name, "size": p.stat().st_size}
        for p in sorted(root.iterdir())
        if p.is_file()
    ]
    return jsonify(entries)


@bp.get("/usage")
def storage_usage():
    files = [p for p in _storage_root().iterdir() if p.is_file()]
    return jsonify({"files": len(files), "bytes": sum(p.stat().st_size for p in files)})


@bp.get("/<path:filename>")
def download_attachment(filename: str):
    target = _storage_root() / filename
    if not target.is_file():
        abort(404)
    mimetype, _ = mimetypes.guess_type(target.name)
    return send_file(
        target,
        mimetype=mimetype or "application/octet-stream",
        as_attachment=True,
        download_name=target.name,
    )
