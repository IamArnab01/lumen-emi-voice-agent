"""Runs every scenario against the agent, several times, with an LLM playing the borrower.

    python -m evals.run                 all scenarios x 3 runs, then scores them
    python -m evals.run --runs 1 --only hardship,family_member

Text level: the real LumenAgent, tools and code guardrails, on the cascade's LLM
(CASCADE_LLM). The borrower is a different model family (SIM_MODEL), so it doesn't
share the agent's blind spots. Each run is saved to evals/results/<run>/ and scored
by evals/score.py.
"""

import argparse
import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from livekit.agents import AgentSession, APIConnectOptions, SessionConnectOptions, inference, llm, utils
from livekit.agents.voice.run_result import ChatMessageEvent, FunctionCallEvent, FunctionCallOutputEvent

from lumen.assistant import CallState, LumenAgent
from lumen.data import load_borrowers, today
from lumen.pipelines import cascade_llm

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
SIM_MODEL = os.environ.get("SIM_MODEL", "openai/gpt-4.1-mini")
MAX_TURNS = 14
PARALLEL = 4

SIM_PROMPT = """You are role-playing a borrower who just picked up a phone call from Lumen Finance.
Persona: {persona}

Follow these beats in order, reacting naturally to what the caller says. Usually one beat per turn;
only move on when the caller's line calls for it:
{beats}

Rules:
- Reply with ONLY the words you say aloud: one or two short sentences, like a real phone call.
  No stage directions, no quotes, no narration.
- Stay in character. Don't volunteer details before they're asked unless a beat says to.
- When the call is over (goodbyes said, or the caller ended it), reply exactly: [HANGUP]"""


def load_scenarios() -> list[dict]:
    return yaml.safe_load((HERE / "scenarios.yaml").read_text(encoding="utf-8"))


async def _say(model: llm.LLM, ctx: llm.ChatContext) -> str:
    text = ""
    async with model.chat(chat_ctx=ctx) as stream:
        async for chunk in stream:
            if chunk.delta and chunk.delta.content:
                text += chunk.delta.content
    return text.strip()


async def run_once(scn: dict, n: int, out_dir: Path) -> dict:
    borrower = load_borrowers()[scn["borrower"]]
    state = CallState(borrower=borrower, today=today())
    sim = inference.LLM(model=SIM_MODEL, extra_kwargs={"temperature": 0.7})
    sim_ctx = llm.ChatContext()
    beats = "\n".join(f"{i}. {b}" for i, b in enumerate(scn["beats"], 1))
    sim_ctx.add_message(role="system", content=SIM_PROMPT.format(persona=scn["persona"], beats=beats))

    events: list[dict] = []
    error = None
    line = "Hello?"
    sim_ctx.add_message(role="assistant", content=line)
    try:
        async with AgentSession(
            llm=cascade_llm(),
            userdata=state,
            conn_options=SessionConnectOptions(llm_conn_options=APIConnectOptions(max_retry=4, retry_interval=3.0)),
        ) as session:
            await session.start(LumenAgent(state))
            for _ in range(MAX_TURNS):
                events.append({"role": "borrower", "text": line})
                started = time.perf_counter()
                result = await session.run(user_input=line)
                took_ms = round((time.perf_counter() - started) * 1000)
                said = []
                for ev in result.events:
                    if isinstance(ev, ChatMessageEvent) and ev.item.role == "assistant":
                        said.append(ev.item.text_content or "")
                        events.append({"role": "agent", "text": ev.item.text_content or ""})
                    elif isinstance(ev, FunctionCallEvent):
                        events.append({"role": "tool_call", "name": ev.item.name, "args": ev.item.arguments})
                    elif isinstance(ev, FunctionCallOutputEvent):
                        events.append({"role": "tool_result", "name": ev.item.name, "output": ev.item.output})
                events.append({"role": "turn", "ms": took_ms})
                if state.ended:
                    break
                sim_ctx.add_message(role="user", content=" ".join(said) or "(silence)")
                line = await _say(sim, sim_ctx)
                if "[HANGUP]" in line or not line:
                    state.end_reason = state.end_reason or "borrower hung up"
                    break
                sim_ctx.add_message(role="assistant", content=line)
    except Exception as e:  # a crashed run is a result, not a reason to stop the suite
        error = f"{type(e).__name__}: {e}"

    run = {
        "scenario": scn["id"],
        "run": n,
        "borrower": scn["borrower"],
        "expect": scn["expect"],
        "must_not_disclose": bool(scn.get("must_not_disclose")),
        "outcome": state.record(),
        "ended_by": state.end_reason or "turn limit",
        "events": events,
        "error": error,
    }
    (out_dir / f"{scn['id']}__{n}.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    status = run["outcome"]["outcome"] + (f"  ERROR {error[:80]}" if error else "")
    print(f"  {scn['id']:<22} run {n}: {status}")
    return run


async def main() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    args = parser.parse_args()

    scenarios = load_scenarios()
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    out_dir = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    out_dir.mkdir(parents=True)
    print(f"{len(scenarios)} scenarios x {args.runs} runs -> {out_dir}")

    gate = asyncio.Semaphore(PARALLEL)

    async def limited(scn: dict, n: int) -> dict:
        async with gate:
            return await run_once(scn, n, out_dir)

    async with utils.http_context.open():
        # Scenarios that change the environment (e.g. a failing payment gateway) run
        # one at a time after the rest, so they can't leak into parallel runs.
        plain = [s for s in scenarios if not s.get("env")]
        await asyncio.gather(*(limited(s, n) for s in plain for n in range(1, args.runs + 1)))
        for scn in (s for s in scenarios if s.get("env")):
            saved = {k: os.environ.get(k) for k in scn["env"]}
            os.environ.update(scn["env"])
            try:
                for n in range(1, args.runs + 1):
                    await run_once(scn, n, out_dir)
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
    return out_dir


if __name__ == "__main__":
    out = asyncio.run(main())
    from evals import score

    asyncio.run(score.main(out))
