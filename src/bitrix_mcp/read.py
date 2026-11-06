"""Read a bounded task page from the configured Bitrix portal."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bitrix_mcp.modules import (
    API_READ,
    API_WRITE,
    SCOPES,
    USER_FIELDS,
    sanitize_result,
    validate_api,
)
from bitrix_mcp.modules import (
    main as module_catalog,
)


def _configured_portal() -> str:
    value = os.environ.get("BITRIX_PORTAL_URL", "https://example.bitrix24.ru")
    if not value or any(ord(char) <= 32 for char in value):
        raise ValueError("bitrix_https_portal_origin_required")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ValueError("bitrix_https_portal_origin_required") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("bitrix_https_portal_origin_required")
    return "https://" + parsed.netloc


PORTAL = _configured_portal()
MAX_BYTES = 2_000_000
READ_METHODS = {
    *API_READ,
    "profile",
    "tasks.task.list",
    "task.commentitem.getlist",
    "scope",
    "methods",
    "disk.storage.getlist",
    "disk.storage.getchildren",
    "disk.folder.getchildren",
    "disk.folder.get",
    "disk.file.get",
    "disk.file.search",
}
WRITE_METHODS = {
    *API_WRITE,
    "tasks.task.add",
    "tasks.task.update",
    "tasks.task.complete",
    "tasks.task.renew",
    "tasks.task.start",
    "tasks.task.pause",
    "tasks.task.defer",
    "task.commentitem.add",
    "disk.storage.addfolder",
    "disk.folder.addsubfolder",
    "disk.storage.uploadfile",
    "disk.folder.uploadfile",
    "disk.file.rename",
    "disk.folder.rename",
    "disk.file.moveto",
    "disk.folder.moveto",
}
FIELDS = {
    "ID": "id",
    "TITLE": "title",
    "STATUS": "status",
    "RESPONSIBLE_ID": "responsibleId",
    "CREATED_BY": "createdBy",
    "GROUP_ID": "groupId",
    "PARENT_ID": "parentId",
    "DEADLINE": "deadline",
    "CREATED_DATE": "createdDate",
    "CHANGED_DATE": "changedDate",
    "CLOSED_DATE": "closedDate",
    "PRIORITY": "priority",
}
ID_FILTERS = {"ID", "RESPONSIBLE_ID", "CREATED_BY", "GROUP_ID", "PARENT_ID", "ACCOMPLICE", "AUDITOR"}
DATE_FILTERS = {"DEADLINE", "CREATED_DATE", "CHANGED_DATE", "CLOSED_DATE"}
ERROR_CODES = {
    "INVALID_CREDENTIALS",
    "NO_AUTH_FOUND",
    "insufficient_scope",
    "ACCESS_DENIED",
    "ERROR_ACCESS_DENIED",
    "ERROR_NOT_FOUND",
    "METHOD_NOT_FOUND",
    "ERROR_METHOD_NOT_FOUND",
    "QUERY_LIMIT_EXCEEDED",
    "OPERATION_TIME_LIMIT",
    "expired_token",
    "invalid_token",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _filters(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("invalid_bitrix_filters")
    for key, raw in value.items():
        if not isinstance(key, str):
            raise ValueError("invalid_bitrix_filter_field")
        match = re.fullmatch(r"(?:<=|>=|!|<|>)?([A-Z_]+)", key)
        field = match[1] if match else ""
        values = raw if isinstance(raw, list) else [raw]
        if not values or len(values) > 50:
            raise ValueError("invalid_bitrix_filter_values")
        for item in values:
            if field in ID_FILTERS:
                valid = type(item) is int and 0 <= item <= 2**53 - 1
            elif field == "REAL_STATUS":
                valid = type(item) is int and item in {2, 3, 4, 5, 6, 7}
            elif field in DATE_FILTERS:
                valid = isinstance(item, str) and len(item) <= 40
                if valid:
                    try:
                        date = datetime.fromisoformat(item)
                        valid = len(item) == 10 or date.tzinfo is not None
                    except ValueError:
                        valid = False
            elif field == "TITLE":
                valid = isinstance(item, str) and 0 < len(item) <= 200
            else:
                valid = False
            if not valid:
                raise ValueError("unsupported_bitrix_filter")
    return value


def _webhook() -> str:
    return os.environ.get("BITRIX_WEBHOOK_URL", "")


def _connection() -> tuple[str, str]:
    value = _webhook()
    if not isinstance(value, str):
        raise ValueError("invalid_bitrix_webhook")
    match = re.fullmatch(re.escape(PORTAL) + r"/rest/([1-9][0-9]*)/([A-Za-z0-9_-]+)/?", value.strip())
    if not match:
        raise ValueError("bitrix_base_webhook_required")
    return f"{PORTAL}/rest/{match[1]}/{match[2]}/", match[2]


def _request(base: str, method: str, body: dict[str, Any], *, mutation: bool = False) -> dict[str, Any]:
    if method not in (WRITE_METHODS if mutation else READ_METHODS):
        raise ValueError("bitrix_method_not_allowed")
    request = Request(
        base + method + ".json",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=25) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise RuntimeError(f"bitrix_http_{status}") from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise RuntimeError("bitrix_transport_error") from None
    if len(raw) > MAX_BYTES:
        raise RuntimeError("bitrix_response_too_large")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RuntimeError("bitrix_invalid_json") from None
    if not isinstance(data, dict):
        raise RuntimeError("bitrix_invalid_response")
    if "error" in data:
        code = data["error"]
        safe = code if isinstance(code, str) and code in ERROR_CODES else "api_error"
        raise RuntimeError("bitrix_" + safe)
    if "result" not in data:
        raise RuntimeError("bitrix_invalid_result")
    return data


def _text(value: Any, token: str, limit: int) -> tuple[Any, bool]:
    if value is None or type(value) in {int, bool}:
        return value, False
    if not isinstance(value, str):
        raise RuntimeError("bitrix_unexpected_field_type")
    value = value.replace(token, "[REDACTED]")
    value = re.sub(r"/rest/[0-9]+/[^/\s]+", "/rest/[REDACTED]", value)
    return value[:limit], len(value) > limit


def main(
    operation: str = "catalog",
    filters: dict[str, Any] | None = None,
    start: int = 0,
    task_id: int = 0,
    include_description: bool = False,
    method: str = "",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if operation in {"api", "capabilities"}:
        if filters or start or task_id or include_description:
            raise ValueError("portal_api_does_not_accept_task_parameters")
        if operation == "capabilities":
            if method or parameters:
                raise ValueError("capabilities_has_no_parameters")
            base, token = _connection()
            scopes = _request(base, "scope", {})["result"]
            if not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes):
                raise RuntimeError("invalid_scope_result")
            registered = {
                scope: _request(base, "methods", {"scope": scope})["result"]
                for scope in sorted(SCOPES)
                if scope in scopes
            }
            safe_registry, truncated = sanitize_result({"scopes": scopes, "registered_by_scope": registered}, token)
            return {
                "source": "bitrix_live",
                "portal": PORTAL,
                **safe_registry,
                "truncated_paths": truncated,
                "implementation": module_catalog(),
                "registry_caveat": "The legacy methods registry omits some routed methods. Use api/method.get to check an exact method; absence here is not proof of unavailability.",
            }
        body = validate_api(method, {} if parameters is None else parameters)
        base, token = _connection()
        data = _request(base, method, body)
        result = data["result"]
        if method in {"user.get", "user.search", "user.current"}:

            def basic(user):
                if not isinstance(user, dict):
                    raise RuntimeError("invalid_user_result")
                return {k: v for k, v in user.items() if k in USER_FIELDS}

            result = [basic(user) for user in result] if isinstance(result, list) else basic(result)
        result, truncated = sanitize_result(result, token)
        pagination, _ = sanitize_result({k: data[k] for k in ("next", "total") if k in data}, token)
        return {
            "source": "bitrix_live",
            "portal": PORTAL,
            "method": method,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "result": result,
            "pagination": pagination,
            "complete": False,
            "truncated_paths": truncated,
            "coverage": "One provider response, restricted to the webhook owner's access. Native cursors may also be inside result. Truncation is not pagination; narrow the query if truncated.",
        }
    if method or parameters:
        raise ValueError("method_and_parameters_require_api")
    if operation not in {"catalog", "profile", "tasks", "task", "comments"}:
        raise ValueError("unsupported_bitrix_operation")
    filters = _filters(filters)
    if type(start) is not int or not 0 <= start <= 1_000_000 or start % 50:
        raise ValueError("bitrix_start_must_be_page_offset")
    if type(task_id) is not int or not 0 <= task_id <= 2**53 - 1:
        raise ValueError("invalid_bitrix_task_id")
    if type(include_description) is not bool:
        raise ValueError("invalid_description_flag")
    if operation not in {"tasks", "comments"} and (filters or start):
        raise ValueError("filters_and_start_require_tasks")
    if operation not in {"task", "comments"} and task_id:
        raise ValueError("task_id_and_description_require_task")
    if operation != "task" and include_description:
        raise ValueError("description_requires_task")
    if operation == "comments" and (start or set(filters) - {"<ID"}):
        raise ValueError("comments_accept_only_before_id_filter")
    if operation in {"task", "comments"} and not task_id:
        raise ValueError("positive_task_id_required")
    if operation == "catalog":
        return {
            "operations": ["catalog", "profile", "tasks", "task", "comments", "api", "capabilities"],
            "portal_api": module_catalog(),
            "portal": PORTAL,
            "read_only": True,
            "secret_input": False,
            "page_size": 50,
            "filter_fields": sorted(ID_FILTERS | DATE_FILTERS | {"REAL_STATUS", "TITLE"}),
            "filter_operators": ["!", "<", "<=", ">", ">="],
            "date_filters": "YYYY-MM-DD or ISO datetime with UTC offset; business timezone Europe/Moscow",
            "coverage": "Only tasks accessible to the webhook owner. No implicit status/person filter.",
            "pagination": "One page per call. Reuse returned next_start and identical filters until null.",
            "task": "Read one task by ID via list; description only when explicitly requested.",
            "comments": "Classic task comments only. New task-card chats are not covered. Continue with filters={'<ID': next_before_id}.",
        }
    base, token = _connection()
    common = {
        "source": "bitrix_live",
        "portal": PORTAL,
        "timezone": "Europe/Moscow",
        "retrieved_at": datetime.now(UTC).isoformat(),
    }
    if operation == "profile":
        profile = _request(base, "profile", {})["result"]
        if not isinstance(profile, dict):
            raise RuntimeError("bitrix_invalid_profile")
        return {
            **common,
            "profile": {
                key: _text(profile[key], token, 200)[0] for key in ("ID", "NAME", "LAST_NAME") if key in profile
            },
        }
    if operation == "comments":
        rows = _request(
            base,
            "task.commentitem.getlist",
            {
                "TASKID": task_id,
                "ORDER": {"ID": "desc"},
                "FILTER": filters,
            },
        )["result"]
        if not isinstance(rows, list):
            raise RuntimeError("bitrix_invalid_comments")
        comments = []
        for row in rows[:50]:
            if not isinstance(row, dict) or not str(row.get("ID", "")).isdigit():
                raise RuntimeError("bitrix_invalid_comment")
            item, cuts = {}, []
            for key in ("ID", "AUTHOR_ID", "AUTHOR_NAME", "POST_DATE", "POST_MESSAGE"):
                if key in row:
                    item[key], cut = _text(row[key], token, 12000 if key == "POST_MESSAGE" else 500)
                    if cut:
                        cuts.append(key)
            item["truncated_fields"] = cuts
            comments.append(item)
        return {
            **common,
            "comments": comments,
            "returned": len(comments),
            "complete": False,
            "next_before_id": int(comments[-1]["ID"]) if len(comments) == 50 else None,
            "coverage": "Classic comments only; not the new task-card chat. Absence is not proof a message was not sent.",
        }
    selected = dict(FIELDS)
    if include_description:
        selected["DESCRIPTION"] = "description"
    body = {
        "filter": {"ID": task_id} if operation == "task" else filters,
        "select": list(selected),
        "order": {"ID": "ASC"},
        "start": start,
    }
    data = _request(base, "tasks.task.list", body)
    if not isinstance(data["result"], dict):
        raise RuntimeError("bitrix_invalid_task_result")
    rows = data["result"].get("tasks")
    if not isinstance(rows, list) or len(rows) > 50:
        raise RuntimeError("bitrix_invalid_task_page")
    tasks = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("bitrix_invalid_task")
        task, truncated = {}, []
        for source, target in selected.items():
            if target in row or source in row:
                task[target], cut = _text(
                    row.get(target, row.get(source)), token, 12000 if target == "description" else 500
                )
                if cut:
                    truncated.append(target)
        if not re.fullmatch(r"[1-9][0-9]*", str(task.get("id", ""))):
            raise RuntimeError("bitrix_missing_task_id")
        if operation == "task" and str(task["id"]) != str(task_id):
            raise RuntimeError("bitrix_task_id_mismatch")
        task["truncated_fields"] = truncated
        tasks.append(task)
    next_start = data.get("next")
    total = data.get("total")
    if next_start is not None and (type(next_start) is not int or next_start != start + 50):
        raise RuntimeError("bitrix_invalid_next_page")
    if total is not None and (type(total) is not int or total < len(tasks)):
        raise RuntimeError("bitrix_invalid_total")
    return {
        **common,
        "tasks": tasks,
        "returned": len(tasks),
        "total_matching": total,
        "start": start,
        "next_start": next_start,
        "complete": start == 0 and next_start is None and total == len(tasks),
        "coverage": "Visible tasks matching these filters, not all company tasks; pages are a live view.",
    }
