import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def now() -> datetime:
    return datetime.now(timezone.utc)


class Workspace(SQLModel, table=True):
    __tablename__ = "ql_workspace"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    login_email: str = Field(index=True, unique=True)
    password_hash: str
    mcp_key: str = Field(default_factory=lambda: secrets.token_urlsafe(24), index=True, unique=True)


class Supplier(SQLModel, table=True):
    __tablename__ = "ql_supplier"
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="ql_workspace.id", index=True)
    name: str
    contact: str = ""
    category: str = ""
    is_sample: bool = False


class SourcingRequest(SQLModel, table=True):
    __tablename__ = "ql_request"
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="ql_workspace.id", index=True)
    title: str
    category: str = ""
    specifications: str = ""
    quantity: int = 1
    unit: str = "unit"
    needed_by: str = ""  # YYYY-MM-DD
    currency: str = "INR"
    status: str = "comparing"  # comparing | negotiating | agreed | closed
    agreed_supplier_id: Optional[int] = None
    agreed_price_paise: Optional[int] = None
    agreed_at: Optional[datetime] = None
    created_via: str = "app"  # app | claude
    is_sample: bool = False
    created_at: datetime = Field(default_factory=now)


class Quotation(SQLModel, table=True):
    __tablename__ = "ql_quotation"
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="ql_request.id", index=True)
    supplier_id: int = Field(foreign_key="ql_supplier.id", index=True)
    base_paise: int = 0
    delivery_paise: int = 0
    tax_paise: int = 0
    quantity: int = 1
    unit: str = "unit"
    lead_time_days: Optional[int] = None
    payment_terms: str = ""
    scope: str = ""
    exclusions: str = ""
    verified: bool = False  # False = extracted by Claude, awaiting buyer check
    created_via: str = "app"
    created_at: datetime = Field(default_factory=now)

    @property
    def total_paise(self) -> int:
        return self.base_paise + self.delivery_paise + self.tax_paise


class Negotiation(SQLModel, table=True):
    __tablename__ = "ql_negotiation"
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="ql_request.id", index=True)
    supplier_id: int = Field(foreign_key="ql_supplier.id", index=True)
    # draft | awaiting_supplier | awaiting_buyer | agreed | rejected | closed
    status: str = "draft"
    token: str = Field(default_factory=lambda: secrets.token_urlsafe(24), index=True, unique=True)
    token_revoked: bool = False
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)


class Offer(SQLModel, table=True):
    __tablename__ = "ql_offer"
    id: Optional[int] = Field(default=None, primary_key=True)
    negotiation_id: int = Field(foreign_key="ql_negotiation.id", index=True)
    author: str  # buyer | supplier
    kind: str = "offer"  # offer | accept | reject
    price_paise: Optional[int] = None
    delivery_commitment: str = ""
    terms: str = ""
    message: str = ""
    author_name: str = ""
    created_at: datetime = Field(default_factory=now)


class NegotiationDraft(SQLModel, table=True):
    __tablename__ = "ql_draft"
    id: Optional[int] = Field(default=None, primary_key=True)
    negotiation_id: int = Field(foreign_key="ql_negotiation.id", index=True)
    price_paise: int
    delivery_commitment: str = ""
    terms: str = ""
    message: str = ""
    reasoning: str = ""
    references: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=now)


class Engagement(SQLModel, table=True):
    """Company-specific supplier history: what was quoted, agreed and actually delivered."""
    __tablename__ = "ql_engagement"
    id: Optional[int] = Field(default=None, primary_key=True)
    workspace_id: int = Field(foreign_key="ql_workspace.id", index=True)
    supplier_id: int = Field(foreign_key="ql_supplier.id", index=True)
    request_id: Optional[int] = None
    date: str  # YYYY-MM-DD
    item: str
    specifications: str = ""
    quantity: int = 1
    unit: str = "unit"
    quoted_paise: int
    agreed_paise: int
    on_time: Optional[bool] = None  # None = not yet recorded
    price_honored: Optional[bool] = None
    scope_honored: Optional[bool] = None
    rating: Optional[int] = None  # 1-5
    notes: str = ""
    is_sample: bool = False
