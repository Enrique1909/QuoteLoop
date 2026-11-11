"""Business logic shared by the web app and the MCP server. No AI anywhere in here."""
from datetime import timezone
from typing import Optional

from sqlmodel import Session, select

from app.models import Agreement, AgreementVersion, ChangeRequest, now, snapshot
from app.money import approved_total, final_deadline, format_inr


class NotFound(Exception):
    pass


def clean_list(items) -> list[str]:
    return [str(i).strip() for i in (items or []) if str(i).strip()]


def clean_milestones(items) -> list[dict]:
    out = []
    for m in items or []:
        if isinstance(m, dict):
            name = str(m.get("name", "")).strip()
            due = str(m.get("due_date", "")).strip()
        else:
            name, due = str(m).strip(), ""
        if name:
            out.append({"name": name, "due_date": due})
    return out


def get_agreement(s: Session, workspace_id: int, agreement_id: int) -> Agreement:
    a = s.get(Agreement, agreement_id)
    if not a or a.workspace_id != workspace_id:
        raise NotFound(f"Agreement {agreement_id} not found in this workspace")
    return a


def change_requests(s: Session, agreement_id: int) -> list[ChangeRequest]:
    return list(s.exec(select(ChangeRequest).where(ChangeRequest.agreement_id == agreement_id).order_by(ChangeRequest.id)))


def versions(s: Session, agreement_id: int) -> list[AgreementVersion]:
    return list(s.exec(select(AgreementVersion).where(AgreementVersion.agreement_id == agreement_id).order_by(AgreementVersion.version_no)))


def latest_version(s: Session, agreement_id: int) -> Optional[AgreementVersion]:
    vs = versions(s, agreement_id)
    return vs[-1] if vs else None


def create_agreement(s: Session, workspace_id: int, *, client_name, project_title, price_paise,
                     summary="", client_email="", deliverables=None, exclusions=None, milestones=None,
                     revision_limit=2, created_via="app", is_sample=False) -> Agreement:
    a = Agreement(
        workspace_id=workspace_id, client_name=client_name.strip(), client_email=(client_email or "").strip(),
        project_title=project_title.strip(), summary=(summary or "").strip(),
        deliverables=clean_list(deliverables), exclusions=clean_list(exclusions),
        milestones=clean_milestones(milestones), revision_limit=int(revision_limit),
        base_price_paise=int(price_paise), created_via=created_via, is_sample=is_sample,
    )
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


def update_agreement(s: Session, a: Agreement, **fields) -> Agreement:
    for k, v in fields.items():
        if k in ("deliverables", "exclusions"):
            v = clean_list(v)
        elif k == "milestones":
            v = clean_milestones(v)
        setattr(a, k, v)
    # Any edit after sending means the client must review a new version.
    a.status = "draft"
    a.updated_at = now()
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


def send_agreement(s: Session, a: Agreement) -> AgreementVersion:
    prev = latest_version(s, a.id)
    v = AgreementVersion(agreement_id=a.id, version_no=(prev.version_no + 1) if prev else 1,
                         snapshot=snapshot(a), status="sent")
    a.status = "sent"
    a.updated_at = now()
    s.add(v)
    s.add(a)
    s.commit()
    s.refresh(v)
    return v


def client_respond(s: Session, a: Agreement, *, approve: bool, name: str, comment: str = "") -> AgreementVersion:
    v = latest_version(s, a.id)
    if not v or a.status not in ("sent",):
        raise ValueError("There is no version waiting for your review.")
    v.status = "approved" if approve else "revision_requested"
    v.responded_by_name = name.strip()
    v.responded_at = now()
    v.client_comment = (comment or "").strip()
    a.status = v.status
    a.updated_at = now()
    s.add(v)
    s.add(a)
    s.commit()
    return v


def create_change_request(s: Session, a: Agreement, *, description, extra_price_paise, extra_days, created_via="app") -> ChangeRequest:
    if a.status != "approved":
        raise ValueError("Change requests can only be raised on an approved agreement.")
    cr = ChangeRequest(agreement_id=a.id, description=description.strip(), extra_price_paise=int(extra_price_paise),
                       extra_days=int(extra_days), created_via=created_via)
    s.add(cr)
    s.commit()
    s.refresh(cr)
    return cr


def respond_change_request(s: Session, a: Agreement, cr_id: int, *, approve: bool, name: str) -> ChangeRequest:
    cr = s.get(ChangeRequest, cr_id)
    if not cr or cr.agreement_id != a.id or cr.status != "pending":
        raise ValueError("This change request is not awaiting a response.")
    cr.status = "approved" if approve else "declined"
    cr.responded_by_name = name.strip()
    cr.responded_at = now()
    s.add(cr)
    s.commit()
    return cr


def iso(dt) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def summary(s: Session, a: Agreement) -> dict:
    crs = change_requests(s, a.id)
    deadline = final_deadline(a, crs)
    return {
        "id": a.id,
        "client": a.client_name,
        "project": a.project_title,
        "status": a.status,
        "base_price": format_inr(a.base_price_paise),
        "approved_total": format_inr(approved_total(a, crs)),
        "pending_change_requests": sum(1 for c in crs if c.status == "pending"),
        "final_deadline": deadline.isoformat() if deadline else None,
        "sample_data": a.is_sample,
    }


def detail(s: Session, a: Agreement) -> dict:
    crs = change_requests(s, a.id)
    d = summary(s, a)
    d.update({
        "client_email": a.client_email,
        "summary": a.summary,
        "deliverables": a.deliverables,
        "exclusions": a.exclusions,
        "milestones": a.milestones,
        "revision_limit": a.revision_limit,
        "versions": [
            {"version": v.version_no, "status": v.status, "sent_at": iso(v.sent_at),
             "responded_by": v.responded_by_name or None, "responded_at": iso(v.responded_at),
             "client_comment": v.client_comment or None}
            for v in versions(s, a.id)
        ],
        "change_requests": [
            {"id": c.id, "description": c.description, "extra_price": format_inr(c.extra_price_paise),
             "extra_days": c.extra_days, "status": c.status, "responded_by": c.responded_by_name or None}
            for c in crs
        ],
    })
    return d
