"""Deterministic synthetic seed data. The same seed always gives the same rows."""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PRIORITIES,
    AppUser,
    CannedResponse,
    Case,
    CaseAudit,
    CaseNote,
    Category,
    Customer,
    Subcategory,
)

SEED = 20261115
BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

# Test credentials for the reference app only. They exist nowhere else.
DEMO_USER = "demo.user"
DEMO_PASSWORD = "workbench-demo"

FIRST = ["Asha", "Ben", "Carla", "Dev", "Elena", "Farid", "Grace", "Hugo", "Ines", "Jonas"]
LAST = ["Okafor", "Lindqvist", "Marsh", "Nakamura", "Petrov", "Quinn", "Rossi", "Shah"]
CITIES = ["Leeds", "Porto", "Tampere", "Graz", "Cork", "Lyon"]
TITLES = [
    "Card declined at checkout",
    "Statement shows duplicate charge",
    "Cannot sign in to online banking",
    "Address change not applied",
    "Refund not received",
    "Transfer pending for three days",
]
CATEGORIES = {
    "CARDS": ("Cards", [("CARD_DECLINE", "Declined payment"), ("CARD_LOST", "Lost or stolen")]),
    "PAYMENTS": (
        "Payments",
        [("PAY_DUP", "Duplicate charge"), ("PAY_REFUND", "Refund"), ("PAY_DELAY", "Delayed")],
    ),
    "ACCESS": ("Online access", [("ACC_LOGIN", "Sign-in problem"), ("ACC_LOCK", "Locked out")]),
    "PROFILE": ("Customer profile", [("PRO_ADDR", "Address change"), ("PRO_NAME", "Name change")]),
}


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def seed_if_empty(db: Session) -> bool:
    """Load the seed rows unless the database already has users. Returns True if it loaded."""
    if db.scalar(select(AppUser.id).limit(1)) is not None:
        return False
    rng = random.Random(SEED)

    db.add(
        AppUser(
            username=DEMO_USER, password_hash=hash_password(DEMO_PASSWORD), display_name="Demo User"
        )
    )

    for code, title, body in (
        ("ACK", "Acknowledge", "Thank you, we are looking into this and will update you."),
        ("MOREINFO", "Need more information", "Please send the date and amount of the payment."),
        ("FIXED", "Resolved", "The issue is fixed. Contact us again if it comes back."),
    ):
        db.add(CannedResponse(code=code, title=title, body=body, active=True))

    subs_by_cat: dict[int, list[int]] = {}
    sub_id = 0
    for cat_id, (code, (name, subs)) in enumerate(CATEGORIES.items(), start=1):
        db.add(Category(id=cat_id, code=code, name=name))
        for sub_code, sub_name in subs:
            sub_id += 1
            db.add(Subcategory(id=sub_id, category_id=cat_id, code=sub_code, name=sub_name))
            subs_by_cat.setdefault(cat_id, []).append(sub_id)

    for i in range(1, 41):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        db.add(
            Customer(
                id=i,
                cust_no=f"CU-{2000 + i}",
                full_name=f"{first} {last}",
                email=f"{first}.{last}{i}@example.test".lower(),
                phone=f"+44 20 7946 {rng.randrange(0, 1000):04d}",
                city=rng.choice(CITIES),
            )
        )

    # Mostly open work, a few already escalated, resolved or closed.
    status_pool = ["OPEN"] * 5 + ["IN_PROGRESS"] * 3 + ["ESCALATED", "RESOLVED", "CLOSED"]
    for i in range(1, 121):
        status = rng.choice(status_pool)
        opened = BASE + timedelta(hours=rng.randrange(0, 24 * 40))
        categorised = status != "OPEN"
        cat_id = rng.randrange(1, len(CATEGORIES) + 1) if categorised else None
        case = Case(
            id=i,
            case_no=f"C-{1000 + i}",
            customer_id=rng.randrange(1, 41),
            title=rng.choice(TITLES),
            status=status,
            priority=rng.choice(PRIORITIES),
            category_id=cat_id,
            subcategory_id=rng.choice(subs_by_cat[cat_id]) if cat_id else None,
            escalation_reason="Customer asked for a manager" if status == "ESCALATED" else None,
            opened_at=opened,
            updated_at=opened,
        )
        db.add(case)
        db.add(
            CaseAudit(
                case_id=i, event="CREATED", detail=case.title, actor="system", created_ts=opened
            )
        )
        if status in ("RESOLVED", "CLOSED"):
            db.add(
                CaseNote(
                    case_id=i,
                    text="Issue confirmed fixed with the customer.",
                    author=DEMO_USER,
                    created_at=opened + timedelta(hours=2),
                )
            )
    db.commit()
    return True
