# ClientRoom

**From project brief to client approval, and every change after it.**

Small agencies describe a project to their own Claude. Claude creates a structured agreement in ClientRoom through MCP. The agency edits it and sends a review link; the client approves (or requests revisions) without needing Claude or an account. After approval, change requests (extra scope, price and days) go through the same link, and ClientRoom recalculates the approved total and final deadline.

- **No AI inside the app.** No model, no AI API keys. The customer's own Claude does the thinking via MCP; ClientRoom does storage, editing, sharing, approvals, version history and the arithmetic.
- **Works without Claude too.** Agreements can be created and edited by hand.

## Try it
- App: `https://<your-render-url>` → log in with the demo account (credentials shared in the submission). Sample agreements are labelled **sample data**.
- Connect Claude: in the app open **Connect Claude** to get this workspace's MCP URL, then in Claude go to **Settings → Connectors → Add custom connector** and paste it.
- Try: *"Create a ClientRoom agreement for Acme Foods: website redesign, ₹1,00,000, five pages, two revision rounds, launch in four weeks."*

## MCP tools
| Tool | What it does |
|---|---|
| `create_agreement` | Creates a draft agreement from a brief; returns the agency and client links |
| `create_change_request` | Adds extra scope/price/days to an approved agreement for the client to approve |
| `list_agreements` | Status, approved total and pending change requests for every agreement |
| `get_agreement` | Full scope, version history and client responses |

## Run locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```
Uses SQLite locally; set `DATABASE_URL` for Postgres. `DEMO_PASSWORD` sets the demo login password.

## Limits of this MVP
Client approval is a documented confirmation (name + timestamp), not a legal e-signature. The MCP connection is authorised by a per-workspace key in the URL; production would use OAuth. No payments, invoicing or email sending yet.
