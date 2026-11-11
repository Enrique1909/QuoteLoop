import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from starlette.middleware.sessions import SessionMiddleware

from app import services
from app.auth import LoginRequired, check_password, current_workspace_id, require_login
from app.db import get_session, init_db
from app.mcp_server import WorkspaceKeyRouter, mcp
from app.models import Agreement, Workspace
from app.money import approved_total, final_deadline, format_inr, inr_to_paise
from app.seed import DEMO_EMAIL, seed


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="ClientRoom", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
                   same_site="lax", https_only=bool(os.environ.get("RENDER")))
app.mount("/mcp", WorkspaceKeyRouter())

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["inr"] = format_inr

STATUS_LABELS = {
    "draft": "Draft", "sent": "Awaiting client", "revision_requested": "Revision requested",
    "approved": "Approved", "pending": "Awaiting client", "declined": "Declined",
}
templates.env.globals["status_label"] = lambda s: STATUS_LABELS.get(s, s)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


def render(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def lines(text: str) -> list[str]:
    return [l.strip() for l in (text or "").splitlines() if l.strip()]


def parse_milestones(text: str) -> list[dict]:
    out = []
    for l in lines(text):
        name, _, due = l.partition("|")
        out.append({"name": name.strip(), "due_date": due.strip()})
    return out


def base_url(request: Request) -> str:
    env = os.environ.get("PUBLIC_BASE_URL")
    if env:
        return env.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


# ---------- health & landing ----------
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if current_workspace_id(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "home.html", demo_email=DEMO_EMAIL)


# ---------- auth ----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", error=None, demo_email=DEMO_EMAIL)


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    with get_session() as s:
        ws = s.exec(select(Workspace).where(Workspace.login_email == email.strip().lower())).first()
    if not ws or not check_password(password, ws.password_hash):
        return render(request, "login.html", error="Wrong email or password.", demo_email=DEMO_EMAIL)
    request.session["workspace_id"] = ws.id
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------- agency ----------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    wid = require_login(request)
    with get_session() as s:
        ws = s.get(Workspace, wid)
        rows = [services.summary(s, a) | {"created_via": a.created_via} for a in
                s.exec(select(Agreement).where(Agreement.workspace_id == wid).order_by(Agreement.updated_at.desc()))]
    totals = {
        "approved": sum(1 for r in rows if r["status"] == "approved"),
        "awaiting": sum(1 for r in rows if r["status"] == "sent"),
        "pending_crs": sum(r["pending_change_requests"] for r in rows),
    }
    return render(request, "dashboard.html", ws=ws, rows=rows, totals=totals)


@app.get("/agreements/new", response_class=HTMLResponse)
def new_agreement(request: Request):
    require_login(request)
    return render(request, "edit.html", a=None)


def _form_fields(client_name, client_email, project_title, summary, deliverables, exclusions, milestones,
                 revision_limit, price_inr):
    return dict(client_name=client_name, client_email=client_email, project_title=project_title, summary=summary,
                deliverables=lines(deliverables), exclusions=lines(exclusions), milestones=parse_milestones(milestones),
                revision_limit=int(revision_limit or 0), base_price_paise=inr_to_paise(price_inr or 0))


@app.post("/agreements/new")
def create_agreement(request: Request, client_name: str = Form(...), client_email: str = Form(""),
                     project_title: str = Form(...), summary: str = Form(""), deliverables: str = Form(""),
                     exclusions: str = Form(""), milestones: str = Form(""), revision_limit: int = Form(2),
                     price_inr: float = Form(0)):
    wid = require_login(request)
    f = _form_fields(client_name, client_email, project_title, summary, deliverables, exclusions, milestones,
                     revision_limit, price_inr)
    with get_session() as s:
        price = f.pop("base_price_paise")
        a = services.create_agreement(s, wid, price_paise=price, **f)
        return RedirectResponse(f"/agreements/{a.id}", status_code=303)


@app.get("/agreements/{aid}", response_class=HTMLResponse)
def agreement_page(request: Request, aid: int):
    wid = require_login(request)
    with get_session() as s:
        try:
            a = services.get_agreement(s, wid, aid)
        except services.NotFound:
            return RedirectResponse("/dashboard", status_code=303)
        crs = services.change_requests(s, aid)
        vs = services.versions(s, aid)
        return render(request, "agreement.html", a=a, crs=crs, versions=vs,
                      total=approved_total(a, crs), deadline=final_deadline(a, crs),
                      client_url=f"{base_url(request)}/c/{a.share_token}")


@app.get("/agreements/{aid}/edit", response_class=HTMLResponse)
def edit_page(request: Request, aid: int):
    wid = require_login(request)
    with get_session() as s:
        a = services.get_agreement(s, wid, aid)
        return render(request, "edit.html", a=a)


@app.post("/agreements/{aid}/edit")
def edit_agreement(request: Request, aid: int, client_name: str = Form(...), client_email: str = Form(""),
                   project_title: str = Form(...), summary: str = Form(""), deliverables: str = Form(""),
                   exclusions: str = Form(""), milestones: str = Form(""), revision_limit: int = Form(2),
                   price_inr: float = Form(0)):
    wid = require_login(request)
    with get_session() as s:
        a = services.get_agreement(s, wid, aid)
        services.update_agreement(s, a, **_form_fields(client_name, client_email, project_title, summary,
                                                       deliverables, exclusions, milestones, revision_limit, price_inr))
    return RedirectResponse(f"/agreements/{aid}", status_code=303)


@app.post("/agreements/{aid}/send")
def send_agreement(request: Request, aid: int):
    wid = require_login(request)
    with get_session() as s:
        services.send_agreement(s, services.get_agreement(s, wid, aid))
    return RedirectResponse(f"/agreements/{aid}?sent=1", status_code=303)


@app.post("/agreements/{aid}/change-requests")
def add_change_request(request: Request, aid: int, description: str = Form(...), extra_price_inr: float = Form(0),
                       extra_days: int = Form(0)):
    wid = require_login(request)
    with get_session() as s:
        a = services.get_agreement(s, wid, aid)
        try:
            services.create_change_request(s, a, description=description, extra_price_paise=inr_to_paise(extra_price_inr),
                                           extra_days=extra_days)
        except ValueError:
            pass
    return RedirectResponse(f"/agreements/{aid}", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    wid = require_login(request)
    with get_session() as s:
        ws = s.get(Workspace, wid)
    return render(request, "settings.html", ws=ws, mcp_url=f"{base_url(request)}/mcp/{ws.mcp_key}")


# ---------- client (no login) ----------
def _by_token(s, token: str):
    return s.exec(select(Agreement).where(Agreement.share_token == token)).first()


@app.get("/c/{token}", response_class=HTMLResponse)
def client_page(request: Request, token: str, done: str = ""):
    with get_session() as s:
        a = _by_token(s, token)
        if not a or a.status == "draft" and not services.versions(s, a.id):
            return render(request, "client_missing.html")
        v = services.latest_version(s, a.id)
        crs = services.change_requests(s, a.id)
        ws = s.get(Workspace, a.workspace_id)
        scope = v.snapshot if v else {}
        return render(request, "client.html", a=a, v=v, scope=scope, crs=crs, ws=ws, done=done,
                      total=approved_total(a, crs), deadline=final_deadline(a, crs),
                      editing=(a.status == "draft"))


@app.post("/c/{token}/respond")
def client_respond(request: Request, token: str, decision: str = Form(...), name: str = Form(...),
                   comment: str = Form("")):
    with get_session() as s:
        a = _by_token(s, token)
        if a:
            try:
                services.client_respond(s, a, approve=(decision == "approve"), name=name, comment=comment)
            except ValueError:
                pass
    return RedirectResponse(f"/c/{token}?done={decision}", status_code=303)


@app.post("/c/{token}/change-requests/{cr_id}")
def client_change_request(request: Request, token: str, cr_id: int, decision: str = Form(...), name: str = Form(...)):
    with get_session() as s:
        a = _by_token(s, token)
        if a:
            try:
                services.respond_change_request(s, a, cr_id, approve=(decision == "approve"), name=name)
            except ValueError:
                pass
    return RedirectResponse(f"/c/{token}?done=cr-{decision}", status_code=303)
