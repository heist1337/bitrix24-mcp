"""Read Bitrix Disk metadata and explicitly selected small files."""

from __future__ import annotations

import base64
import hashlib
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from bitrix_mcp.read import MAX_BYTES, PORTAL, NoRedirect, _connection, _request, _text

DISK_FIELDS = (
    "ID",
    "NAME",
    "TYPE",
    "ENTITY_TYPE",
    "ENTITY_ID",
    "ROOT_OBJECT_ID",
    "STORAGE_ID",
    "PARENT_ID",
    "SIZE",
    "CREATE_TIME",
    "UPDATE_TIME",
    "CREATED_BY",
    "UPDATED_BY",
)
METHODS = {
    "storages": "disk.storage.getlist",
    "root": "disk.storage.getchildren",
    "children": "disk.folder.getchildren",
    "folder": "disk.folder.get",
    "file": "disk.file.get",
    "search": "disk.file.search",
    "download": "disk.file.get",
}


def _item(row: Any, token: str) -> dict[str, Any]:
    if not isinstance(row, dict) or not str(row.get("ID", "")).isdigit():
        raise RuntimeError("bitrix_invalid_disk_item")
    # No signed URLs, arbitrary nested objects or access-control lists in metadata.
    return {key: _text(row[key], token, 500)[0] for key in DISK_FIELDS if key in row}


def _download(row: dict[str, Any], representation: str, token: str) -> dict[str, Any]:
    url = row.get("DOWNLOAD_URL")
    if not isinstance(url, str):
        raise RuntimeError("bitrix_download_unavailable")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != urlsplit(PORTAL).netloc
        or parsed.path != "/rest/download.json"
        or parsed.fragment
    ):
        raise RuntimeError("bitrix_download_route_not_allowed")
    size = str(row.get("SIZE", ""))
    if not size.isdigit() or int(size) > MAX_BYTES:
        raise ValueError("bitrix_file_exceeds_2mb_inline_limit")
    try:
        with build_opener(NoRedirect).open(Request(url), timeout=25) as response:
            data = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise RuntimeError(f"bitrix_download_http_{code}") from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise RuntimeError("bitrix_download_transport_error") from None
    if len(data) > MAX_BYTES or len(data) != int(size):
        raise RuntimeError("bitrix_download_size_mismatch")
    if token.encode() in data:
        raise RuntimeError("bitrix_download_contains_connection_secret")
    result = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "representation": representation}
    if representation == "text":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            raise ValueError("bitrix_non_utf8_file_use_base64") from None
        if "\x00" in text:
            raise ValueError("bitrix_binary_file_use_base64")
        result["content"] = _text(text, token, MAX_BYTES)[0]
    else:
        result["content_base64"] = base64.b64encode(data).decode()
    return result


def main(
    operation: str = "catalog",
    object_id: int = 0,
    start: int = 0,
    query: str = "",
    storage_id: int = 0,
    folder_id: int = 0,
    representation: str = "text",
) -> dict[str, Any]:
    if operation not in {"catalog", *METHODS}:
        raise ValueError("unsupported_bitrix_disk_operation")
    if any(type(n) is not int or not 0 <= n <= 2**53 - 1 for n in [object_id, storage_id, folder_id]):
        raise ValueError("invalid_disk_id")
    if type(start) is not int or start < 0 or start > 1_000_000 or start % 50:
        raise ValueError("invalid_disk_page")
    if not isinstance(query, str) or representation not in {"text", "base64"}:
        raise ValueError("invalid_disk_parameters")
    if operation != "search" and (query or storage_id or folder_id):
        raise ValueError("search_parameters_require_search")
    if operation != "download" and representation != "text":
        raise ValueError("representation_requires_download")
    if operation in {"catalog", "storages", "search"} and object_id:
        raise ValueError("unexpected_disk_id")
    if operation not in {"catalog", "storages", "search"} and not object_id:
        raise ValueError("positive_disk_id_required")
    if operation not in {"storages", "root", "children", "search"} and start:
        raise ValueError("pagination_requires_list")
    if operation == "catalog":
        return {
            "operations": ["catalog", *METHODS],
            "read_only": True,
            "page_size": 50,
            "download": "Explicit file ID only, max 2 MB, UTF-8 text or base64; URLs never returned.",
            "search": "Names of visible files/folders; excludes some storage types and chat folders. Max start 1000.",
            "writes": "Use bitrix_write for create folder, upload, rename and move; no delete/sharing.",
        }
    body: dict[str, Any] = {}
    if object_id:
        body["id"] = object_id
    if operation in {"storages", "root", "children"}:
        body.update(start=start, order={"ID": "ASC"})
    if operation == "search":
        query = " ".join(query.split())
        if not 3 <= len(query) <= 255 or start > 1000:
            raise ValueError("disk_search_requires_3_to_255_chars_and_start_at_most_1000")
        body = {"QUERY": query, "TYPE": "all", "start": start}
        scope = {}
        if storage_id:
            scope["STORAGE_ID"] = storage_id
        if folder_id:
            scope["FOLDER_ID"] = folder_id
        if scope:
            body["FILTER"] = scope
    base, token = _connection()
    data = _request(base, METHODS[operation], body)
    result = data["result"]
    common = {"source": "bitrix_disk_live", "portal": PORTAL, "operation": operation}
    if operation in {"file", "folder", "download"}:
        item = _item(result, token)
        if str(item["ID"]) != str(object_id):
            raise RuntimeError("bitrix_disk_id_mismatch")
        return {**common, "item": item, **(_download(result, representation, token) if operation == "download" else {})}
    if not isinstance(result, list) or len(result) > 50:
        raise RuntimeError("bitrix_invalid_disk_page")
    total, next_start = data.get("total"), data.get("next")
    if total is not None and (type(total) is not int or total < len(result)):
        raise RuntimeError("bitrix_invalid_disk_total")
    if next_start is not None and (type(next_start) is not int or next_start != start + 50):
        raise RuntimeError("bitrix_invalid_disk_cursor")
    capped = operation == "search" and next_start is not None and next_start > 1000
    return {
        **common,
        "items": [_item(row, token) for row in result],
        "returned": len(result),
        "total_matching": total,
        "start": start,
        "next_start": None if capped else next_start,
        "search_limit_reached": capped,
        "complete": start == 0 and next_start is None and total == len(result),
        "coverage": "Only this visible storage/folder/search page; not a recursive inventory of all company files.",
    }
