"""QuoteLoop business logic, shared by the web app and the MCP server. No AI runs here."""
from datetime import date, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models import (Engagement, Negotiation, NegotiationDraft, Offer, Quotation, SourcingRequest, Supplier,
                        now)
from app.money import format_inr, pct


class NotFound(Exception):
    pass


class InvalidState(Exception):
    pass


# ---------- suppliers & requests ----------
def find_supplier(s: Session, wid: int, name: str) -> Optional[Supplier]:
    name = (name or "").strip().lower()
    for sup in s.exec(select(Supplier).where(Supplier.workspace_id == wid)):
        if sup.name.strip().lower() == name:
            return sup
    return None


def get_or_create_supplier(s: Session, wid: int, name: str, contact: str = "", category: str = "") -> Supplier:
    sup = find_supplier(s, wid, name)
    if sup:
        return sup
    sup = Supplier(workspace_id=wid, name=name.strip(), contact=contact or "", category=category or "")
    s.add(sup)
    s.commit()
    s.refresh(sup)
    return sup


def get_request(s: Session, wid: int, rid: int) -> SourcingRequest:
    r = s.get(SourcingRequest, rid)
    if not r or r.workspace_id != wid:
        raise NotFound(f"Sourcing request {rid} not found in this workspace")
    return r


def get_supplier(s: Session, wid: int, sid: int) -> Supplier:
    sup = s.get(Supplier, sid)
    if not sup or sup.workspace_id != wid:
        raise NotFound(f"Supplier {sid} not found in this workspace")
    return sup


def quotations(s: Session, rid: int) -> list[Quotation]:
    return list(s.exec(select(Quotation).where(Quotation.request_id == rid).order_by(Quotation.id)))


def quote_for(s: Session, rid: int, sid: int) -> Optional[Quotation]:
    return s.exec(select(Quotation).where(Quotation.request_id == rid, Quotation.supplier_id == sid)
                  .order_by(Quotation.id)).first()


# ---------- comparison (deterministic) ----------
def quote_flags(req: SourcingRequest, q: Quotation) -> list[str]:
    flags = []
    if q.quantity != req.quantity or (q.unit or "").strip().lower() != (req.unit or "").strip().lower():
        flags.append(f"Quoted {q.quantity} {q.unit} vs requested {req.quantity} {req.unit}: not directly comparable")
    if q.lead_time_days is None:
        flags.append("Lead time missing")
    if not (q.payment_terms or "").strip():
        flags.append("Payment terms missing")
    if q.tax_paise == 0:
        flags.append("Tax not stated (total may exclude GST)")
    return flags


def history_summary(s: Session, wid: int, sid: int, category: str = "") -> dict:
    rows = list(s.exec(select(Engagement).where(Engagement.workspace_id == wid, Engagement.supplier_id == sid)
                       .order_by(Engagement.date.desc())))
    recorded = [e for e in rows if e.on_time is not None]
    rated = [e.rating for e in rows if e.rating]
    discounts = [pct(e.quoted_paise - e.agreed_paise, e.quoted_paise) for e in rows if e.quoted_paise]
    return {
        "engagements": len(rows),
        "on_time_rate": pct(sum(1 for e in recorded if e.on_time), len(recorded)) if recorded else None,
        "late_count": sum(1 for e in recorded if e.on_time is False),
        "avg_rating": round(sum(rated) / len(rated), 1) if rated else None,
        "avg_discount_pct": round(sum(float(d) for d in discounts) / len(discounts), 1) if discounts else None,
        "issues": [e.notes for e in rows if e.notes and (e.on_time is False or e.price_honored is False
                                                          or e.scope_honored is False)],
        "rows": rows,
    }


def comparison(s: Session, req: SourcingRequest) -> list[dict]:
    rows = []
    for q in quotations(s, req.id):
        sup = s.get(Supplier, q.supplier_id)
        flags = quote_flags(req, q)
        comparable = not any("not directly comparable" in f for f in flags)
        rows.append({"q": q, "supplier": sup, "total": q.total_paise, "flags": flags, "comparable": comparable,
                     "unit_price": q.total_paise // q.quantity if q.quantity else None,
                     "history": history_summary(s, req.workspace_id, sup.id),
                     "negotiation": s.exec(select(Negotiation).where(Negotiation.request_id == req.id,
                                                                     Negotiation.supplier_id == sup.id)).first()})
    comparable = [r for r in rows if r["comparable"]]
    if comparable:
        low = min(r["total"] for r in comparable)
        for r in comparable:
            r["cheapest"] = r["total"] == low
            r["gap_pct"] = pct(r["total"] - low, low) if low else None
        lead = [r for r in comparable if r["q"].lead_time_days is not None]
        if lead:
            fast = min(r["q"].lead_time_days for r in lead)
            for r in lead:
                r["fastest"] = r["q"].lead_time_days == fast
    return rows


# ---------- negotiation state machine ----------
def get_negotiation(s: Session, wid: int, nid: int) -> Negotiation:
    n = s.get(Negotiation, nid)
    if not n:
        raise NotFound(f"Negotiation {nid} not found")
    get_request(s, wid, n.request_id)  # ownership check
    return n


def get_or_create_negotiation(s: Session, req: SourcingRequest, sid: int) -> Negotiation:
    n = s.exec(select(Negotiation).where(Negotiation.request_id == req.id, Negotiation.supplier_id == sid)).first()
    if n:
        return n
    if not quote_for(s, req.id, sid):
        raise InvalidState("This supplier has no quotation on this request.")
    n = Negotiation(request_id=req.id, supplier_id=sid)
    s.add(n)
    s.commit()
    s.refresh(n)
    return n


def timeline(s: Session, nid: int) -> list[Offer]:
    return list(s.exec(select(Offer).where(Offer.negotiation_id == nid).order_by(Offer.id)))


def latest_offer(s: Session, nid: int, author: Optional[str] = None) -> Optional[Offer]:
    offers = [o for o in timeline(s, nid) if o.kind == "offer" and (author is None or o.author == author)]
    return offers[-1] if offers else None


def latest_draft(s: Session, nid: int) -> Optional[NegotiationDraft]:
    return s.exec(select(NegotiationDraft).where(NegotiationDraft.negotiation_id == nid)
                  .order_by(NegotiationDraft.id.desc())).first()


def _touch(s, n, status, req=None):
    n.status = status
    n.updated_at = now()
    s.add(n)
    if req is not None:
        s.add(req)
    s.commit()


def buyer_send_offer(s: Session, n: Negotiation, *, price_paise: int, delivery: str, terms: str, message: str) -> Offer:
    req = s.get(SourcingRequest, n.request_id)
    if req.status == "agreed" or n.status not in ("draft", "awaiting_buyer"):
        raise InvalidState("You can only send an offer when it's your turn and the request isn't agreed yet.")
    if price_paise <= 0:
        raise InvalidState("Offer price must be more than zero.")
    o = Offer(negotiation_id=n.id, author="buyer", price_paise=price_paise, delivery_commitment=delivery.strip(),
              terms=terms.strip(), message=message.strip(), author_name="Buyer")
    s.add(o)
    req.status = "negotiating"
    _touch(s, n, "awaiting_supplier", req)
    return o


def _finalize(s: Session, n: Negotiation, price_paise: int):
    req = s.get(SourcingRequest, n.request_id)
    if req.status == "agreed":
        raise InvalidState("This request already has an agreed supplier.")
    req.status = "agreed"
    req.agreed_supplier_id = n.supplier_id
    req.agreed_price_paise = price_paise
    req.agreed_at = now()
    n.token_revoked = False
    _touch(s, n, "agreed", req)
    for other in s.exec(select(Negotiation).where(Negotiation.request_id == req.id, Negotiation.id != n.id)):
        if other.status not in ("agreed", "rejected"):
            other.status = "closed"
        other.token_revoked = True
        s.add(other)
    q = quote_for(s, req.id, n.supplier_id)
    s.add(Engagement(workspace_id=req.workspace_id, supplier_id=n.supplier_id, request_id=req.id,
                     date=date.today().isoformat(), item=req.title, specifications=req.specifications,
                     quantity=req.quantity, unit=req.unit, quoted_paise=q.total_paise if q else price_paise,
                     agreed_paise=price_paise))
    s.commit()


def supplier_respond(s: Session, n: Negotiation, *, action: str, name: str, price_paise: int = 0,
                     delivery: str = "", message: str = "") -> None:
    if n.token_revoked or n.status != "awaiting_supplier":
        raise InvalidState("There is no offer waiting for your response.")
    buyer_offer = latest_offer(s, n.id, "buyer")
    name = (name or "").strip() or "Supplier"
    if action == "accept":
        s.add(Offer(negotiation_id=n.id, author="supplier", kind="accept", price_paise=buyer_offer.price_paise,
                    message=message.strip(), author_name=name))
        _finalize(s, n, buyer_offer.price_paise)
    elif action == "reject":
        s.add(Offer(negotiation_id=n.id, author="supplier", kind="reject", message=message.strip(), author_name=name))
        _touch(s, n, "rejected")
    elif action == "counter":
        if price_paise <= 0:
            raise InvalidState("Enter your counteroffer price.")
        s.add(Offer(negotiation_id=n.id, author="supplier", price_paise=price_paise,
                    delivery_commitment=delivery.strip(), message=message.strip(), author_name=name))
        _touch(s, n, "awaiting_buyer")
    else:
        raise InvalidState("Unknown action.")


def buyer_accept(s: Session, n: Negotiation) -> None:
    if n.status != "awaiting_buyer":
        raise InvalidState("There is no supplier offer to accept.")
    sup_offer = latest_offer(s, n.id, "supplier")
    s.add(Offer(negotiation_id=n.id, author="buyer", kind="accept", price_paise=sup_offer.price_paise,
                author_name="Buyer"))
    _finalize(s, n, sup_offer.price_paise)


def buyer_reject(s: Session, n: Negotiation) -> None:
    if n.status in ("agreed", "rejected"):
        raise InvalidState("This negotiation is already finished.")
    s.add(Offer(negotiation_id=n.id, author="buyer", kind="reject", author_name="Buyer"))
    n.token_revoked = True
    _touch(s, n, "rejected")


def save_draft(s: Session, n: Negotiation, *, price_paise, delivery, terms, message, reasoning, references):
    d = NegotiationDraft(negotiation_id=n.id, price_paise=price_paise, delivery_commitment=delivery or "",
                         terms=terms or "", message=message or "", reasoning=reasoning or "",
                         references=list(references or []))
    s.add(d)
    s.commit()
    s.refresh(d)
    return d


def record_outcome(s: Session, wid: int, eid: int, *, on_time, price_honored, scope_honored, rating, notes):
    e = s.get(Engagement, eid)
    if not e or e.workspace_id != wid:
        raise NotFound("Engagement not found")
    e.on_time, e.price_honored, e.scope_honored = on_time, price_honored, scope_honored
    e.rating, e.notes = rating, notes.strip()
    s.add(e)
    s.commit()


# ---------- read models for MCP ----------
def iso(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def engagement_dict(e: Engagement) -> dict:
    return {"date": e.date, "item": e.item, "specifications": e.specifications, "quantity": e.quantity,
            "unit": e.unit, "quoted": format_inr(e.quoted_paise), "agreed": format_inr(e.agreed_paise),
            "agreed_unit_price": format_inr(e.agreed_paise // e.quantity) if e.quantity else None,
            "discount_from_quote_pct": float(pct(e.quoted_paise - e.agreed_paise, e.quoted_paise) or 0),
            "on_time": e.on_time, "price_honored": e.price_honored, "scope_honored": e.scope_honored,
            "rating": e.rating, "notes": e.notes or None}


def quote_dict(req, q: Quotation, sup: Supplier) -> dict:
    return {"supplier_id": sup.id, "supplier": sup.name, "base": format_inr(q.base_paise),
            "delivery": format_inr(q.delivery_paise), "tax": format_inr(q.tax_paise), "total": format_inr(q.total_paise),
            "quantity": q.quantity, "unit": q.unit, "lead_time_days": q.lead_time_days,
            "payment_terms": q.payment_terms, "scope": q.scope, "exclusions": q.exclusions,
            "flags": quote_flags(req, q)}


def request_dict(s: Session, req: SourcingRequest) -> dict:
    rows = comparison(s, req)
    return {"request_id": req.id, "title": req.title, "category": req.category, "status": req.status,
            "specifications": req.specifications, "quantity": req.quantity, "unit": req.unit, "needed_by": req.needed_by,
            "quotations": [quote_dict(req, r["q"], r["supplier"]) | {"cheapest_comparable": r.get("cheapest", False),
                                                                    "gap_vs_cheapest_pct": float(r["gap_pct"]) if r.get("gap_pct") is not None else None}
                           for r in rows],
            "agreed_supplier_id": req.agreed_supplier_id,
            "agreed_price": format_inr(req.agreed_price_paise) if req.agreed_price_paise else None}


def offer_dict(o: Offer) -> dict:
    return {"author": o.author, "type": o.kind, "price": format_inr(o.price_paise) if o.price_paise else None,
            "delivery_commitment": o.delivery_commitment or None, "terms": o.terms or None,
            "message": o.message or None, "at": iso(o.created_at)}


def supplier_scorecard(s: Session, wid: int) -> list[dict]:
    """Per-supplier totals from recorded engagements: spend, savings, reliability. Pure arithmetic."""
    out = []
    for sup in s.exec(select(Supplier).where(Supplier.workspace_id == wid)):
        rows = list(s.exec(select(Engagement).where(Engagement.supplier_id == sup.id)))
        if not rows:
            continue
        recorded = [e for e in rows if e.on_time is not None]
        rated = [e.rating for e in rows if e.rating]
        spend = sum(e.agreed_paise for e in rows)
        quoted = sum(e.quoted_paise for e in rows)
        out.append({
            "supplier": sup, "jobs": len(rows), "spend": spend, "saved": quoted - spend,
            "saved_pct": pct(quoted - spend, quoted) if quoted else None,
            "on_time_rate": pct(sum(1 for e in recorded if e.on_time), len(recorded)) if recorded else None,
            "late": sum(1 for e in recorded if e.on_time is False),
            "issues": sum(1 for e in recorded if e.on_time is False or e.price_honored is False or e.scope_honored is False),
            "avg_rating": round(sum(rated) / len(rated), 1) if rated else None,
            "last": max(e.date for e in rows),
        })
    return sorted(out, key=lambda x: x["spend"], reverse=True)


# ---------- negotiation levers (fixed rules over the buyer's own data; no AI) ----------
def _round500(paise: int) -> int:
    step = 50000  # ₹500
    return int(round(paise / step)) * step


def negotiation_levers(s: Session, req: SourcingRequest, sup: Supplier) -> dict:
    """Return evidence-based levers and a suggested opening offer.

    Every lever is an if/then rule on recorded quotes, agreements and delivery outcomes.
    """
    from decimal import Decimal

    q = quote_for(s, req.id, sup.id)
    if not q:
        return {"levers": [], "target_paise": None, "target_basis": []}
    hist = history_summary(s, req.workspace_id, sup.id)
    rows = hist["rows"]
    recorded = [e for e in rows if e.on_time is not None]
    levers, basis = [], []
    target = Decimal(q.total_paise)

    # 1. Their usual discount from first quote
    if hist["avg_discount_pct"] and hist["avg_discount_pct"] >= 2:
        d = Decimal(str(hist["avg_discount_pct"]))
        target = target * (1 - d / 100)
        basis.append(f"−{d}% (their usual discount)")
        levers.append({"type": "discount_history", "strength": "strong", "title": f"They usually agree {d}% below their first quote",
                       "evidence": "; ".join(f"{e.date}: quoted {format_inr(e.quoted_paise)} → agreed {format_inr(e.agreed_paise)}"
                                             for e in rows if e.agreed_paise < e.quoted_paise)[:240],
                       "ask": f"Open at about {format_inr(_round500(int(Decimal(q.total_paise) * (1 - d / 100))))} instead of accepting {format_inr(q.total_paise)}."})

    # 2. Late deliveries
    late = [e for e in recorded if e.on_time is False]
    if late:
        target = target * Decimal("0.95")
        basis.append("−5% (late-delivery risk)")
        note = next((e.notes for e in late if e.notes), "")
        levers.append({"type": "late_delivery", "strength": "strong", "title": f"Late on {len(late)} of {len(recorded)} recorded jobs",
                       "evidence": (f"Latest: {late[0].date}, {late[0].item}" + (f" ({note})" if note else "")),
                       "ask": "Ask for a lower price, or a written late-delivery penalty (e.g. 10% off if setup isn't ready on time)."})

    # 3. Scope or price not honoured before
    broken = [e for e in recorded if e.scope_honored is False or e.price_honored is False]
    if broken:
        e = broken[0]
        levers.append({"type": "scope_issue", "strength": "medium", "title": "Didn't deliver the full agreed scope last time",
                       "evidence": f"{e.date}, {e.item}" + (f": {e.notes}" if e.notes else ""),
                       "ask": "Get every deliverable listed in the offer, and ask for a credit if anything is missing."})

    # 4. Cheaper comparable competitor
    comparable = [o for o in quotations(s, req.id) if o.supplier_id != sup.id and not any(
        "not directly comparable" in f for f in quote_flags(req, o))]
    cheaper = sorted([o for o in comparable if o.total_paise < q.total_paise], key=lambda o: o.total_paise)
    if cheaper:
        low = cheaper[0]
        gap = pct(q.total_paise - low.total_paise, q.total_paise)
        levers.append({"type": "competitor_price", "strength": "strong", "title": f"A comparable quote is {gap}% lower",
                       "evidence": f"Lowest comparable total: {format_inr(low.total_paise)} (supplier name stays private)",
                       "ask": f"Tell them you have a lower comparable quote and ask them to match or close the gap."})

    # 5. Better payment terms elsewhere
    credit = [o for o in comparable if "credit" in (o.payment_terms or "").lower()]
    if credit and "advance" in (q.payment_terms or "").lower():
        levers.append({"type": "payment_terms", "strength": "medium", "title": "Another supplier offers credit; this one wants advance",
                       "evidence": f"This quote: {q.payment_terms}. Elsewhere: {credit[0].payment_terms}",
                       "ask": "Ask to reduce the advance (e.g. 30% instead of 50%) or for part-payment after the event."})

    # 6. Delivery charge others include
    if q.delivery_paise > 0 and any(o.delivery_paise == 0 for o in comparable):
        levers.append({"type": "delivery_charge", "strength": "medium", "title": f"Delivery charged at {format_inr(q.delivery_paise)}",
                       "evidence": "A comparable quote includes delivery and setup at no extra cost",
                       "ask": f"Ask them to waive the {format_inr(q.delivery_paise)} delivery charge."})

    # 7. Price vs last job with the same spec (only like-for-like; otherwise flagged as a different spec)
    norm = lambda t: " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in (t or "")).split())
    req_spec = norm(req.specifications)
    same_unit = [e for e in rows if (e.unit or "") == (req.unit or "") and e.quantity == req.quantity]
    same_spec = [e for e in same_unit if norm(e.specifications) and norm(e.specifications) in req_spec]
    if same_spec and q.total_paise > same_spec[0].agreed_paise:
        e = same_spec[0]
        up = pct(q.total_paise - e.agreed_paise, e.agreed_paise)
        levers.append({"type": "price_increase", "strength": "strong" if up >= 5 else "medium",
                       "title": f"{up}% above what you paid them for the same spec",
                       "evidence": f"{e.date}, {e.item}: agreed {format_inr(e.agreed_paise)} ({e.specifications})",
                       "ask": "Ask them to hold last time's price, or explain what changed."})
    elif same_unit and q.total_paise > same_unit[0].agreed_paise:
        e = same_unit[0]
        levers.append({"type": "price_reference", "strength": "light",
                       "title": "Last job with them was a different spec",
                       "evidence": f"{e.date}, {e.item}: agreed {format_inr(e.agreed_paise)} ({e.specifications})",
                       "ask": "Use it as a reference only; ask for a line-by-line price to compare like for like."})

    # 8. Loyalty
    if len(rows) >= 2:
        spend = sum(e.agreed_paise for e in rows)
        levers.append({"type": "loyalty", "strength": "light", "title": f"Repeat customer: {len(rows)} jobs, {format_inr(spend)} so far",
                       "evidence": f"Since {rows[-1].date}", "ask": "Ask for a repeat-customer discount or a free add-on."})

    target_paise = _round500(int(target)) if basis else None
    if target_paise is not None and cheaper and target_paise < int(cheaper[0].total_paise * 0.9):
        target_paise = _round500(int(cheaper[0].total_paise * 0.9))
        basis.append("floored at 10% below the lowest comparable quote")
    order = {"strong": 0, "medium": 1, "light": 2}
    levers.sort(key=lambda l: order[l["strength"]])
    return {"levers": levers, "target_paise": target_paise, "target_basis": basis, "quote_paise": q.total_paise}
