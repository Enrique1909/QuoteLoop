import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


class Workspace(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    login_email: str = Field(index=True, unique=True)
    password_hash: str
    mcp_key: str = Field(default_factory=lambda: secrets.token_urlsafe(24), index=True, unique=True)
    created_at: datetime = Field(default_factory=now)


class Agreement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="workspace.id", index=True)
    client_name: str
    client_email: str = ""
    project_title: str
    summary: str = ""
    deliverables: list = Field(default_factory=list, sa_column=Column(JSON))
    exclusions: list = Field(default_factory=list, sa_column=Column(JSON))
    milestones: list = Field(default_factory=list, sa_column=Column(JSON))  # [{name, due_date}]
    revision_limit: int = 2
    base_price_paise: int = 0
    currency: str = "INR"
    status: str = "draft"  # draft | sent | revision_requested | approved
    share_token: str = Field(default_factory=lambda: secrets.token_urlsafe(16), index=True, unique=True)
    created_via: str = "app"  # app | claude
    is_sample: bool = False
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class AgreementVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agreement_id: int = Field(foreign_key="agreement.id", index=True)
    version_no: int
    snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "sent"  # sent | revision_requested | approved
    sent_at: datetime = Field(default_factory=now)
    responded_by_name: str = ""
    responded_at: Optional[datetime] = None
    client_comment: str = ""


class ChangeRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agreement_id: int = Field(foreign_key="agreement.id", index=True)
    description: str
    extra_price_paise: int = 0
    extra_days: int = 0
    status: str = "pending"  # pending | approved | declined
    created_via: str = "app"
    created_at: datetime = Field(default_factory=now)
    responded_by_name: str = ""
    responded_at: Optional[datetime] = None


SNAPSHOT_FIELDS = [
    "client_name", "client_email", "project_title", "summary", "deliverables",
    "exclusions", "milestones", "revision_limit", "base_price_paise", "currency",
]


def snapshot(a: Agreement) -> dict:
    return {f: getattr(a, f) for f in SNAPSHOT_FIELDS}
