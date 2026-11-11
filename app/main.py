import contextlib
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote as urlquote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from starlette.middleware.sessions import SessionMiddleware

from app import services
from app.auth import LoginRequired, check_password, current_workspace_id, require_login
from app.db import get_session, init_db
from app.mcp_server import WorkspaceKeyRouter, mcp
from app.models import (Engagement, Negotiation, Offer, Quotation, SourcingRequest, Supplier, Workspace)
from app.money import format_inr, inr_to_paise, pct
from app.seed import DEMO_EMAIL, seed


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed()
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="QuoteLoop", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
                   same_site="lax", https_only=bool(os.environ.get("RENDER")))
app.mount("/mcp", WorkspaceKeyRouter())

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
STATUS = {
    "comparing": ("Comparing quotes", "bg-slate-50 text-slate-700 border-slate-200"),
    "negotiating": ("Negotiating", "bg-amber-50 text-amber-800 border-amber-200"),
    "agreed": ("Agreed", "bg-emerald-50 text-emerald-800 border-emerald-200"),
    "closed": ("Closed", "bg-slate-50 text-slate-500 border-slate-200"),
    "draft": ("Not sent yet", "bg-slate-50 text-slate-700 border-slate-200"),
    "awaiting_supplier": ("Waiting on supplier", "bg-amber-50 text-amber-800 border-amber-200"),
    "awaiting_buyer": ("Supplier replied: your move", "bg-brand-50 text-brand-700 border-brand-100"),
    "rejected": ("Rejected", "bg-rose-50 text-rose-700 border-rose-200"),
}
templates.env.globals.update(inr=format_inr, status=lambda k: STATUS.get(k, (k, "bg-slate-100 text-slate-700")))
CATEGORY = {"av equipment": "bg-indigo-50 text-indigo-700 border-indigo-200", "printing": "bg-cyan-50 text-cyan-700 border-cyan-200",
            "catering": "bg-orange-50 text-orange-700 border-orange-200", "décor": "bg-rose-50 text-rose-700 border-rose-200",
            "decor": "bg-rose-50 text-rose-700 border-rose-200"}
ACCENT = {"action": "border-l-brand-600", "waiting": "border-l-amber-400", "agreed": "border-l-emerald-500",
          "closed": "border-l-slate-200"}
templates.env.globals.update(cat_cls=lambda c: CATEGORY.get((c or "").strip().lower(), "bg-slate-50 text-slate-600 border-slate-200"),
                             accent=lambda k: ACCENT.get(k, "border-l-slate-200"))

RATE = defaultdict(deque)


def rate_limited(key: str, limit: int = 10, window: int = 60) -> bool:
    q, t = RATE[key], time.time()
    while q and q[0] < t - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(t)
    return False


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


def render(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


def base_url(request: Request) -> str:
    env = os.environ.get("PUBLIC_BASE_URL")
    if env:
        return env.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"


def to_int(v):
    try:
        return int(v) if str(v).strip() != "" else None
    except ValueError:
        return None


# ---------- public ----------
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if current_workspace_id(request):
        return RedirectResponse("/dashboard", status_code=303)
    return render(request, "home.html")


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


# ---------- buyer ----------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    wid = require_login(request)
    with get_session() as s:
        ws = s.get(Workspace, wid)
        rows = _request_rows(s, wid)
        req_ids = [x["r"].id for x in rows]
        activity = []
        if req_ids:
            negs = {n.id: n for n in s.exec(select(Negotiation).where(Negotiation.request_id.in_(req_ids)))}
            for o in s.exec(select(Offer).where(Offer.negotiation_id.in_(list(negs) or [0])).order_by(Offer.id.desc()).limit(5)):
                n = negs[o.negotiation_id]
                activity.append({"o": o, "n": n, "supplier": s.get(Supplier, n.supplier_id).name,
                                 "request": s.get(SourcingRequest, n.request_id).title})
        scorecard = services.supplier_scorecard(s, wid)
    active = sorted([x for x in rows if x["r"].status in ("comparing", "negotiating")],
                    key=lambda x: (0 if x["state"] == "action" else 1, -x["r"].id))
    agreed = [x for x in rows if x["r"].status == "agreed"]
    stats = {"active": len(active), "open": sum(x["your_turn"] + x["waiting"] for x in rows),
             "your_turn": sum(1 for x in rows if x["state"] == "action"),
             "spend": sum(x["spend"] for x in scorecard), "saved": sum(x["saved"] for x in scorecard),
             "jobs": sum(x["jobs"] for x in scorecard)}
    return render(request, "dashboard.html", ws=ws, active=active, agreed=agreed, stats=stats, activity=activity,
                  scorecard=scorecard, agreed_total=sum(x["r"].agreed_price_paise or 0 for x in agreed))


def _request_rows(s, wid):
    rows = []
    for r in s.exec(select(SourcingRequest).where(SourcingRequest.workspace_id == wid).order_by(SourcingRequest.id.desc())):
        qs = services.quotations(s, r.id)
        negs = list(s.exec(select(Negotiation).where(Negotiation.request_id == r.id)))
        your_turn = sum(1 for n in negs if n.status == "awaiting_buyer")
        first = services.quote_for(s, r.id, r.agreed_supplier_id) if r.agreed_supplier_id else None
        rows.append({"r": r, "quotes": len(qs), "lowest": min((q.total_paise for q in qs), default=None),
                     "your_turn": your_turn, "waiting": sum(1 for n in negs if n.status == "awaiting_supplier"),
                     "saved": (first.total_paise - r.agreed_price_paise) if first and r.agreed_price_paise else 0,
                     "agreed_with": s.get(Supplier, r.agreed_supplier_id).name if r.agreed_supplier_id else None,
                     "state": ("agreed" if r.status == "agreed" else "closed" if r.status == "closed"
                               else "action" if your_turn or not any(n.status == "awaiting_supplier" for n in negs)
                               else "waiting")})
    return rows


@app.get("/requests", response_class=HTMLResponse)
def all_requests(request: Request, status: str = ""):
    wid = require_login(request)
    with get_session() as s:
        rows = _request_rows(s, wid)
    if status:
        rows = [x for x in rows if x["r"].status == status]
    return render(request, "requests.html", rows=rows, status=status)


@app.get("/requests/new", response_class=HTMLResponse)
def new_request_page(request: Request):
    require_login(request)
    return render(request, "request_new.html")


@app.post("/requests/new")
def new_request(request: Request, title: str = Form(...), category: str = Form(""), specifications: str = Form(""),
                quantity: int = Form(1), unit: str = Form("event package"), needed_by: str = Form("")):
    wid = require_login(request)
    with get_session() as s:
        r = SourcingRequest(workspace_id=wid, title=title.strip(), category=category.strip(),
                            specifications=specifications.strip(), quantity=max(1, quantity),
                            unit=unit.strip() or "unit", needed_by=needed_by.strip())
        s.add(r)
        s.commit()
        s.refresh(r)
        return RedirectResponse(f"/requests/{r.id}", status_code=303)


@app.get("/requests/{rid}", response_class=HTMLResponse)
def request_page(request: Request, rid: int):
    wid = require_login(request)
    with get_session() as s:
        try:
            r = services.get_request(s, wid, rid)
        except services.NotFound:
            return RedirectResponse("/dashboard", status_code=303)
        rows = services.comparison(s, r)
        for x in rows:
            x["levers"] = services.negotiation_levers(s, r, x["supplier"])
        agreed = None
        if r.agreed_supplier_id:
            first = services.quote_for(s, r.id, r.agreed_supplier_id)
            eng = s.exec(select(Engagement).where(Engagement.request_id == r.id)).first()
            agreed = {"supplier": s.get(Supplier, r.agreed_supplier_id), "first": first,
                      "saved": first.total_paise - r.agreed_price_paise if first else 0,
                      "saved_pct": pct(first.total_paise - r.agreed_price_paise, first.total_paise) if first else None,
                      "engagement": eng}
        return render(request, "request.html", r=r, rows=rows, agreed=agreed)


@app.post("/requests/{rid}/quotes")
def add_quote(request: Request, rid: int, supplier_name: str = Form(...), base_inr: str = Form("0"),
              delivery_inr: str = Form("0"), tax_inr: str = Form("0"), quantity: str = Form(""), unit: str = Form(""),
              lead_time_days: str = Form(""), payment_terms: str = Form(""), scope: str = Form(""),
              exclusions: str = Form("")):
    wid = require_login(request)
    with get_session() as s:
        r = services.get_request(s, wid, rid)
        sup = services.get_or_create_supplier(s, wid, supplier_name, category=r.category)
        s.add(Quotation(request_id=r.id, supplier_id=sup.id, base_paise=inr_to_paise(base_inr or 0),
                        delivery_paise=inr_to_paise(delivery_inr or 0), tax_paise=inr_to_paise(tax_inr or 0),
                        quantity=to_int(quantity) or r.quantity, unit=unit.strip() or r.unit,
                        lead_time_days=to_int(lead_time_days), payment_terms=payment_terms.strip(),
                        scope=scope.strip(), exclusions=exclusions.strip(), verified=True))
        s.commit()
    return RedirectResponse(f"/requests/{rid}", status_code=303)


def _owned_quote(s, wid, qid):
    q = s.get(Quotation, qid)
    if not q:
        raise services.NotFound("Quote not found")
    r = services.get_request(s, wid, q.request_id)
    return q, r


@app.get("/quotes/{qid}/edit", response_class=HTMLResponse)
def edit_quote_page(request: Request, qid: int):
    wid = require_login(request)
    with get_session() as s:
        q, r = _owned_quote(s, wid, qid)
        return render(request, "quote_edit.html", q=q, r=r, supplier=s.get(Supplier, q.supplier_id))


@app.post("/quotes/{qid}/edit")
def edit_quote(request: Request, qid: int, base_inr: str = Form("0"), delivery_inr: str = Form("0"),
               tax_inr: str = Form("0"), quantity: str = Form(""), unit: str = Form(""), lead_time_days: str = Form(""),
               payment_terms: str = Form(""), scope: str = Form(""), exclusions: str = Form("")):
    wid = require_login(request)
    with get_session() as s:
        q, r = _owned_quote(s, wid, qid)
        if r.status == "agreed":
            return RedirectResponse(f"/requests/{r.id}", status_code=303)
        q.base_paise, q.delivery_paise, q.tax_paise = (inr_to_paise(base_inr or 0), inr_to_paise(delivery_inr or 0),
                                                       inr_to_paise(tax_inr or 0))
        q.quantity = to_int(quantity) or r.quantity
        q.unit = unit.strip() or r.unit
        q.lead_time_days = to_int(lead_time_days)
        q.payment_terms, q.scope, q.exclusions = payment_terms.strip(), scope.strip(), exclusions.strip()
        q.verified = True
        s.add(q)
        s.commit()
        return RedirectResponse(f"/requests/{r.id}?saved=1", status_code=303)


@app.post("/requests/{rid}/delete")
def delete_request(request: Request, rid: int):
    wid = require_login(request)
    with get_session() as s:
        services.delete_request(s, services.get_request(s, wid, rid))
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/requests/{rid}/suppliers/{sid}/remove")
def remove_supplier(request: Request, rid: int, sid: int):
    wid = require_login(request)
    with get_session() as s:
        r = services.get_request(s, wid, rid)
        try:
            services.remove_supplier(s, r, sid)
        except services.InvalidState:
            pass
    return RedirectResponse(f"/requests/{rid}", status_code=303)


@app.post("/negotiations/{nid}/log-reply")
def log_reply(request: Request, nid: int, price_inr: str = Form(...), delivery: str = Form(""), message: str = Form(""),
              via: str = Form("WhatsApp")):
    wid = require_login(request)
    with get_session() as s:
        n = services.get_negotiation(s, wid, nid)
        try:
            services.log_supplier_reply(s, n, price_paise=inr_to_paise(price_inr or 0), delivery=delivery, message=message,
                                        via=via if via in ("WhatsApp", "phone", "email") else "WhatsApp")
        except services.InvalidState as e:
            return RedirectResponse(f"/negotiations/{nid}?error={urlquote(str(e))}", status_code=303)
    return RedirectResponse(f"/negotiations/{nid}", status_code=303)


@app.post("/requests/{rid}/negotiate/{sid}")
def start_negotiation(request: Request, rid: int, sid: int):
    wid = require_login(request)
    with get_session() as s:
        r = services.get_request(s, wid, rid)
        services.get_supplier(s, wid, sid)
        n = services.get_or_create_negotiation(s, r, sid)
        return RedirectResponse(f"/negotiations/{n.id}", status_code=303)


@app.get("/negotiations/{nid}", response_class=HTMLResponse)
def negotiation_page(request: Request, nid: int, error: str = ""):
    wid = require_login(request)
    with get_session() as s:
        try:
            n = services.get_negotiation(s, wid, nid)
        except services.NotFound:
            return RedirectResponse("/dashboard", status_code=303)
        r = s.get(SourcingRequest, n.request_id)
        sup = s.get(Supplier, n.supplier_id)
        q = services.quote_for(s, r.id, sup.id)
        offers = services.timeline(s, n.id)
        draft = services.latest_draft(s, n.id)
        last_sup = services.latest_offer(s, n.id, "supplier")
        last_buy = services.latest_offer(s, n.id, "buyer")
        hist = services.history_summary(s, wid, sup.id)
        lv = services.negotiation_levers(s, r, sup)
        link = f"{base_url(request)}/s/{n.token}"
        msg = (f"Hi, here is our offer for {r.title}. Please review and respond here: {link}")
        prefill = None
        if n.status in ("draft", "awaiting_buyer"):
            if draft:
                prefill = {"price": draft.price_paise // 100, "delivery": draft.delivery_commitment,
                           "terms": draft.terms, "message": draft.message}
            else:
                start = last_sup.price_paise if last_sup else (lv["target_paise"] or q.total_paise)
                prefill = {"price": start // 100,
                           "delivery": r.needed_by and f"By {r.needed_by}" or "", "terms": q.payment_terms, "message": ""}
        return render(request, "negotiation.html", n=n, r=r, sup=sup, q=q, offers=offers, draft=draft,
                      last_sup=last_sup, last_buy=last_buy, hist=hist, lv=lv, link=link, prefill=prefill, error=error,
                      wa=f"https://wa.me/?text={urlquote(msg)}",
                      mail=f"mailto:?subject={urlquote('Offer: ' + r.title)}&body={urlquote(msg)}")


@app.post("/negotiations/{nid}/offer")
def send_offer(request: Request, nid: int, price_inr: str = Form(...), delivery: str = Form(""), terms: str = Form(""),
               message: str = Form("")):
    wid = require_login(request)
    with get_session() as s:
        n = services.get_negotiation(s, wid, nid)
        try:
            services.buyer_send_offer(s, n, price_paise=inr_to_paise(price_inr or 0), delivery=delivery, terms=terms,
                                      message=message)
        except services.InvalidState as e:
            return RedirectResponse(f"/negotiations/{nid}?error={urlquote(str(e))}", status_code=303)
    return RedirectResponse(f"/negotiations/{nid}?sent=1", status_code=303)


@app.post("/negotiations/{nid}/accept")
def accept_offer(request: Request, nid: int):
    wid = require_login(request)
    with get_session() as s:
        n = services.get_negotiation(s, wid, nid)
        try:
            services.buyer_accept(s, n)
        except services.InvalidState as e:
            return RedirectResponse(f"/negotiations/{nid}?error={urlquote(str(e))}", status_code=303)
        return RedirectResponse(f"/requests/{n.request_id}?agreed=1", status_code=303)


@app.post("/negotiations/{nid}/reject")
def reject_offer(request: Request, nid: int):
    wid = require_login(request)
    with get_session() as s:
        n = services.get_negotiation(s, wid, nid)
        try:
            services.buyer_reject(s, n)
        except services.InvalidState:
            pass
    return RedirectResponse(f"/negotiations/{nid}", status_code=303)


@app.post("/engagements/{eid}/outcome")
def record_outcome(request: Request, eid: int, on_time: str = Form(""), price_honored: str = Form(""),
                   scope_honored: str = Form(""), rating: str = Form(""), notes: str = Form(""), back: str = Form("/")):
    wid = require_login(request)
    yn = lambda v: True if v == "yes" else False if v == "no" else None
    with get_session() as s:
        services.record_outcome(s, wid, eid, on_time=yn(on_time), price_honored=yn(price_honored),
                                scope_honored=yn(scope_honored), rating=to_int(rating), notes=notes)
    return RedirectResponse(back if back.startswith("/") else "/", status_code=303)


@app.get("/suppliers/{sid}", response_class=HTMLResponse)
def supplier_page(request: Request, sid: int):
    wid = require_login(request)
    with get_session() as s:
        sup = services.get_supplier(s, wid, sid)
        return render(request, "supplier.html", sup=sup, hist=services.history_summary(s, wid, sid))


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    wid = require_login(request)
    with get_session() as s:
        ws = s.get(Workspace, wid)
    return render(request, "settings.html", ws=ws, mcp_url=f"{base_url(request)}/mcp/{ws.mcp_key}")


# ---------- supplier (private link, no account) ----------
def _neg_by_token(s, token):
    return s.exec(select(Negotiation).where(Negotiation.token == token)).first()


@app.get("/s/{token}", response_class=HTMLResponse)
def supplier_link(request: Request, token: str, done: str = "", error: str = ""):
    with get_session() as s:
        n = _neg_by_token(s, token)
        if not n or (n.token_revoked and n.status != "agreed"):
            return render(request, "supplier_missing.html")
        r = s.get(SourcingRequest, n.request_id)
        sup = s.get(Supplier, n.supplier_id)
        ws = s.get(Workspace, r.workspace_id)
        q = services.quote_for(s, r.id, sup.id)  # only THIS supplier's own quote is shown
        offers = services.timeline(s, n.id)
        current = services.latest_offer(s, n.id, "buyer")
        return render(request, "supplier_link.html", n=n, r=r, sup=sup, ws=ws, q=q, offers=offers,
                      current=current, done=done, error=error)


@app.post("/s/{token}/respond")
def supplier_respond(request: Request, token: str, action: str = Form(...), name: str = Form(""),
                     price_inr: str = Form(""), delivery: str = Form(""), message: str = Form("")):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    if rate_limited(f"{token}:{ip}"):
        return RedirectResponse(f"/s/{token}?error={urlquote('Too many attempts. Please wait a minute.')}", status_code=303)
    with get_session() as s:
        n = _neg_by_token(s, token)
        if not n:
            return RedirectResponse(f"/s/{token}", status_code=303)
        try:
            services.supplier_respond(s, n, action=action, name=name, price_paise=inr_to_paise(price_inr or 0),
                                      delivery=delivery, message=message)
        except services.InvalidState as e:
            return RedirectResponse(f"/s/{token}?error={urlquote(str(e))}", status_code=303)
    return RedirectResponse(f"/s/{token}?done={action}", status_code=303)
