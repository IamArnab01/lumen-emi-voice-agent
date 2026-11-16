"""Time to first token for candidate cascade LLMs, with the agent's real prompt and tools.

The LLM is the largest share of the cascade's reply time (voice bot: 1.0-1.9 s of a
~3.7 s reply on gpt-4.1-mini), so the model is chosen on measurement, not reputation.

    python -m evals.llm_ttft [model ...]
"""

import asyncio
import statistics
import sys
import time

from livekit.agents import inference, llm, utils

from lumen.assistant import CallState, LumenAgent
from lumen.data import load_borrowers, today

CANDIDATES = [
    "openai/gpt-4.1-mini",
    "openai/gpt-4.1-nano",
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash-lite",
    "google/gemini-3.1-flash-lite",
    "google/gemini-2.5-flash",
]
RUNS = 5
HISTORY = [
    ("user", "Hello?"),
    ("assistant", "Hello, this is Asha calling from Lumen Finance. Am I speaking with Rohan Mehta?"),
    ("user", "Yes, speaking. What's this about?"),
]


async def first_token(model: llm.LLM, agent: LumenAgent) -> tuple[float, str]:
    ctx = llm.ChatContext()
    ctx.add_message(role="system", content=agent.instructions)
    for role, text in HISTORY:
        ctx.add_message(role=role, content=text)
    start = time.perf_counter()
    first = None
    said = ""
    async with model.chat(chat_ctx=ctx, tools=list(agent.tools)) as stream:
        async for chunk in stream:
            if chunk.delta and (chunk.delta.content or chunk.delta.tool_calls):
                first = first or time.perf_counter() - start
                said += chunk.delta.content or ""
    return first or float("nan"), said


async def main() -> None:
    models = sys.argv[1:] or CANDIDATES
    agent = LumenAgent(CallState(borrower=load_borrowers()["B001"], today=today()))
    rows = []
    async with utils.http_context.open():
        for name in models:
            model = inference.LLM(model=name, extra_kwargs={"temperature": 0.4})
            times, sample = [], ""
            for _ in range(RUNS):
                try:
                    t, said = await first_token(model, agent)
                except Exception as e:  # a model missing from the catalog shouldn't stop the rest
                    print(f"{name}: {type(e).__name__}: {str(e)[:120]}")
                    break
                times.append(t * 1000)
                sample = sample or said
            if times:
                rows.append((statistics.median(times), name, min(times), max(times), sample))
    print(f"\n{'model':<32} {'median':>7} {'min':>6} {'max':>6}   first reply")
    for med, name, lo, hi, sample in sorted(rows):
        print(f"{name:<32} {med:>6.0f}ms {lo:>5.0f} {hi:>6.0f}   {sample[:70]!r}")


if __name__ == "__main__":
    asyncio.run(main())
