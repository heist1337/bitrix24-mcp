"""Plan or execute one owner-requested task or Disk change, without automatic retries."""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime
from typing import Any

from bitrix_mcp.modules import API_WRITE, validate_api
from bitrix_mcp.read import MAX_BYTES, PORTAL, _connection, _request

TASK_STATES = {
    "complete_task": "tasks.task.complete",
    "reopen_task": "tasks.task.renew",
    "start_task": "tasks.task.start",
    "pause_task": "tasks.task.pause",
    "defer_task": "tasks.task.defer",
}
OPERATIONS = {
    "api",
    "create_task",
    "update_task",
    "comment",
    *TASK_STATES,
    "create_folder",
    "upload_file",
    "rename_file",
    "rename_folder",
    "move_file",
    "move_folder",
}
TASK_FIELDS = {
    "TITLE",
    "DESCRIPTION",
    "RESPONSIBLE_ID",
    "DEADLINE",
    "PRIORITY",
    "GROUP_ID",
    "PARENT_ID",
    "ACCOMPLICES",
    "AUDITORS",
    "TAGS",
    "UF_TASK_WEBDAV_FILES",
}


def _id(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 2**53 - 1:
        raise ValueError("positive_integer_id_required")
    return value


def _string(value: Any, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value or (not empty and not value.strip()):
        raise ValueError("invalid_bitrix_text")
    return value


def _name(value: Any) -> str:
    value = _string(value, 255)
    if value in {".", ".."} or any(x in value for x in '/\\:*?"<>|'):
        raise ValueError("invalid_disk_name")
    return value


def _fields(fields: Any, *, create: bool) -> dict[str, Any]:
    if not isinstance(fields, dict) or not fields or set(fields) - TASK_FIELDS:
        raise ValueError("unsupported_task_fields")
    if create and not {"TITLE", "RESPONSIBLE_ID"} <= fields.keys():
        raise ValueError("task_title_and_responsible_required")
    for key, value in fields.items():
        if key == "TITLE":
            _string(value, 255)
        elif key == "DESCRIPTION":
            _string(value, 12000, empty=True)
        elif key in {"RESPONSIBLE_ID", "GROUP_ID", "PARENT_ID"}:
            _id(value)
        elif key == "PRIORITY":
            if type(value) is not int or value not in {0, 1, 2}:
                raise ValueError("invalid_task_priority")
        elif key == "DEADLINE":
            _string(value, 40, empty=True)
            if value:
                try:
                    valid = datetime.fromisoformat(value).tzinfo is not None
                except ValueError:
                    valid = False
                if not valid:
                    raise ValueError("deadline_requires_iso_datetime_with_offset")
        else:
            if not isinstance(value, list) or len(value) > 50:
                raise ValueError("invalid_task_array")
            for item in value:
                if key in {"ACCOMPLICES", "AUDITORS"}:
                    _id(item)
                elif key == "TAGS":
                    _string(item, 100)
                elif (
                    not isinstance(item, str)
                    or not item.startswith("n")
                    or not item[1:].isdigit()
                    or int(item[1:]) <= 0
                ):
                    raise ValueError("disk_attachment_requires_n_prefixed_id")
    return fields


def _plan(operation: str, p: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if operation == "api":
        if set(p) != {"method", "arguments"}:
            raise ValueError("api_requires_method_and_arguments")
        method = p["method"]
        body = validate_api(method, p["arguments"], mutation=True)
    elif operation in {"create_task", "update_task"}:
        keys = {"fields"} if operation == "create_task" else {"task_id", "fields"}
        if set(p) != keys:
            raise ValueError("invalid_task_parameters")
        body = {"fields": _fields(p["fields"], create=operation == "create_task")}
        if operation == "update_task":
            body["taskId"] = _id(p["task_id"])
        method = "tasks.task.add" if operation == "create_task" else "tasks.task.update"
    elif operation in TASK_STATES:
        if set(p) != {"task_id"}:
            raise ValueError("task_id_only_required")
        method, body = TASK_STATES[operation], {"taskId": _id(p["task_id"])}
    elif operation == "comment":
        if set(p) != {"task_id", "message"}:
            raise ValueError("invalid_comment_parameters")
        # This classic method is advertised by this on-premise portal. Never spoof AUTHOR_ID.
        method = "task.commentitem.add"
        body = {"TASKID": _id(p["task_id"]), "FIELDS": {"POST_MESSAGE": _string(p["message"], 12000)}}
    elif operation in {"create_folder", "upload_file"}:
        keys = {"parent_id", "parent_type", "name"}
        if operation == "upload_file":
            keys.add("content_base64")
        if set(p) != keys or p["parent_type"] not in {"storage", "folder"}:
            raise ValueError("invalid_disk_create_parameters")
        parent, name = _id(p["parent_id"]), _name(p["name"])
        body = {"id": parent, "data": {"NAME": name}}
        if operation == "create_folder":
            method = "disk.storage.addfolder" if p["parent_type"] == "storage" else "disk.folder.addsubfolder"
        else:
            encoded = _string(p["content_base64"], ((MAX_BYTES + 2) // 3) * 4, empty=True)
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("invalid_upload_base64") from None
            if len(content) > MAX_BYTES:
                raise ValueError("bitrix_upload_exceeds_2mb_inline_limit")
            body.update(fileContent=[name, encoded], generateUniqueName=False)
            method = f"disk.{p['parent_type']}.uploadfile"
            return (
                method,
                body,
                {
                    "parent_type": p["parent_type"],
                    "parent_id": parent,
                    "name": name,
                    "bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "generate_unique_name": False,
                },
            )
    elif operation in {"rename_file", "rename_folder", "move_file", "move_folder"}:
        verb, kind = operation.split("_")
        field = "name" if verb == "rename" else "target_folder_id"
        if set(p) != {"object_id", field}:
            raise ValueError("invalid_disk_change_parameters")
        body = {"id": _id(p["object_id"])}
        if verb == "rename":
            method, body["newName"] = f"disk.{kind}.rename", _name(p["name"])
        else:
            method, body["targetFolderId"] = f"disk.{kind}.moveto", _id(p["target_folder_id"])
    else:
        raise ValueError("unsupported_bitrix_write_operation")
    return method, body, body


def _receipt(operation: str, result: Any, parameters: dict[str, Any]) -> dict[str, Any]:
    if operation == "api":
        method = parameters["method"]
        if result is True:
            return {"method": method, "accepted": True}
        if type(result) is int and result > 0:
            return {"method": method, "id": result}
        if isinstance(result, str) and result.isdigit() and int(result) > 0:
            return {"method": method, "id": int(result)}
        if isinstance(result, dict):
            identifier = result.get("id", result.get("ID"))
            if type(identifier) is int and identifier > 0:
                return {"method": method, "id": identifier}
        raise ValueError("unrecognized_portal_write_receipt")
    if operation == "comment":
        if type(result) is not int or result <= 0:
            raise ValueError("invalid_comment_receipt")
        return {"comment_id": result, "task_id": parameters["task_id"]}
    if operation in {"create_task", "update_task", *TASK_STATES}:
        task = result.get("task") if isinstance(result, dict) else None
        if isinstance(task, dict):
            identifier = task.get("id", task.get("ID"))
            if not str(identifier).isdigit() or int(identifier) <= 0:
                raise ValueError("invalid_task_receipt")
            if operation != "create_task" and int(identifier) != parameters["task_id"]:
                raise ValueError("task_receipt_id_mismatch")
            return {"task_id": int(identifier)}
        if operation != "create_task" and result is True:
            return {"task_id": parameters["task_id"]}
        raise ValueError("invalid_task_receipt")
    if isinstance(result, dict) and str(result.get("ID", "")).isdigit() and int(result["ID"]) > 0:
        identifier = int(result["ID"])
        if "object_id" in parameters and identifier != parameters["object_id"]:
            raise ValueError("disk_receipt_id_mismatch")
        return {"disk_object_id": identifier}
    if result is True and "object_id" in parameters:
        return {"disk_object_id": parameters["object_id"]}
    raise ValueError("invalid_disk_receipt")


def main(operation: str = "catalog", parameters: dict[str, Any] | None = None, apply: bool = False) -> dict[str, Any]:
    if operation not in {"catalog", *OPERATIONS} or type(apply) is not bool:
        raise ValueError("unsupported_bitrix_write_operation")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise ValueError("bitrix_parameters_object_required")
    if operation == "catalog":
        if parameters or apply:
            raise ValueError("catalog_has_no_parameters_or_apply")
        return {
            "operations": sorted(OPERATIONS),
            "api_methods": API_WRITE,
            "parameters": {
                "api": {
                    "method": "im.message.add",
                    "arguments": {"DIALOG_ID": "123", "MESSAGE": "Exact owner-approved text"},
                },
                "create_task": {"fields": {"TITLE": "Required title", "RESPONSIBLE_ID": 123}},
                "update_task": {"task_id": 123, "fields": {"DEADLINE": "2026-11-01T18:00:00+03:00"}},
                "task_state_operations": {"task_id": 123},
                "comment": {"task_id": 123, "message": "Exact owner-approved text"},
                "create_folder": {"parent_type": "storage or folder", "parent_id": 123, "name": "Folder name"},
                "upload_file": {
                    "parent_type": "storage or folder",
                    "parent_id": 123,
                    "name": "file.txt",
                    "content_base64": "aGVsbG8=",
                },
                "rename_file_or_folder": {"object_id": 123, "name": "New name; preserve file extension"},
                "move_file_or_folder": {"object_id": 123, "target_folder_id": 456},
            },
            "task_fields": sorted(TASK_FIELDS),
            "default": "apply=false: exact plan without credentials or network",
            "execution": "apply=true only for an explicit owner request naming the target and content; setup is not such a request",
            "retries": "No automatic retries or exactly-once guarantee. Unknown outcome must be reconciled by reading Bitrix before any repeat.",
            "effects": "Bitrix may notify task participants, event attendees, chat/news recipients and employees. Arrays replace existing values. News creation requires explicit DEST; no default company-wide audience.",
            "excluded": ["delete", "public links", "sharing/ACL", "author impersonation", "arbitrary REST", "batch"],
            "upload_limit_bytes": MAX_BYTES,
        }
    method, body, plan = _plan(operation, parameters)
    common = {"portal": PORTAL, "operation": operation}
    if not apply:
        return {
            **common,
            "outcome": "planned",
            "mutated": False,
            "method": method,
            "changes": plan,
            "note": "Server permissions, current values, notifications and conflicts are not checked by this offline plan.",
        }
    base, _ = _connection()
    try:
        response = _request(base, method, body, mutation=True)
        receipt = _receipt(operation, response["result"], parameters)
    except (RuntimeError, ValueError):
        # A lost response or parsing failure may follow a committed change. Never retry here.
        return {
            **common,
            "outcome": "unknown",
            "mutated": None,
            "retry_safe": False,
            "next_step": "Read the target in Bitrix and reconcile before any repeat. Creation/comment/upload may already exist.",
        }
    return {**common, "outcome": "applied", "mutated": True, "receipt": receipt, "retry_safe": False}
