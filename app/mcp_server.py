"""MCP server: the actions a customer's own Claude can call. No AI runs here.

Each workspace connects with its own URL: https://<host>/mcp/<workspace_key>
The ASGI wrapper below validates the key, passes it on as a header and forwards
the request to the MCP app, so every tool is scoped to one workspace.
"""
import os

from pydantic import BaseModel, Field
from sqlmodel import select

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from app import services
from app.db import get_session
from app.models import Agreement, Workspace
from app.money import inr_to_paise

KEY_HEADER = "x-clientroom-workspace-key"

mcp = MCPServer(
    name="ClientRoom",
    instructions=(
        "ClientRoom stores client agreements for a service agency and lets the agency's client "
        "approve them from a link. Use create_agreement to turn a project brief into a structured "
        "agreement (prices in Indian rupees). After an agreement is approved, use "
        "create_change_request for extra scope. Use list_agreements / get_agreement to report status. "
        "Always give the user the links returned by the tools."
    ),
)


class Milestone(BaseModel):
    name: str = Field(description="Milestone name, e.g. 'Homepage design'")
    due_date: str = Field(default="", description="Due date as YYYY-MM-DD")


def _base_url(ctx: Context) -> str:
    env = os.environ.get("PUBLIC_BASE_URL")
    if env:
        return env.rstrip("/")
    headers = ctx.headers or {}
    host = headers.get("x-forwarded-host") or headers.get("host") or "localhost:8000"
    scheme = headers.get("x-forwarded-proto") or ("http" if host.startswith(("localhost", "127.")) else "https")
    return f"{scheme}://{host}"


def _workspace_id(ctx: Context) -> int:
    key = (ctx.headers or {}).get(KEY_HEADER)
    if not key:
        raise ToolError("Missing workspace key. Reconnect using the MCP URL from ClientRoom settings.")
    with get_session() as s:
        ws = s.exec(select(Workspace).where(Workspace.mcp_key == key)).first()
    if not ws:
        raise ToolError("Unknown workspace key.")
    return ws.id


def _links(ctx: Context, a: Agreement) -> dict:
    base = _base_url(ctx)
    return {"agency_url": f"{base}/agreements/{a.id}", "client_review_url": f"{base}/c/{a.share_token}"}


@mcp.tool()
def create_agreement(
    ctx: Context,
    client_name: str,
    project_title: str,
    price_inr: float,
    deliverables: list[str],
    exclusions: list[str] = [],
    milestones: list[Milestone] = [],
    revision_limit: int = 2,
    summary: str = "",
    client_email: str = "",
) -> dict:
    """Create a draft client agreement in ClientRoom from a project brief.

    price_inr is the fixed project price in rupees (e.g. 100000 for ₹1,00,000).
    The agency reviews and edits the draft in ClientRoom, then sends the client review link.
    """
    wid = _workspace_id(ctx)
    if price_inr < 0:
        raise ToolError("price_inr must be zero or more.")
    with get_session() as s:
        a = services.create_agreement(
            s, wid, client_name=client_name, project_title=project_title, price_paise=inr_to_paise(price_inr),
            summary=summary, client_email=client_email, deliverables=deliverables, exclusions=exclusions,
            milestones=[m.model_dump() for m in milestones], revision_limit=revision_limit, created_via="claude",
        )
        out = services.summary(s, a)
        out.update(_links(ctx, a))
        out["next_step"] = "Open agency_url to review/edit, then press 'Send to client' to activate the client link."
        return out


@mcp.tool()
def create_change_request(ctx: Context, agreement_id: int, description: str, extra_price_inr: float, extra_days: int = 0) -> dict:
    """Raise a change request (extra scope, price and days) on an APPROVED agreement.

    The client approves or declines it from the same review link; ClientRoom then updates
    the approved total and final deadline.
    """
    wid = _workspace_id(ctx)
    with get_session() as s:
        try:
            a = services.get_agreement(s, wid, agreement_id)
            cr = services.create_change_request(
                s, a, description=description, extra_price_paise=inr_to_paise(extra_price_inr),
                extra_days=extra_days, created_via="claude",
            )
        except (services.NotFound, ValueError) as e:
            raise ToolError(str(e))
        out = {"change_request_id": cr.id, "status": cr.status, "agreement": services.summary(s, a)}
        out.update(_links(ctx, a))
        return out


@mcp.tool()
def list_agreements(ctx: Context, status: str = "") -> list[dict]:
    """List this agency's agreements with status, approved total and pending change requests.

    Optional status filter: draft, sent, revision_requested, approved.
    """
    wid = _workspace_id(ctx)
    with get_session() as s:
        q = select(Agreement).where(Agreement.workspace_id == wid).order_by(Agreement.updated_at.desc())
        if status:
            q = q.where(Agreement.status == status)
        return [services.summary(s, a) for a in s.exec(q)]


@mcp.tool()
def get_agreement(ctx: Context, agreement_id: int) -> dict:
    """Get one agreement in full: scope, versions sent, client responses and change requests."""
    wid = _workspace_id(ctx)
    with get_session() as s:
        try:
            a = services.get_agreement(s, wid, agreement_id)
        except services.NotFound as e:
            raise ToolError(str(e))
        out = services.detail(s, a)
        out.update(_links(ctx, a))
        return out


# Streamable HTTP app. DNS-rebinding protection is off because the server is public and
# every request is authorised by the workspace key instead.
mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    host="0.0.0.0",
)


class WorkspaceKeyRouter:
    """ASGI app mounted at /mcp: /mcp/<key>[/...] -> validates key, forwards to the MCP app at /mcp."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await mcp_http_app(scope, receive, send)
        path = scope.get("path", "")
        # Under a FastAPI Mount, scope["path"] is the full path; strip the mount prefix.
        rel = path[len(scope.get("root_path", "")):] if path.startswith(scope.get("root_path", "")) else path
        parts = [p for p in rel.split("/") if p]
        if parts and parts[0] == "mcp":
            parts = parts[1:]
        key = parts[0] if parts else ""
        with get_session() as s:
            ok = bool(key) and s.exec(select(Workspace).where(Workspace.mcp_key == key)).first() is not None
        if not ok:
            body = b'{"error":"unknown or missing workspace key"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return
        new_scope = dict(scope)
        new_scope["path"] = "/mcp"
        new_scope["raw_path"] = b"/mcp"
        new_scope["root_path"] = ""
        new_scope["headers"] = [h for h in scope["headers"] if h[0].lower() != KEY_HEADER.encode()] + [
            (KEY_HEADER.encode(), key.encode())
        ]
        await mcp_http_app(new_scope, receive, send)
