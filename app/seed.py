"""Fictional demo data (labelled 'sample'): one event agency, 9 suppliers across 4 categories,
~5 months of quotes, negotiations, agreements and delivery outcomes."""
import os
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app import services
from app.auth import check_password, hash_password
from app.db import get_session
from app.models import Engagement, Offer, Quotation, SourcingRequest, Supplier, Workspace
from app.money import inr_to_paise as p

DEMO_EMAIL = os.environ.get("DEMO_EMAIL", "demo@quoteloop.app")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "volopay-demo")

SUPPLIERS = [
    ("SoundWave AV Rentals", "AV equipment", "Arjun · +91 98200 11223"),
    ("BrightStage Productions", "AV equipment", "Neha · +91 99300 44556"),
    ("ProLite Event Tech", "AV equipment", "Karan · +91 98190 77889"),
    ("InkJet Print Hub", "Printing", "Faisal · +91 98330 22110"),
    ("PrintCraft Studio", "Printing", "Meera · +91 98670 55443"),
    ("Saffron Table Caterers", "Catering", "Rohit · +91 98211 66778"),
    ("UrbanBite Catering", "Catering", "Sana · +91 99870 33221"),
    ("Bloom & Bower Décor", "Décor", "Ishita · +91 98925 88990"),
    ("Elevate Event Décor", "Décor", "Vikram · +91 98336 44112"),
]

# Older jobs recorded before QuoteLoop (history only).
PAST_JOBS = [
    # supplier, date, item, specs, quoted, agreed, on_time, price_ok, scope_ok, rating, notes
    ("SoundWave AV Rentals", "2026-08-14", "AV for product launch", "Line-array PA, 4 wireless mics, 10x8 ft LED wall, 1 operator",
     180000, 155000, True, True, True, 4, "Agreed ₹1,55,000 after we shared a competing quote"),
    ("SoundWave AV Rentals", "2026-06-20", "AV for investor meet", "Compact PA, 2 wireless mics, projector", 95000, 88000,
     True, True, True, 4, ""),
    ("BrightStage Productions", "2026-09-05", "AV for dealer conference", "Line-array PA, 4 wireless mics, 12x8 ft LED wall, 1 operator",
     170000, 165000, False, True, True, 2, "LED wall arrived 3 hours late; doors delayed 40 min"),
    ("BrightStage Productions", "2026-07-11", "AV for awards night", "PA, 6 wireless mics, LED wall 10x8 ft", 160000, 158000,
     False, True, False, 2, "Lighting rig delivered next morning; 2 mics missing"),
    ("BrightStage Productions", "2026-04-18", "AV for team offsite", "Compact PA, 2 mics", 60000, 60000, True, True, True, 4, ""),
    ("ProLite Event Tech", "2026-09-26", "AV for leadership summit", "Line-array PA, 4 wireless mics, 12x8 ft LED wall, 1 operator",
     190000, 186000, True, True, True, 5, "Set up 2 hours early; backup mics included"),
    ("ProLite Event Tech", "2026-05-09", "AV for client dinner", "Compact PA, 2 wireless mics", 70000, 70000, True, True, True, 5, ""),
    ("Saffron Table Caterers", "2026-07-26", "Catering for monsoon client brunch", "120 guests, live counters, 2 desserts",
     150000, 138000, True, True, True, 5, ""),
    ("UrbanBite Catering", "2026-08-30", "Catering for team offsite", "80 guests, buffet lunch + high tea", 95000, 95000,
     False, True, True, 3, "Lunch service started 45 min late"),
    ("Bloom & Bower Décor", "2026-06-14", "Décor for wedding expo stall", "Floral backdrop, 2 entry arches", 120000, 112000,
     True, True, True, 4, ""),
    ("InkJet Print Hub", "2026-07-02", "Brochures for trade show", "2,000 A4 tri-fold brochures", 32000, 30000, True, True, True, 4, ""),
]

# Full cycles run through QuoteLoop: quotes -> negotiation -> agreement -> outcome.
# quote: (supplier, base, delivery, tax, lead_days, terms, scope, exclusions)
# moves: ("buyer"|"supplier", "offer"|"accept", price, message)
CYCLES = [
    dict(date="2026-09-12", title="Catering for Ganesh Chaturthi client lunch", category="Catering",
         specs="150 guests, vegetarian thali + live chaat counter, service staff", unit="event package", needed_by="2026-09-19",
         quotes=[("Saffron Table Caterers", 180000, 5000, 9250, 7, "50% advance", "Food, 8 service staff, crockery", "Venue charges"),
                 ("UrbanBite Catering", 165000, 8000, 8650, 5, "100% advance", "Food, 6 service staff", "Crockery")],
         winner="Saffron Table Caterers",
         moves=[("buyer", "offer", 175000, "Can you do ₹1,75,000 all-inclusive? Same menu as July."),
                ("supplier", "offer", 182000, "₹1,82,000 including GST, with an extra dessert."),
                ("buyer", "accept", None, "")],
         outcome=(True, True, True, 5, "Client praised the live counter")),
    dict(date="2026-10-02", title="Stage décor for annual awards night", category="Décor",
         specs="Stage backdrop 30x12 ft, floral arch, 20 centrepieces, entrance décor", unit="event package", needed_by="2026-10-10",
         quotes=[("Bloom & Bower Décor", 210000, 12000, 39960, 10, "50% advance", "Backdrop, arch, centrepieces, entrance", "Lighting"),
                 ("Elevate Event Décor", 195000, 10000, 36900, 7, "100% advance", "Backdrop, arch, centrepieces, entrance", "Lighting")],
         winner="Elevate Event Décor",
         moves=[("buyer", "offer", 225000, "We can confirm today at ₹2,25,000 all-inclusive."),
                ("supplier", "accept", None, "Done, confirmed.")],
         outcome=(True, True, False, 3, "Floral arch missing on the day; ₹15,000 credit agreed")),
    dict(date="2026-10-18", title="AV for Diwali client gala", category="AV equipment",
         specs="Line-array PA, 4 wireless mics, 12x8 ft LED wall, 1 operator", unit="event package", needed_by="2026-10-25",
         quotes=[("SoundWave AV Rentals", 190000, 8000, 35640, 5, "50% advance", "PA, 4 mics, LED wall, operator", "Power backup"),
                 ("ProLite Event Tech", 195000, 6000, 36180, 7, "30 days credit", "PA, 4 mics, LED wall, operator, 2 backup mics", "Power backup"),
                 ("BrightStage Productions", 162000, 0, 29160, 3, "100% advance", "PA, 4 mics, LED wall, operator", "Generator")],
         winner="ProLite Event Tech",
         moves=[("buyer", "offer", 220000, "We'd like to work with you again. Can you do ₹2,20,000?"),
                ("supplier", "offer", 228000, "₹2,28,000 with backup mics and 30-day credit."),
                ("buyer", "offer", 224000, "Let's close at ₹2,24,000."),
                ("supplier", "accept", None, "Agreed. See you on the 25th.")],
         outcome=(True, True, True, 5, "On site 3 hours early; handled a power trip calmly")),
    dict(date="2026-10-25", title="Event badges and lanyards", category="Printing",
         specs="600 PVC badges with printed lanyards, names variable data", unit="order", needed_by="2026-11-01",
         quotes=[("InkJet Print Hub", 38000, 1000, 7020, 4, "50% advance", "Badges, lanyards, delivery", ""),
                 ("PrintCraft Studio", 34000, 1500, 6390, 6, "On delivery", "Badges, lanyards", "Rush charges")],
         winner="PrintCraft Studio",
         moves=[("buyer", "offer", 39000, "Can you do ₹39,000 delivered?"), ("supplier", "accept", None, "OK.")],
         outcome=(False, True, True, 3, "Delivered a day late; 40 badges reprinted")),
    dict(date="2026-11-03", title="Printed standees and backdrop", category="Printing",
         specs="6 roll-up standees 3x6 ft, 1 backdrop 20x10 ft, flex print, delivered to venue", unit="order", needed_by="2026-11-08",
         quotes=[("InkJet Print Hub", 42000, 1500, 7830, 4, "50% advance", "6 standees, backdrop, delivery", "")],
         winner="InkJet Print Hub",
         moves=[("buyer", "offer", 46000, "Can you do ₹46,000 all-inclusive?"),
                ("supplier", "offer", 48500, "Best I can do is ₹48,500 including GST."), ("buyer", "accept", None, "")],
         outcome=(True, True, True, 4, "")),
]

OPEN = [
    dict(date="2026-11-06", title="Catering for client offsite (12 Dec)", category="Catering",
         specs="200 guests, buffet lunch + evening snacks, 10 service staff", unit="event package", needed_by="2026-12-12",
         quotes=[("Saffron Table Caterers", 240000, 6000, 12300, 7, "50% advance", "Food, 10 staff, crockery", "Venue charges"),
                 ("UrbanBite Catering", 220000, 8000, 11400, 5, "100% advance", "Food, 8 staff", "Crockery")],
         negotiate="Saffron Table Caterers",
         moves=[("buyer", "offer", 245000, "Happy to go with you again. Can you do ₹2,45,000 all-inclusive?")]),
    dict(date="2026-11-08", title="Stage décor for product launch (8 Dec)", category="Décor",
         specs="Stage backdrop 24x10 ft, product podium, 12 centrepieces", unit="event package", needed_by="2026-12-08",
         quotes=[("Bloom & Bower Décor", 140000, 8000, 26640, 10, "50% advance", "Backdrop, podium, centrepieces", "Lighting"),
                 ("Elevate Event Décor", 130000, 8000, 24840, 7, "100% advance", "Backdrop, podium, centrepieces", "Lighting")],
         negotiate="Bloom & Bower Décor",
         moves=[("buyer", "offer", 155000, "Can you do ₹1,55,000? We'd like reliable delivery this time."),
                ("supplier", "offer", 164000, "₹1,64,000 including GST and setup the night before.")]),
]


def _dt(day: str, hours: int = 0) -> datetime:
    return datetime.fromisoformat(day).replace(hour=10, tzinfo=timezone.utc) + timedelta(hours=hours)


def ensure_workspace() -> int:
    with get_session() as s:
        ws = s.exec(select(Workspace).where(Workspace.login_email == DEMO_EMAIL)).first()
        if ws:
            if os.environ.get("DEMO_PASSWORD") and not check_password(DEMO_PASSWORD, ws.password_hash):
                ws.password_hash = hash_password(DEMO_PASSWORD)
                s.add(ws)
                s.commit()
            return ws.id
        ws = Workspace(name="Spotlight Events (demo)", login_email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD))
        if os.environ.get("MCP_KEY"):
            ws.mcp_key = os.environ["MCP_KEY"]
        s.add(ws)
        s.commit()
        s.refresh(ws)
        return ws.id


def _run(s, wid, sups, spec, finish):
    req = SourcingRequest(workspace_id=wid, title=spec["title"] + " (sample)", category=spec["category"],
                          specifications=spec["specs"], quantity=1, unit=spec["unit"], needed_by=spec["needed_by"],
                          is_sample=True, created_at=_dt(spec["date"]))
    s.add(req)
    s.commit()
    s.refresh(req)
    for name, base, deliv, tax, lead, terms, scope, excl in spec["quotes"]:
        s.add(Quotation(request_id=req.id, supplier_id=sups[name].id, base_paise=p(base), delivery_paise=p(deliv),
                        tax_paise=p(tax), quantity=1, unit=spec["unit"], lead_time_days=lead, payment_terms=terms,
                        scope=scope, exclusions=excl, verified=True, created_at=_dt(spec["date"])))
    s.commit()
    n = services.get_or_create_negotiation(s, req, sups[spec.get("winner") or spec["negotiate"]].id)
    for who, kind, price, msg in spec["moves"]:
        if who == "buyer" and kind == "offer":
            services.buyer_send_offer(s, n, price_paise=p(price), delivery=f"By {spec['needed_by']}", terms="", message=msg)
        elif who == "supplier" and kind == "offer":
            services.supplier_respond(s, n, action="counter", name=sups_contact(sups, n), price_paise=p(price),
                                      delivery=f"By {spec['needed_by']}", message=msg)
        elif who == "supplier" and kind == "accept":
            services.supplier_respond(s, n, action="accept", name=sups_contact(sups, n), message=msg)
        elif who == "buyer" and kind == "accept":
            services.buyer_accept(s, n)
    # Backdate the timeline so the history reads naturally.
    for i, o in enumerate(services.timeline(s, n.id)):
        o.created_at = _dt(spec["date"], 6 * (i + 1))
        s.add(o)
    s.refresh(req)
    if finish and req.status == "agreed":
        req.agreed_at = _dt(spec["date"], 30)
        s.add(req)
        e = s.exec(select(Engagement).where(Engagement.request_id == req.id)).first()
        on_time, price_ok, scope_ok, rating, notes = spec["outcome"]
        e.date = spec["needed_by"]
        e.item = spec["title"]
        e.on_time, e.price_honored, e.scope_honored, e.rating, e.notes = on_time, price_ok, scope_ok, rating, notes
        e.is_sample = True
        s.add(e)
    s.commit()


def sups_contact(sups, n):
    for sup in sups.values():
        if sup.id == n.supplier_id:
            return sup.contact.split(" ·")[0]
    return "Supplier"


def seed_sample_data(wid: int) -> None:
    with get_session() as s:
        if s.exec(select(Supplier).where(Supplier.workspace_id == wid)).first():
            return  # already populated
        sups = {}
        for name, cat, contact in SUPPLIERS:
            sup = Supplier(workspace_id=wid, name=name, category=cat, contact=contact, is_sample=True)
            s.add(sup)
            s.commit()
            s.refresh(sup)
            sups[name] = sup
        for name, d, item, spec, quoted, agreed, on_time, price_ok, scope_ok, rating, notes in PAST_JOBS:
            s.add(Engagement(workspace_id=wid, supplier_id=sups[name].id, date=d, item=item, specifications=spec,
                             quantity=1, unit="event package", quoted_paise=p(quoted), agreed_paise=p(agreed),
                             on_time=on_time, price_honored=price_ok, scope_honored=scope_ok, rating=rating,
                             notes=notes, is_sample=True))
        s.commit()
        for spec in CYCLES:
            _run(s, wid, sups, spec, finish=True)
        for spec in OPEN:
            _run(s, wid, sups, spec, finish=False)


def seed() -> None:
    seed_sample_data(ensure_workspace())
