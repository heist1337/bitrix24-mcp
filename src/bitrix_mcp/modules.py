"""Explicit portal API contracts; no credentials, network or dynamic method dispatch."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any


def spec(required: str = "", optional: str = "") -> dict[str, Any]:
    def fields(value):
        return dict(item.split(":", 1) for item in value.split())

    return {"required": list(fields(required)), "parameters": fields(required + " " + optional)}


API_READ = {
    "scope": spec(),
    "methods": spec(optional="scope:scope"),
    "method.get": spec("name:method"),
    "server.time": spec(),
    "user.fields": spec(),
    "user.current": spec(),
    "user.get": spec(optional="FILTER:user_filter sort:ID|NAME|LAST_NAME order:ASC|DESC start:offset"),
    "user.search": spec("FILTER:user_filter", "sort:ID|NAME|LAST_NAME order:ASC|DESC start:offset"),
    "department.fields": spec(),
    "department.get": spec(
        optional="ID:id NAME:short PARENT:uint UF_HEAD:id sort:ID|NAME|SORT|PARENT|UF_HEAD order:ASC|DESC START:offset"
    ),
    "calendar.section.get": spec("type:calendar_type ownerId:uint"),
    "calendar.event.get": spec("type:calendar_type ownerId:uint from:date to:date", "section:ids"),
    "calendar.event.getbyid": spec("id:id"),
    "calendar.accessibility.get": spec("users:ids from:date to:date"),
    "calendar.meeting.status.get": spec("eventId:id"),
    "calendar.settings.get": spec(),
    "calendar.user.settings.get": spec(),
    "im.recent.list": spec(
        optional="SKIP_OPENLINES:Y|N SKIP_DIALOG:Y|N SKIP_CHAT:Y|N UNREAD_ONLY:Y|N LAST_MESSAGE_DATE:datetime"
    ),
    "im.dialog.get": spec("DIALOG_ID:dialog"),
    "im.dialog.messages.get": spec("DIALOG_ID:dialog", "LAST_ID:id FIRST_ID:id LIMIT:limit"),
    "im.chat.user.list": spec("CHAT_ID:id"),
    "im.search.chat.list": spec("FIND:query", "OFFSET:uint LIMIT:limit"),
    "im.search.user.list": spec("FIND:query", "OFFSET:uint LIMIT:limit"),
    "im.counters.get": spec(),
    "im.notify.get": spec(optional="LAST_ID:uint LAST_TYPE:notify_type LIMIT:limit"),
    "log.blogpost.get": spec(optional="POST_ID:id LOG_DATE_FROM:date LOG_DATE_TO:date start:offset"),
    "log.blogpost.getusers.important": spec("POST_ID:id", "start:offset"),
    "log.blogcomment.user.get": spec(optional="USER_ID:id FIRST_ID:id LAST_ID:id LIMIT:limit"),
    "like.list": spec("ENTITY_TYPE_ID:entity_type ENTITY_ID:id"),
    "like.reactions": spec("ENTITY_TYPE_ID:entity_type ENTITY_ID:id"),
    "tasks.task.getfields": spec(),
    "task.item.getallowedactions": spec("TASKID:id"),
    "task.checklistitem.getlist": spec("TASKID:id"),
    "task.elapseditem.getlist": spec(
        "taskId:id", "order:time_order filter:time_filter select:time_select params:time_page"
    ),
    "task.logitem.list": spec("taskId:id"),
}

CALENDAR_FIELDS = "skip_time:Y|N timezone_from:timezone timezone_to:timezone description:text location:short accessibility:busy|absent|quest|free importance:high|normal|low private_event:Y|N is_meeting:Y|N attendees:ids"
API_WRITE = {
    "calendar.event.add": spec(
        "type:calendar_type ownerId:uint from:date to:date section:id name:short", CALENDAR_FIELDS
    ),
    "calendar.event.update": spec(
        "id:id type:calendar_type ownerId:uint name:short recurrence_mode:this|next|all",
        "from:date to:date section:id current_date_from:date " + CALENDAR_FIELDS,
    ),
    "calendar.meeting.status.set": spec("eventId:id status:Y|N|Q"),
    "im.message.add": spec("DIALOG_ID:dialog MESSAGE:text", "REPLY_ID:id"),
    "im.message.update": spec("MESSAGE_ID:id MESSAGE:text"),
    "im.message.like": spec("MESSAGE_ID:id ACTION:plus|minus"),
    "im.notify": spec("USER_ID:id MESSAGE:text"),
    "log.blogpost.add": spec("POST_TITLE:short POST_MESSAGE:text DEST:destinations"),
    "log.blogpost.update": spec("POST_ID:id POST_TITLE:short POST_MESSAGE:text"),
    "log.blogcomment.add": spec("POST_ID:id TEXT:text"),
    "department.add": spec("NAME:short", "PARENT:id SORT:uint UF_HEAD:id"),
    "department.update": spec("ID:id NAME:short", "PARENT:id SORT:uint UF_HEAD:id"),
    "task.checklistitem.add": spec("TASKID:id FIELDS:checklist_fields"),
    "task.checklistitem.update": spec("TASKID:id ITEMID:id FIELDS:checklist_fields"),
    "task.checklistitem.complete": spec("TASKID:id ITEMID:id"),
    "task.checklistitem.renew": spec("TASKID:id ITEMID:id"),
    "task.elapseditem.add": spec("TASKID:id ARFIELDS:elapsed_fields"),
    "task.item.approve": spec("TASKID:id"),
    "task.item.disapprove": spec("TASKID:id"),
}

SCOPES = {
    "calendar",
    "department",
    "disk",
    "im",
    "log",
    "rating",
    "rest",
    "task",
    "tasks",
    "tasks_extended",
    "user_basic",
}
BLOCKED_EXTENDED = ["tasks_extended.meta.setanystatus", "tasks_extended.meta.occurinlogsas"]
USER_FIELDS = {"ID", "ACTIVE", "NAME", "LAST_NAME", "SECOND_NAME", "WORK_POSITION", "UF_DEPARTMENT", "USER_TYPE"}


def _integer(value: Any, minimum: int = 1, maximum: int = 2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_integer_parameter")


def _validate(value: Any, kind: str):
    if "|" in kind:
        if not isinstance(value, str) or value not in kind.split("|"):
            raise ValueError("invalid_enum_parameter")
    elif kind in {"id", "uint", "offset", "limit", "notify_type"}:
        _integer(value, 0 if kind in {"uint", "offset"} else 1, 50 if kind == "limit" else 2**53 - 1)
        if kind == "offset" and (value % 50 or value > 1_000_000):
            raise ValueError("invalid_page_offset")
        if kind == "notify_type" and value not in {1, 3}:
            raise ValueError("invalid_notification_cursor")
    elif kind in {"ids", "destinations", "time_select"}:
        if not isinstance(value, list) or not 1 <= len(value) <= 50:
            raise ValueError("invalid_array_parameter")
        for item in value:
            if kind == "ids":
                _integer(item)
            elif kind == "destinations":
                if not isinstance(item, str) or not re.fullmatch(r"UA|(?:U|SG|DR)[1-9][0-9]*", item):
                    raise ValueError("invalid_explicit_news_recipient")
            elif item not in {
                "ID",
                "USER_ID",
                "SECONDS",
                "MINUTES",
                "COMMENT_TEXT",
                "CREATED_DATE",
                "DATE_START",
                "DATE_STOP",
            }:
                raise ValueError("unsupported_time_field")
    elif kind.endswith("_fields") or kind in {"user_filter", "time_filter", "time_order", "time_page"}:
        if not isinstance(value, dict) or not value or len(value) > 16:
            raise ValueError("invalid_object_parameter")
        if kind == "time_page":
            if set(value) != {"NAV_PARAMS"} or not isinstance(value["NAV_PARAMS"], dict):
                raise ValueError("invalid_time_pagination")
            nav = value["NAV_PARAMS"]
            if set(nav) != {"nPageSize", "iNumPage"}:
                raise ValueError("invalid_time_pagination")
            _integer(nav["nPageSize"], 1, 50)
            _integer(nav["iNumPage"], 1, 20000)
            return
        maps = {
            "checklist_fields": {
                "TITLE": "short",
                "PARENT_ID": "uint",
                "SORT_INDEX": "uint",
                "IS_COMPLETE": "Y|N",
                "IS_IMPORTANT": "Y|N",
            },
            "elapsed_fields": {"SECONDS": "id", "COMMENT_TEXT": "text"},
            "user_filter": {
                "ID": "id",
                "ACTIVE": "Y|N",
                "NAME": "short",
                "LAST_NAME": "short",
                "FIND": "query",
                "UF_DEPARTMENT": "id",
            },
            "time_filter": {"ID": "id", "USER_ID": "id", "CREATED_DATE": "date"},
            "time_order": {
                x: "asc|desc"
                for x in ("ID", "USER_ID", "MINUTES", "SECONDS", "CREATED_DATE", "DATE_START", "DATE_STOP")
            },
        }
        for key, item in value.items():
            field = re.sub(r"^(?:<=|>=|!|<|>)", "", key) if kind == "time_filter" else key
            if field not in maps[kind]:
                raise ValueError("unsupported_nested_parameter")
            _validate(item, maps[kind][field])
        if kind == "elapsed_fields" and "SECONDS" not in value:
            raise ValueError("elapsed_seconds_required")
    else:
        if (
            not isinstance(value, str)
            or not value.strip()
            or "\x00" in value
            or len(value) > (12000 if kind == "text" else 255)
        ):
            raise ValueError("invalid_text_parameter")
        if kind in {"date", "datetime"}:
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                raise ValueError("iso_date_required") from None
            if (kind == "datetime" or len(value) != 10) and parsed.tzinfo is None:
                raise ValueError("datetime_requires_utc_offset")
        elif kind == "scope" and value not in SCOPES:
            raise ValueError("unsupported_scope")
        elif kind == "method" and not re.fullmatch(r"[a-z][a-z0-9.]{0,120}", value):
            raise ValueError("invalid_method_name")
        elif kind == "dialog" and not re.fullmatch(r"(?:chat|sg)?[1-9][0-9]*", value):
            raise ValueError("invalid_dialog_id")
        elif kind == "calendar_type" and value not in {"user", "group", "company_calendar"}:
            raise ValueError("invalid_calendar_type")
        elif kind == "query" and len(value.strip()) < 2:
            raise ValueError("search_requires_two_characters")
        elif kind == "entity_type" and value not in {"BLOG_POST", "BLOG_COMMENT", "FORUM_TOPIC", "FORUM_POST"}:
            raise ValueError("unsupported_rating_entity")
        elif kind == "timezone":
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("invalid_timezone") from None


def validate_api(method: str, parameters: Any, *, mutation: bool = False) -> dict[str, Any]:
    registry = API_WRITE if mutation else API_READ
    if not isinstance(method, str) or method not in registry:
        raise ValueError("unsupported_portal_method")
    if not isinstance(parameters, dict) or len(json.dumps(parameters)) > 100_000:
        raise ValueError("invalid_portal_parameters")
    entry = registry[method]
    if set(parameters) - entry["parameters"].keys() or not set(entry["required"]) <= parameters.keys():
        raise ValueError("missing_or_unsupported_portal_parameters")
    for key, value in parameters.items():
        _validate(value, entry["parameters"][key])
    p = dict(parameters)
    if "FIRST_ID" in p and "LAST_ID" in p:
        raise ValueError("choose_one_cursor_direction")
    if method == "im.notify.get":
        if ("LAST_ID" in p) != ("LAST_TYPE" in p):
            raise ValueError("notification_cursor_pair_required")
        p.setdefault("LAST_ID", 0)
        p.setdefault("LAST_TYPE", 1)
    if "LIMIT" in entry["parameters"]:
        p.setdefault("LIMIT", 50)
    if method == "task.elapseditem.getlist":
        p.setdefault("params", {"NAV_PARAMS": {"nPageSize": 50, "iNumPage": 1}})
    if method in {"user.get", "user.search", "log.blogpost.get"}:
        p.setdefault("start", 0)
    if method == "department.get":
        p.setdefault("START", 0)
    if method == "task.checklistitem.add" and "TITLE" not in p["FIELDS"]:
        raise ValueError("checklist_title_required")
    if method.startswith("calendar."):
        if (
            method == "calendar.event.update"
            and p["recurrence_mode"] in {"this", "next"}
            and "current_date_from" not in p
        ):
            raise ValueError("recurrence_instance_date_required")
        if "ownerId" in p and ((p["type"] == "company_calendar") != (p["ownerId"] == 0)):
            raise ValueError("invalid_calendar_owner")
        if ("from" in p) != ("to" in p):
            raise ValueError("calendar_both_dates_required")
        if "from" in p:
            start, end = datetime.fromisoformat(p["from"]), datetime.fromisoformat(p["to"])
            if (start.tzinfo is None) != (end.tzinfo is None):
                raise ValueError("calendar_date_modes_must_match")
            if not 0 <= (end - start).total_seconds() <= 62 * 86400:
                raise ValueError("calendar_range_must_be_0_to_62_days")
            if mutation and len(p["from"]) == 10 and p.get("skip_time") != "Y":
                raise ValueError("all_day_event_requires_skip_time")
        if p.get("is_meeting") == "Y" and not p.get("attendees"):
            raise ValueError("meeting_attendees_required")
    if method in {"im.message.add", "im.message.update"}:
        p["URL_PREVIEW"] = "N"
    if method == "im.message.add":
        p["SYSTEM"] = "N"
    if method == "im.notify":
        p["TYPE"] = "USER"
    return p


def sanitize_result(value: Any, token: str) -> tuple[Any, list[str]]:
    """Bound untrusted nested output and withhold credential-bearing provider URLs."""
    changes: list[str] = []
    budget = {"nodes": 0, "chars": 0}
    hidden = {
        "auth",
        "access_token",
        "refresh_token",
        "token",
        "password",
        "download_url",
        "downloadurl",
        "upload_url",
        "uploadurl",
        "external_link",
        "externallink",
    }

    def clean(item, path, depth):
        budget["nodes"] += 1
        if depth > 12 or budget["nodes"] > 10000 or budget["chars"] > 160000:
            changes.append(path)
            return "[TRUNCATED]"
        if item is None or type(item) in {bool, int, float}:
            return item
        if isinstance(item, str):
            item = item.replace(token, "[REDACTED]") if token else item
            item = re.sub(r"/rest/[0-9]+/[^/\s\"<>]+", "/rest/[REDACTED]", item)
            item = re.sub(
                r"https?://[^\s\"<>]*(?:[?&](?:auth|token|access_token|signature|sig)=|/rest/download\.json)[^\s\"<>]*",
                "[SIGNED_URL_WITHHELD]",
                item,
                flags=re.I,
            )
            budget["chars"] += min(len(item), 12000)
            if len(item) > 12000:
                changes.append(path)
            return item[:12000]
        if isinstance(item, list):
            if len(item) > 200:
                changes.append(path)
            return [clean(v, f"{path}[{i}]", depth + 1) for i, v in enumerate(item[:200])]
        if isinstance(item, dict):
            if len(item) > 200:
                changes.append(path)
            out = {}
            for key, child in list(item.items())[:200]:
                if str(key).lower() in hidden:
                    continue
                safe_key = str(key).replace(token, "[REDACTED]") if token else str(key)
                out[safe_key[:200]] = clean(child, f"{path}.{safe_key[:200]}", depth + 1)
            return out
        raise ValueError("unexpected_portal_result_type")

    return clean(value, "result", 0), sorted(set(changes))[:100]


def main() -> dict[str, Any]:
    return {
        "read_methods": API_READ,
        "write_methods": API_WRITE,
        "scope_names": sorted(SCOPES),
        "extended_task_methods_withheld": BLOCKED_EXTENDED,
        "extended_task_reason": "Author impersonation and forced arbitrary statuses are not part of this connector. Use normal task actions with the caller's access checks.",
        "news_comments": "This portal provides log.blogcomment.user.get (by author), not log.blogcomment.get (by post).",
        "pagination": "One provider response per call. Retain native cursors; complete=false unless independently established. No implicit full-company coverage.",
    }
