# QuoteLoop

**Compare supplier quotes, negotiate with evidence, and remember who actually delivered.**

For small event agencies that buy AV, printing, catering and décor from the same vendors every month, with quotes arriving over email, WhatsApp and PDFs.

- The buyer's **own Claude** reads messy quotes and creates a comparison in QuoteLoop through MCP.
- QuoteLoop calculates totals (base + delivery + tax, in integer paise), flags quotes that aren't comparable, and shows the buyer's **history with each supplier**: past quoted vs agreed prices, on-time record, ratings, issues.
- Claude reads that history through MCP and **saves a suggested counteroffer as a draft**. Nothing is sent automatically.
- The buyer edits and sends the offer. The **supplier opens a private link** (no account, no Claude, and they never see competitors' quotes), then accepts or counters.
- Claude can also **judge the award** (price vs reliability vs terms) and save a recommendation that a manager or client approves from a private link, and write **supplier reviews** from the spend data.
- On agreement, terms are locked, savings vs the first quote are calculated, and the outcome feeds the supplier's history for next time.

**No AI inside the app:** no model, no AI API keys. Claude runs on the customer's own plan.

## Try it
- App: `https://<render-url>` → log in with the demo buyer account (credentials shared in the submission). Demo data is fictional and labelled **sample data**.
- Connect Claude: **Connect Claude** page → copy the MCP URL → in Claude, **Settings → Connectors → Add custom connector**.
- Sample quotes to attach in Claude: `docs/sample-quotes/` (an email, a WhatsApp message and a PDF-style quotation).

## MCP tools
| Tool | Purpose |
|---|---|
| `create_sourcing_comparison` | Save a sourcing request and the extracted quotations (editable by the buyer; totals calculated by the app) |
| `get_negotiation_context` | Current quote, other quotes (buyer-side only), history with the supplier, negotiation timeline |
| `save_negotiation_draft` | Store a suggested counteroffer with reasoning and the history it relied on, for buyer review |
| `record_supplier_reply` | Log a counteroffer the supplier sent on WhatsApp, phone or email (e.g. a pasted message) |
| `get_sourcing_overview` | All quotes on a request with totals, history and levers, to judge the award |
| `save_award_recommendation` | Claude's recommended supplier with rationale, risks and fallback; shared with an approver via a private link |
| `get_spend_overview` | Spend, savings and reliability by supplier and category |
| `save_supplier_review` | A supplier/spend review written by Claude, saved for the team |
| `list_sourcing_requests` | Status of all requests |

## Run locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```
SQLite by default; set `DATABASE_URL` (pooled) and `DATABASE_URL_UNPOOLED` (direct, used for table creation) for Neon Postgres. `DEMO_PASSWORD` sets the demo login.

## Known limitations (POC)
- One demo buyer organisation; MCP access uses a revocable per-workspace key in the URL (production: OAuth).
- Supplier links are long random tokens scoped to one negotiation and revoked when another supplier wins; no supplier accounts.
- Acceptance is a commercial record, not a legal e-signature. No payments, purchase orders, or WhatsApp/email sending.
- Supplier performance can be recorded after an agreement; richer analytics are roadmap.
- Suppliers can use the private link or reply on WhatsApp; WhatsApp replies are logged by the buyer (or their Claude). Automatic WhatsApp ingestion via the WhatsApp Business Platform is roadmap.
