"""What the agent's tools actually do, kept free of LiveKit so it can be tested alone.

The guardrails that must never depend on the model live here, in code:
verification, which outcomes need it, forced escalation, and the outcome schema.
"""

import asyncio
import json
import os
import random
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from lumen.data import Borrower, today

ROOT = Path(__file__).resolve().parent.parent
OUTCOMES_FILE = ROOT / "outcomes.jsonl"

MAX_VERIFY_ATTEMPTS = 2

OUTCOMES = {
    "will_pay_on_time",  # by the due date; or today, on an overdue loan
    "already_paid",
    "promise_to_pay",  # commits to a specific date after that
    "hardship",
    "dispute",  # disputes the amount, or says the loan is not theirs
    "wrong_number",
    "third_party",  # someone else answered; no details shared
    "callback_requested",
    "stop_requested",
    "verification_failed",
    "incomplete",  # written by the call itself when it ended without an outcome
}
# Outcomes that only make sense after the borrower heard their loan details.
NEEDS_VERIFICATION = {"will_pay_on_time", "already_paid", "promise_to_pay"}
# Escalation is forced for these regardless of what the model asked for.
ALWAYS_ESCALATE = {"hardship", "dispute"}
# A promise further out than this is still recorded, but a human looks at it.
PROMISE_REVIEW_DAYS = 30


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def check_identity(borrower: Borrower, dob: str = "", last4: str = "") -> bool:
    """True when the DOB (YYYY-MM-DD) or the last 4 digits match. Either is enough."""
    if dob:
        try:
            if date.fromisoformat(dob.strip()) == borrower.dob:
                return True
        except ValueError:
            pass
    return bool(last4) and _digits(last4)[-4:] == borrower.phone_last4


class OutcomeError(ValueError):
    """Told back to the model so it can correct the record, never to the caller."""


def build_outcome(
    *,
    borrower: Borrower,
    today: date,
    verified: bool,
    payment_link_sent: bool,
    outcome: str,
    promise_to_pay_date: str = "",
    escalate_to_human: bool = False,
    notes: str = "",
    language: str = "",
    call_id: str = "",
) -> dict:
    if outcome not in OUTCOMES:
        raise OutcomeError(f"unknown outcome {outcome!r}; use one of {sorted(OUTCOMES)}")
    if outcome in NEEDS_VERIFICATION and not verified:
        raise OutcomeError(f"{outcome} needs a verified borrower; the caller is not verified")

    notes = notes or ""
    promise: date | None = None
    if outcome == "promise_to_pay" and not promise_to_pay_date:
        raise OutcomeError("promise_to_pay needs promise_to_pay_date as YYYY-MM-DD")
    if promise_to_pay_date and outcome in ("promise_to_pay", "will_pay_on_time"):
        try:
            promise = date.fromisoformat(promise_to_pay_date.strip())
        except ValueError as e:
            raise OutcomeError("promise_to_pay_date must be YYYY-MM-DD") from e
        if promise < today:
            raise OutcomeError(
                f"{promise} is in the past (today is {today}); confirm the date with the caller"
            )
        # One rule decides which outcome a payment date is (found in smoke test F8: "I'll
        # pay on the seventeenth" was logged as promise_to_pay, but the 17th is the due
        # date). On or before the due date (or today, if already overdue) is on time.
        on_time_by = max(borrower.due_date, today)
        outcome = "will_pay_on_time" if promise <= on_time_by else "promise_to_pay"

    escalate = bool(escalate_to_human) or outcome in ALWAYS_ESCALATE
    if promise and promise > today + timedelta(days=PROMISE_REVIEW_DAYS):
        escalate = True
        notes = f"{notes} [promise more than {PROMISE_REVIEW_DAYS} days out]".strip()

    return {
        "call_id": call_id or uuid.uuid4().hex[:12],
        "borrower_id": borrower.id,
        "verified": verified,
        "outcome": outcome,
        "promise_to_pay_date": promise.isoformat() if promise else None,
        "payment_link_sent": payment_link_sent,
        "escalate_to_human": escalate,
        "notes": notes.strip(),
        "language": language or borrower.language,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def incomplete_outcome(
    borrower: Borrower, verified: bool, payment_link_sent: bool, call_id: str, why: str = ""
) -> dict:
    """The record for a call that ended (e.g. the caller hung up) before an outcome."""
    return build_outcome(
        borrower=borrower,
        today=today(),
        verified=verified,
        payment_link_sent=payment_link_sent,
        outcome="incomplete",
        notes=f"call ended before an outcome was recorded ({why or 'caller left'})",
        call_id=call_id,
    )


def save_outcome(record: dict, path: Path = OUTCOMES_FILE) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class PaymentLinkError(RuntimeError):
    pass


async def create_payment_link(borrower: Borrower, amount: int) -> str:
    """Mock payment gateway: slow on purpose (1.5-3 s) so the call must fill the wait."""
    await asyncio.sleep(random.uniform(1.5, 3.0))
    if os.environ.get("FAIL_PAYMENT_LINK", "0") == "1":
        raise PaymentLinkError("payment gateway timed out")
    return f"https://pay.lumen.example/{borrower.id}/{uuid.uuid4().hex[:8]}?amt={amount}"


if __name__ == "__main__":
    from lumen.data import load_borrowers

    b = load_borrowers()["B002"]
    ref = date(2026, 11, 14)

    assert check_identity(b, dob="1994-07-25")
    assert check_identity(b, last4="3307")
    assert check_identity(b, last4="98 7654 3307")  # a full number still ends in the right 4
    assert not check_identity(b, dob="1994-07-26")
    assert not check_identity(b, last4="3308")
    assert not check_identity(b, dob="25 July 1994")  # model must send ISO; garbage fails closed
    assert not check_identity(b)

    def bad(**kw) -> str:
        args = dict(borrower=b, today=ref, verified=True, payment_link_sent=False)
        try:
            build_outcome(**{**args, **kw})
        except OutcomeError as e:
            return str(e)
        raise AssertionError(f"expected OutcomeError for {kw}")

    assert "unknown outcome" in bad(outcome="paid")
    assert "past" in bad(outcome="promise_to_pay", promise_to_pay_date="2026-11-10")
    assert "needs promise_to_pay_date" in bad(outcome="promise_to_pay")
    assert "verified" in bad(outcome="promise_to_pay", promise_to_pay_date="2026-11-20", verified=False)

    ok = dict(borrower=b, today=ref, verified=True, payment_link_sent=True)
    # B002 is overdue (due 2 Nov): paying today is on time, any later date is a promise.
    rec = build_outcome(**ok, outcome="promise_to_pay", promise_to_pay_date="2026-11-20")
    assert rec["outcome"] == "promise_to_pay" and rec["promise_to_pay_date"] == "2026-11-20"
    assert rec["escalate_to_human"] is False
    rec = build_outcome(**ok, outcome="promise_to_pay", promise_to_pay_date="2026-11-14")
    assert rec["outcome"] == "will_pay_on_time" and rec["promise_to_pay_date"] == "2026-11-14"
    # B001 is due 17 Nov: "I'll pay on the seventeenth" is on time, the 18th is a promise.
    b1 = load_borrowers()["B001"]
    ok1 = dict(borrower=b1, today=ref, verified=True, payment_link_sent=True)
    assert build_outcome(**ok1, outcome="promise_to_pay", promise_to_pay_date="2026-11-17")["outcome"] == "will_pay_on_time"
    assert build_outcome(**ok1, outcome="will_pay_on_time", promise_to_pay_date="2026-11-18")["outcome"] == "promise_to_pay"
    assert build_outcome(**ok1, outcome="will_pay_on_time")["promise_to_pay_date"] is None
    rec = build_outcome(**ok, outcome="hardship")  # model forgot to escalate
    assert rec["escalate_to_human"] is True
    rec = build_outcome(**ok, outcome="promise_to_pay", promise_to_pay_date="2027-01-30")
    assert rec["escalate_to_human"] is True and "30 days" in rec["notes"]
    rec = build_outcome(borrower=b, today=ref, verified=False, payment_link_sent=False, outcome="third_party")
    assert rec["verified"] is False and rec["language"] == "en-IN"
    print("ok")
