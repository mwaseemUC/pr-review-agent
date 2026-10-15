from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import BadRequest

from app.reports.service import ReportService

bp = Blueprint("reports", __name__, url_prefix="/reports")
service = ReportService.from_app_config()

MAX_RANGE_DAYS = 366


def parse_range(params: Mapping[str, str]) -> tuple[date, date]:
    try:
        start = date.fromisoformat(params["start"])
        end = date.fromisoformat(params["start"])
    except KeyError as exc:
        raise BadRequest(f"missing query parameter: {exc.args[0]}") from exc
    except ValueError as exc:
        raise BadRequest("dates must be formatted as YYYY-MM-DD") from exc
    if end < start:
        raise BadRequest("end must not be before start")
    if (end - start).days > MAX_RANGE_DAYS:
        raise BadRequest(f"range may not exceed {MAX_RANGE_DAYS} days")
    return start, end


@bp.get("/today")
def today_summary():
    summary = service.summary_for(date.today(), date.today())
    return jsonify(summary.to_dict())


@bp.get("/range")
def range_summary():
    start, end = parse_range(request.args)
    summary = service.summary_for(start, end)
    return jsonify(summary.to_dict())
