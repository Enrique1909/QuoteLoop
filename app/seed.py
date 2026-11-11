"""Fictional demo data (clearly labelled). One event agency, three AV suppliers with real-looking history."""
import os

from sqlmodel import select

from app import services
from app.auth import check_password, hash_password
from app.db import get_session
from app.models import Engagement, Quotation, SourcingRequest, Supplier, Workspace
from app.money import inr_to_paise as p

DEMO_EMAIL = os.environ.get("DEMO_EMAIL", "demo@quoteloop.app")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "volopay-demo")

HISTORY = {
    # supplier: (contact, [(date, item, specs, qty, unit, quoted, agreed, on_time, price_ok, scope_ok, rating, notes)])
    "SoundWave AV Rentals": ("Arjun · +91 98200 11223", [
        ("2026-08-14", "AV for product launch (sample)", "Line-array PA, 4 wireless mics, 10x8 ft LED wall, 1 operator", 1,
         "event package", 180000, 155000, True, True, True, 4, "Agreed ₹1,55,000 after we shared a competing quote"),
        ("2026-06-20", "AV for investor meet (sample)", "Compact PA, 2 wireless mics, projector", 1, "event package",
         95000, 88000, True, True, True, 4, ""),
    ]),
    "BrightStage Productions": ("Neha · +91 99300 44556", [
        ("2026-09-05", "AV for dealer conference (sample)", "Line-array PA, 4 wireless mics, 12x8 ft LED wall, 1 operator", 1,
         "event package", 170000, 165000, False, True, True, 2, "LED wall arrived 3 hours late; doors delayed 40 min"),
        ("2026-07-11", "AV for awards night (sample)", "PA, 6 wireless mics, LED wall 10x8 ft", 1, "event package",
         160000, 158000, False, True, False, 2, "Lighting rig delivered next morning; 2 mics missing"),
        ("2026-04-18", "AV for team offsite (sample)", "Compact PA, 2 mics", 1, "event package",
         60000, 60000, True, True, True, 4, ""),
    ]),
    "ProLite Event Tech": ("Karan · +91 98190 77889", [
        ("2026-09-26", "AV for leadership summit (sample)", "Line-array PA, 4 wireless mics, 12x8 ft LED wall, 1 operator", 1,
         "event package", 190000, 186000, True, True, True, 5, "Set up 2 hours early; backup mics included"),
        ("2026-05-09", "AV for client dinner (sample)", "Compact PA, 2 wireless mics", 1, "event package",
         70000, 70000, True, True, True, 5, ""),
    ]),
}


def seed() -> None:
    with get_session() as s:
        ws = s.exec(select(Workspace).where(Workspace.login_email == DEMO_EMAIL)).first()
        if ws:
            if os.environ.get("DEMO_PASSWORD") and not check_password(DEMO_PASSWORD, ws.password_hash):
                ws.password_hash = hash_password(DEMO_PASSWORD)
                s.add(ws)
                s.commit()
            return
        ws = Workspace(name="Spotlight Events (demo)", login_email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD))
        if os.environ.get("MCP_KEY"):
            ws.mcp_key = os.environ["MCP_KEY"]
        s.add(ws)
        s.commit()
        s.refresh(ws)

        sups = {}
        for name, (contact, rows) in HISTORY.items():
            sup = Supplier(workspace_id=ws.id, name=name, contact=contact, category="AV equipment", is_sample=True)
            s.add(sup)
            s.commit()
            s.refresh(sup)
            sups[name] = sup
            for d, item, spec, qty, unit, quoted, agreed, on_time, price_ok, scope_ok, rating, notes in rows:
                s.add(Engagement(workspace_id=ws.id, supplier_id=sup.id, date=d, item=item, specifications=spec,
                                 quantity=qty, unit=unit, quoted_paise=p(quoted), agreed_paise=p(agreed), on_time=on_time,
                                 price_honored=price_ok, scope_honored=scope_ok, rating=rating, notes=notes, is_sample=True))
        s.commit()

        # One completed sample request so the dashboard has a finished cycle.
        req = SourcingRequest(workspace_id=ws.id, title="Printed standees and backdrop (sample)", category="Printing",
                              specifications="6 roll-up standees 3x6 ft, 1 backdrop 20x10 ft, flex print, delivered to venue",
                              quantity=1, unit="order", needed_by="2026-11-20", is_sample=True)
        s.add(req)
        s.commit()
        s.refresh(req)
        printer = Supplier(workspace_id=ws.id, name="InkJet Print Hub", contact="Faisal · +91 98330 22110",
                           category="Printing", is_sample=True)
        s.add(printer)
        s.commit()
        s.refresh(printer)
        s.add(Quotation(request_id=req.id, supplier_id=printer.id, base_paise=p(42000), delivery_paise=p(1500),
                        tax_paise=p(7830), quantity=1, unit="order", lead_time_days=4, payment_terms="50% advance",
                        scope="6 standees, 1 backdrop, delivery", verified=True))
        s.commit()
        n = services.get_or_create_negotiation(s, req, printer.id)
        services.buyer_send_offer(s, n, price_paise=p(46000), delivery="By 18 Nov", terms="50% advance",
                                  message="Can you do ₹46,000 all-inclusive?")
        services.supplier_respond(s, n, action="counter", name="Faisal", price_paise=p(48500),
                                  delivery="By 18 Nov", message="Best I can do is ₹48,500 including GST.")
        services.buyer_accept(s, n)
        for e in s.exec(select(Engagement).where(Engagement.request_id == req.id)):
            e.is_sample = True
            s.add(e)
        s.commit()
