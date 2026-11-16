"""The voice agent: one LiveKit Agent, the same for both pipelines and for text-mode evals.

Loan facts enter the model's context only as the result of a successful
verify_identity call, so nothing in the prompt can leak before verification.
"""

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from livekit.agents import Agent, RunContext, function_tool, llm

from lumen import prompts
from lumen.data import Borrower, loan_facts
from lumen.tools import (
    MAX_VERIFY_ATTEMPTS,
    OutcomeError,
    PaymentLinkError,
    build_outcome,
    check_identity,
    create_payment_link,
    incomplete_outcome,
    save_outcome,
)

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Said by the framework only if the tool is still running after the model's own
# lead-in line has finished and the line has been quiet for a moment.
FILLER = {
    "en": "Just a second, it's going through.",
    "hi": "बस एक second, भेज रही हूँ।",
}


@dataclass
class CallState:
    borrower: Borrower
    today: date
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    verified: bool = False
    verify_attempts: int = 0
    payment_link_sent: bool = False
    outcome: dict | None = None
    ended: bool = False
    end_reason: str = ""
    # Set by the voice entrypoint: hangs up once the goodbye has played.
    hang_up: Callable[[], None] | None = None

    def finalize(self) -> dict:
        """Save and return the call's outcome; a call that never got one is 'incomplete'."""
        record = self.outcome or incomplete_outcome(
            self.borrower, self.verified, self.payment_link_sent, self.call_id, self.end_reason
        )
        save_outcome(record)
        return record


def caller_language(chat_ctx: llm.ChatContext, default: str) -> str:
    """'hi' or 'en' from the caller's last turn; `default` (a BCP-47 code) before they speak."""
    # ponytail: script heuristic; romanised Hindi ("haan theek hai") reads as English.
    for item in reversed(chat_ctx.items):
        if getattr(item, "role", None) == "user":
            return "hi" if _DEVANAGARI.search(item.text_content or "") else "en"
    return "hi" if default == "hi-IN" else "en"


def _ends_on_open_question(items: list) -> bool:
    """True when the agent's last line was a question the caller hasn't answered yet.

    F4: the agent asked the borrower's mother "when would be a convenient time?" and
    hung up in the same turn. An already-answered question doesn't count (F4b).
    """
    for item in reversed(items):
        role = getattr(item, "role", None)
        text = (getattr(item, "text_content", None) or "").strip()
        if role == "user" and text:
            return False
        if role == "assistant" and text:
            return text.endswith("?")
    return False


class LumenAgent(Agent):
    def __init__(self, state: CallState) -> None:
        super().__init__(instructions=prompts.instructions(state.borrower, state.today))
        self._state = state

    def _caller_language(self) -> str:
        return caller_language(self.chat_ctx, self._state.borrower.language)

    @function_tool
    async def verify_identity(
        self, ctx: RunContext, date_of_birth: str = "", last4_digits: str = ""
    ) -> str:
        """Check the caller is the borrower. Call with whatever they just told you.

        Args:
            date_of_birth: Their date of birth as YYYY-MM-DD, if they gave it.
            last4_digits: The last four digits of their registered mobile number, if they gave those.
        """
        s = self._state
        if s.verified:
            return json.dumps({"verified": True, **loan_facts(s.borrower, s.today)})
        if s.verify_attempts >= MAX_VERIFY_ATTEMPTS:
            return (
                "Verification already failed. Share no loan details. Politely say you can't "
                "discuss the account and end the call with outcome verification_failed."
            )
        s.verify_attempts += 1
        if check_identity(s.borrower, date_of_birth, last4_digits):
            s.verified = True
            return json.dumps(
                {
                    "verified": True,
                    **loan_facts(s.borrower, s.today),
                    "next": "Remind them of the EMI using the *_spoken fields, then ask what they plan to do.",
                },
                ensure_ascii=False,
            )
        left = MAX_VERIFY_ATTEMPTS - s.verify_attempts
        if left:
            return (
                "Did not match. Don't say which part was wrong. Ask them once more, politely, "
                "for their date of birth or the last four digits of their registered number."
            )
        return (
            "Did not match again. Share no loan details. Politely say you can't discuss the "
            "account without verification, suggest they call Lumen Finance, record outcome "
            "verification_failed and end the call."
        )

    @function_tool
    async def send_payment_link(self, ctx: RunContext) -> str:
        """Send the borrower a payment link by SMS. Only after they agree to receive it."""
        s = self._state
        if not s.verified:
            return "Not allowed: the caller is not verified. Do not send anything."
        if s.payment_link_sent:
            return "Already sent earlier in this call. Tell them it's on their phone."
        try:
            async with ctx.with_filler(FILLER[self._caller_language()], delay=1.2, max_steps=1):
                await create_payment_link(s.borrower, s.borrower.emi_amount)
        except PaymentLinkError:
            return (
                "FAILED: the payment link could not be sent right now. Apologise briefly; say "
                "they can pay in the Lumen Finance app or website, or the link will be sent "
                "later. Do not retry and do not make up a link."
            )
        s.payment_link_sent = True
        return "Sent by SMS to their registered mobile number. Tell them it's on its way."

    @function_tool
    async def record_outcome(
        self,
        ctx: RunContext,
        outcome: str,
        promise_to_pay_date: str = "",
        escalate_to_human: bool = False,
        notes: str = "",
        language: str = "en-IN",
    ) -> str:
        """Record how the call ended. Call once, before the goodbye.

        Args:
            outcome: One of will_pay_on_time, already_paid, promise_to_pay, hardship, dispute, wrong_number, third_party, callback_requested, stop_requested, verification_failed.
            promise_to_pay_date: The day the caller said they'll pay, as YYYY-MM-DD, whenever they named one (even if it's the due date). Empty if they named no day: don't fill it in for them.
            escalate_to_human: True if a person from the team must follow up (dispute, hardship, they asked for a person).
            notes: One short factual line: what they said, callback time, what they dispute.
            language: Language the call ended in: en-IN or hi-IN.
        """
        s = self._state
        # Found in the smoke test: gpt-oss-20b recorded "wrong_number" and hung up on
        # the caller's first "Hello?" without saying a word. No outcome before we've spoken.
        if not any(
            getattr(item, "role", None) == "assistant" and (item.text_content or "").strip()
            for item in self.chat_ctx.items
        ):
            return (
                "Not recorded: you haven't spoken to the caller yet. Greet them, say you're "
                "from Lumen Finance, and ask for the borrower by name first."
            )
        try:
            s.outcome = build_outcome(
                borrower=s.borrower,
                today=s.today,
                verified=s.verified,
                payment_link_sent=s.payment_link_sent,
                outcome=outcome,
                promise_to_pay_date=promise_to_pay_date,
                escalate_to_human=escalate_to_human,
                notes=notes,
                language=language,
                call_id=s.call_id,
            )
        except OutcomeError as e:
            return f"Not recorded: {e}"
        # F9: "Say a goodbye" here made the agent say a second one after its first.
        return "Recorded. If you haven't said goodbye yet, say one short warm line; then call end_call."

    @function_tool
    async def end_call(self, ctx: RunContext) -> str | None:
        """Hang up. Only after record_outcome and after you have said goodbye."""
        s = self._state
        if s.outcome is None:
            return "Record the outcome with record_outcome first, then say goodbye."
        # Wait for this turn's speech to play out first: only then is what the agent said
        # in this same turn in the history (checking before missed it; see F4b).
        await ctx.wait_for_playout()
        if _ends_on_open_question(self.session.history.items):
            return "Not ended: you just asked the caller a question. Wait for their answer first."
        s.ended = True
        s.end_reason = s.end_reason or "agent"
        if s.hang_up:
            s.hang_up()
        return None


if __name__ == "__main__":
    from types import SimpleNamespace as Item

    def said(role: str, text: str) -> Item:
        return Item(role=role, text_content=text)

    # F4: the question is the last thing said, nobody answered: don't hang up.
    assert _ends_on_open_question([said("user", "She's at college."), said("assistant", "When can I reach her?")])
    # F4b: the question was answered, then a goodbye: fine to hang up.
    assert not _ends_on_open_question(
        [said("assistant", "Anything else?"), said("user", "No, thanks."), said("assistant", "Have a great day!")]
    )
    assert not _ends_on_open_question([said("assistant", "Anything else?"), said("user", "No.")])
    assert not _ends_on_open_question([])
    print("ok")
