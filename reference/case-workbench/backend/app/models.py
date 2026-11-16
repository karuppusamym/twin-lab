"""Tables of the Case Workbench reference application."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

STATUSES = ("OPEN", "IN_PROGRESS", "ESCALATED", "RESOLVED", "CLOSED")
PRIORITIES = ("LOW", "MEDIUM", "HIGH")


class Base(DeclarativeBase):
    pass


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(80))


class Customer(Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    cust_no: Mapped[str] = mapped_column(String(12), unique=True)
    full_name: Mapped[str] = mapped_column(String(80), index=True)
    email: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(60))


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(60))


class Subcategory(Base):
    __tablename__ = "subcategory"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"))
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(60))


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_no: Mapped[str] = mapped_column(String(12), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), index=True)
    priority: Mapped[str] = mapped_column(String(8))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    subcategory_id: Mapped[int | None] = mapped_column(ForeignKey("subcategory.id"))
    escalation_reason: Mapped[str | None] = mapped_column(String(200))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship()
    category: Mapped[Category | None] = relationship()
    subcategory: Mapped[Subcategory | None] = relationship()


class CaseNote(Base):
    __tablename__ = "case_note"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    text: Mapped[str] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CannedResponse(Base):
    """Reply templates kept by team leads. No automation uses these."""

    __tablename__ = "canned_response"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    title: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(String(1000))
    active: Mapped[bool] = mapped_column(default=True)


class CaseAudit(Base):
    __tablename__ = "case_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    event: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str] = mapped_column(String(300))
    actor: Mapped[str] = mapped_column(String(40))
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
