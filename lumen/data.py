"""Mock borrowers, and how their amounts and dates are said out loud.

The agent never formats numbers itself: it gets ready-to-speak phrases
("one lakh twenty-five thousand rupees", "this Tuesday, the seventeenth"),
so the call sounds like a person, not a form being read out.
"""

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from num2words import num2words

load_dotenv()

BORROWERS_FILE = Path(__file__).resolve().parent.parent / "data" / "borrowers.json"


def today() -> date:
    """`TODAY` from the env when pinned (reproducible evals), else the real date."""
    pinned = os.environ.get("TODAY", "").strip()
    return date.fromisoformat(pinned) if pinned else date.today()


@dataclass(frozen=True)
class Borrower:
    id: str
    name: str
    dob: date
    phone_last4: str
    loan_type: str
    emi_amount: int
    due_date: date
    language: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    def days_overdue(self, on: date) -> int:
        # Derived, not stored, so it can't disagree with the due date.
        # ponytail: one missed EMI only; add an instalment ledger if multi-EMI arrears matter.
        return max(0, (on - self.due_date).days)


def load_borrowers(path: Path = BORROWERS_FILE) -> dict[str, Borrower]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        row["id"]: Borrower(
            id=row["id"],
            name=row["name"],
            dob=date.fromisoformat(row["dob"]),
            phone_last4=row["phone_last4"],
            loan_type=row["loan_type"],
            emi_amount=row["emi_amount"],
            due_date=date.fromisoformat(row["due_date"]),
            language=row["language"],
        )
        for row in rows
    }


def inr(amount: int) -> str:
    """Indian digit grouping: 125000 -> '₹1,25,000'."""
    digits = str(amount)
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while head:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return "₹" + ",".join([*pairs, tail]) if pairs else "₹" + tail


def rupees(amount: int) -> str:
    """12500 -> 'twelve thousand five hundred rupees'; uses lakh, not 'hundred thousand'."""
    words = num2words(amount, lang="en_IN").replace(",", "").replace(" and ", " ")
    return f"{words} rupee" if amount == 1 else f"{words} rupees"


def _count(n: int, unit: str) -> str:
    return f"{num2words(n)} {unit}" + ("" if n == 1 else "s")


def spoken_date(day: date, on: date) -> str:
    """How a person says a due date, relative to `on` (the call's today)."""
    delta = (day - on).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    ordinal = f"the {num2words(day.day, to='ordinal')}"
    if 1 < delta < 7:
        return f"this {day.strftime('%A')}, {ordinal}"
    if (day.year, day.month) == (on.year, on.month):
        return ordinal
    month = day.strftime("%B")
    return f"{ordinal} of {month}" if day.year == on.year else f"{ordinal} of {month} {day.year}"


def loan_facts(borrower: Borrower, on: date) -> dict:
    """What the agent may say once the borrower is verified, raw and spoken."""
    overdue = borrower.days_overdue(on)
    if overdue:
        status = "overdue"
    elif borrower.due_date == on:
        status = "due_today"
    else:
        status = "upcoming"
    return {
        "loan_type": borrower.loan_type,
        "status": status,
        "emi_amount": borrower.emi_amount,
        "emi_amount_display": inr(borrower.emi_amount),
        "emi_amount_spoken": rupees(borrower.emi_amount),
        "due_date": borrower.due_date.isoformat(),
        "due_date_spoken": spoken_date(borrower.due_date, on),
        "days_overdue": overdue,
        "days_overdue_spoken": _count(overdue, "day") if overdue else "",
    }


if __name__ == "__main__":
    ref = date(2026, 11, 14)  # a Saturday
    assert inr(2999) == "₹2,999"
    assert inr(125000) == "₹1,25,000"
    assert inr(10000000) == "₹1,00,00,000"
    assert rupees(12500) == "twelve thousand five hundred rupees", rupees(12500)
    assert rupees(125000) == "one lakh twenty-five thousand rupees", rupees(125000)
    assert rupees(2999) == "two thousand nine hundred ninety-nine rupees", rupees(2999)
    assert spoken_date(ref, ref) == "today"
    assert spoken_date(date(2026, 11, 15), ref) == "tomorrow"
    assert spoken_date(date(2026, 11, 17), ref) == "this Tuesday, the seventeenth"
    assert spoken_date(date(2026, 11, 2), ref) == "the second"
    assert spoken_date(date(2026, 12, 5), ref) == "the fifth of December"
    assert spoken_date(date(2027, 1, 5), ref) == "the fifth of January 2027"

    borrowers = load_borrowers()
    assert len(borrowers) == 8
    facts = {b.id: loan_facts(b, ref) for b in borrowers.values()}
    assert facts["B002"]["status"] == "overdue" and facts["B002"]["days_overdue"] == 12
    assert facts["B002"]["days_overdue_spoken"] == "twelve days"
    assert facts["B005"]["status"] == "due_today"
    assert facts["B001"]["status"] == "upcoming"

    for b in borrowers.values():
        f = facts[b.id]
        tail = f", {f['days_overdue_spoken']} overdue" if f["days_overdue"] else ""
        print(f"{b.id} {b.name:<20} {f['emi_amount_spoken']}, due {f['due_date_spoken']}{tail}")
    print("ok")
