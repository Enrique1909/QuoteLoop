"""Reset demo data: delete everything except the workspace (so the Claude connector URL keeps working),
then reload the sample data. Run: .venv/bin/python -m scripts.reset_demo"""
from sqlalchemy import text

from app.db import engine, init_db
from app.seed import ensure_workspace, seed_sample_data

TABLES = ["ql_recommendation", "ql_review", "ql_draft", "ql_offer", "ql_negotiation", "ql_engagement", "ql_quotation", "ql_request", "ql_supplier"]

init_db()
with engine.begin() as c:
    for t in TABLES:
        c.execute(text(f"DELETE FROM {t}"))
wid = ensure_workspace()
seed_sample_data(wid)
print("Demo data reset for workspace", wid)
