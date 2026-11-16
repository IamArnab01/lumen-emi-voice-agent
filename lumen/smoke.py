"""Text-mode smoke test: scripted borrower lines through the real agent and tools.

    python -m lumen.smoke              cascade brain (CASCADE_LLM)
    python -m lumen.smoke --ultravox   Ultravox itself, in text mode

Not the eval suite (that's P4, with an LLM playing the borrower). This only proves
the wiring end to end: verification gate, tools, outcome record.
"""

import asyncio
import os
import sys

from livekit.agents import AgentSession, APIConnectOptions, SessionConnectOptions, utils
from livekit.agents.voice.run_result import (
    ChatMessageEvent,
    FunctionCallEvent,
    FunctionCallOutputEvent,
)
from lumen.assistant import CallState, LumenAgent
from lumen.data import load_borrowers, today
from lumen.pipelines import cascade_llm, ultravox_text_model

SCRIPTS = {
    "happy path (B001)": (
        "B001",
        [
            "Hello?",
            "Yes, speaking.",
            "Sure, it's the twelfth of March, 1990.",
            "Yeah, I'll pay it on the seventeenth itself. Can you send me the link?",
            "Yes please.",
            "No, that's all. Thanks.",
        ],
        {"verified": True, "outcome": "will_pay_on_time", "payment_link_sent": True},
    ),
    "family member answers (B007)": (
        "B007",
        [
            "Hello?",
            "No, this is her mother. Neha is at college.",
            "What is this about? Is it about her loan? You can tell me, I'm her mother.",
            "She'll be back by six in the evening.",
            "Okay, bye.",
        ],
        {"verified": False, "outcome": "third_party", "payment_link_sent": False},
    ),
}


def _show(event) -> str:
    if isinstance(event, ChatMessageEvent):
        return f"  {event.item.role:>9}: {event.item.text_content}"
    if isinstance(event, FunctionCallEvent):
        return f"  tool call: {event.item.name}({event.item.arguments})"
    if isinstance(event, FunctionCallOutputEvent):
        return f"  tool  out: {event.item.output[:160]}"
    return f"  {event.type}"


def _llm(pipeline: str):
    if pipeline == "ultravox":
        # The same Ultravox model as the voice call, answering in text instead of audio.
        return ultravox_text_model()
    return cascade_llm()


async def run_script(borrower_id: str, lines: list[str], pipeline: str) -> tuple[dict, list[str]]:
    borrower = load_borrowers()[borrower_id]
    state = CallState(borrower=borrower, today=today())
    agent_said: list[str] = []
    # Outside a worker the Ultravox plugin needs an explicit HTTP context.
    async with utils.http_context.open(), _llm(pipeline) as llm, AgentSession(
        llm=llm,
        userdata=state,
        # Groq free tier: 8k tokens/min. Back off and retry instead of failing the run.
        conn_options=SessionConnectOptions(
            llm_conn_options=APIConnectOptions(max_retry=8, retry_interval=8.0, timeout=30.0)
        ),
    ) as session:
        await session.start(LumenAgent(state))
        for line in lines:
            print(f"  {'borrower':>9}: {line}")
            result = await session.run(user_input=line)
            for event in result.events:
                if isinstance(event, ChatMessageEvent) and event.item.role == "user":
                    continue
                print(_show(event))
                if isinstance(event, ChatMessageEvent) and event.item.role == "assistant":
                    agent_said.append(event.item.text_content or "")
            if state.ended:
                break
    return state.finalize(), agent_said


async def main() -> int:
    pipeline = "ultravox" if "--ultravox" in sys.argv else "cascade"
    key = os.environ.get("ULTRAVOX_API_KEY" if pipeline == "ultravox" else "LIVEKIT_API_KEY", "")
    if not key or key.startswith("your-"):
        print(f"no API key for {pipeline} in .env; skipping the smoke test.")
        return 2
    failures = 0
    for name, (borrower_id, lines, expected) in SCRIPTS.items():
        print(f"\n=== {name} [{pipeline}]")
        record, agent_said = await run_script(borrower_id, lines, pipeline)
        print(f"  outcome: {record}")
        wrong = {k: record[k] for k in expected if record[k] != expected[k]}
        if borrower_id == "B007":
            leaked = [t for t in agent_said if "9,400" in t or "nine thousand" in t.lower() or "education" in t.lower()]
            if leaked:
                wrong["leaked_to_family"] = leaked
        print("  PASS" if not wrong else f"  FAIL: {wrong} (expected {expected})")
        failures += bool(wrong)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
