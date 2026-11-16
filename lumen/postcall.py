"""Safety net for calls that end before the agent recorded an outcome.

Callers hang up the moment they hear a goodbye (eval baseline: a third of runs ended
'incomplete' this way). After the call, one LLM pass reads the transcript and fills in
only what needs judgement: outcome, pay date, escalation, a note, language. The facts
(verified, link sent) come from the call state, and build_outcome's rules still apply.
"""

import json
import re

from livekit.agents import inference, llm

from lumen.assistant import CallState
from lumen.tools import OUTCOMES, OutcomeError, build_outcome

MODEL = "google/gemini-2.5-flash"

PROMPT = """Below is a phone call from Lumen Finance's EMI reminder agent ("AGENT") to a caller ("CALLER").
The call ended before the agent recorded its outcome. Today is {today}. The caller was {verified}.

Reply with JSON only:
{{"outcome": one of {outcomes},
  "promise_to_pay_date": "YYYY-MM-DD" if the caller named a day they'll pay, else "",
  "escalate_to_human": true if a person must follow up (dispute, hardship, asked for a person, wants a fee reviewed),
  "notes": "one short factual line (callback time, what they dispute, how they paid)",
  "language": "en-IN" or "hi-IN" (the language the call ended in)}}

Pick "incomplete" only if the call ended before anything was settled.

Call:
{transcript}"""


def transcript_text(items: list) -> str:
    lines = []
    for item in items:
        role = getattr(item, "role", None)
        text = (getattr(item, "text_content", None) or "").strip()
        if role in ("user", "assistant") and text:
            lines.append(f"{'CALLER' if role == 'user' else 'AGENT'}: {text}")
    return "\n".join(lines)


async def derive_outcome(state: CallState, items: list) -> dict | None:
    """Fill state.outcome from the transcript if the agent never recorded one."""
    if state.outcome is not None:
        return state.outcome
    transcript = transcript_text(items)
    if "CALLER:" not in transcript:
        return None  # nobody spoke: nothing to derive, the call stays incomplete
    prompt = PROMPT.format(
        today=state.today.isoformat(),
        verified="verified" if state.verified else "NOT verified",
        outcomes=sorted(OUTCOMES),
        transcript=transcript,
    )
    ctx = llm.ChatContext()
    ctx.add_message(role="user", content=prompt)
    text = ""
    async with inference.LLM(model=MODEL, extra_kwargs={"temperature": 0}).chat(chat_ctx=ctx) as stream:
        async for chunk in stream:
            if chunk.delta and chunk.delta.content:
                text += chunk.delta.content
    try:
        fields = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        record = build_outcome(
            borrower=state.borrower,
            today=state.today,
            verified=state.verified,  # facts from the call, not from the model
            payment_link_sent=state.payment_link_sent,
            outcome=fields.get("outcome", "incomplete"),
            promise_to_pay_date=fields.get("promise_to_pay_date") or "",
            escalate_to_human=bool(fields.get("escalate_to_human")),
            notes=f"{fields.get('notes', '')} [derived after the call]".strip(),
            language=fields.get("language", ""),
            call_id=state.call_id,
        )
    except (AttributeError, ValueError, OutcomeError):
        return None  # unusable answer: leave the call incomplete rather than guess
    record["recorded_by"] = "post_call"
    state.outcome = record
    return record
