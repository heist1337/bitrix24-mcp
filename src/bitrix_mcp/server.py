"""Native FastMCP tools; domain contracts remain independently testable."""

from fastmcp import FastMCP

from . import disk, read, write


def create_server() -> FastMCP:
    mcp = FastMCP(
        "Bitrix",
        mask_error_details=True,
        instructions="Use tool catalog operations first. Read explicit bounded pages and follow continuation. Writes require an explicit owner request naming target and content. Plan with apply=false; execute once with apply=true. Reconcile unknown outcomes before retrying. No deletes, ACL changes, public links, impersonation or arbitrary REST.",
    )
    mcp.tool(
        read.main,
        name="bitrix_read",
        description="Read tasks, comments and allowed portal APIs. catalog is offline; capabilities queries live scopes. Discover allowed methods and parameters through catalog. Follow pagination; a page is not a complete dataset.",
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    mcp.tool(
        disk.main,
        name="bitrix_disk",
        description="Read visible storages, folders, file metadata and selected bounded file contents. catalog explains operations. No file mutations or sharing; download URLs are never returned.",
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    mcp.tool(
        write.main,
        name="bitrix_write",
        description="Plan or perform one explicitly requested Bitrix action. catalog lists supported actions; apply=false produces an offline plan, apply=true executes once. Messages and notifications require explicit target and content. Never blindly retry an unknown outcome.",
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    return mcp
