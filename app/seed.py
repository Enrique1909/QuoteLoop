"""Creates the demo workspace and SAMPLE agreements (clearly labelled as sample data)."""
import os
from datetime import date, timedelta

from sqlmodel import select

from app import services
from app.auth import check_password, hash_password
from app.db import get_session
from app.models import Workspace
from app.money import inr_to_paise

DEMO_EMAIL = os.environ.get("DEMO_EMAIL", "demo@clientroom.app")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "volopay-demo")


def seed() -> None:
    with get_session() as s:
        existing = s.exec(select(Workspace).where(Workspace.login_email == DEMO_EMAIL)).first()
        if existing:
            # Keep the demo login in sync with the DEMO_PASSWORD set on the host.
            if os.environ.get("DEMO_PASSWORD") and not check_password(DEMO_PASSWORD, existing.password_hash):
                existing.password_hash = hash_password(DEMO_PASSWORD)
                s.add(existing)
                s.commit()
            return
        if s.exec(select(Workspace)).first():
            return
        ws = Workspace(name="Demo Studio", login_email=DEMO_EMAIL, password_hash=hash_password(DEMO_PASSWORD))
        if os.environ.get("MCP_KEY"):
            ws.mcp_key = os.environ["MCP_KEY"]
        s.add(ws)
        s.commit()
        s.refresh(ws)

        today = date.today()
        a1 = services.create_agreement(
            s, ws.id, client_name="Sharma Interiors (sample)", project_title="Brand identity refresh",
            price_paise=inr_to_paise(60000), summary="New logo, colour palette and stationery for a Pune interiors studio.",
            deliverables=["Logo with 2 variants", "Colour palette and typography guide", "Business card and letterhead"],
            exclusions=["Printing costs", "Website changes"],
            milestones=[{"name": "Concepts", "due_date": str(today - timedelta(days=20))},
                        {"name": "Final files", "due_date": str(today + timedelta(days=5))}],
            revision_limit=2, is_sample=True)
        services.send_agreement(s, a1)
        services.client_respond(s, a1, approve=True, name="Priya Sharma")
        services.create_change_request(s, a1, description="Add an Instagram post template pack",
                                       extra_price_paise=inr_to_paise(8000), extra_days=4)

        a2 = services.create_agreement(
            s, ws.id, client_name="GreenLeaf Foods (sample)", project_title="Monthly social media retainer",
            price_paise=inr_to_paise(45000), summary="12 posts and 4 reels per month for a packaged-foods brand.",
            deliverables=["12 static posts", "4 reels", "Monthly performance summary"],
            exclusions=["Paid ad spend", "Influencer fees"],
            milestones=[{"name": "Content calendar", "due_date": str(today + timedelta(days=7))}],
            revision_limit=1, is_sample=True)
        services.send_agreement(s, a2)
