"""LiveKit worker: one job per call.

    python agent.py console          talk to it with your laptop mic, no server needed
    python agent.py start            register with LiveKit Cloud; join from the Agents Playground
    python agent.py download-files   fetch local model weights (VAD, turn detector fallback)

Which borrower is called: job or room metadata {"borrower_id": "B003"}, else BORROWER in .env.
"""

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from livekit.agents import AgentServer, JobContext, JobProcess, cli  # noqa: E402

from lumen.assistant import CallState, LumenAgent  # noqa: E402
from lumen.data import load_borrowers, today  # noqa: E402
from lumen.guards import CallGuards  # noqa: E402
from lumen.pipelines import build_session, load_vad, selected  # noqa: E402
from lumen.timing import log_turn_latency  # noqa: E402

logger = logging.getLogger("lumen")
CALLS_DIR = Path(__file__).resolve().parent / "logs" / "calls"


def _prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = load_vad()


server = AgentServer(setup_fnc=_prewarm)


def _borrower_id(ctx: JobContext) -> str:
    for raw in (ctx.job.metadata, ctx.room.metadata):
        try:
            borrower_id = json.loads(raw or "{}").get("borrower_id")
        except (ValueError, AttributeError):
            borrower_id = None
        if borrower_id:
            return borrower_id
    return os.environ.get("BORROWER", "B001")


@server.rtc_session()
async def call(ctx: JobContext) -> None:
    await ctx.connect()
    pipeline = selected()
    borrowers = load_borrowers()
    borrower_id = _borrower_id(ctx)
    if borrower_id not in borrowers:
        raise ValueError(f"unknown borrower {borrower_id!r}; have {sorted(borrowers)}")

    state = CallState(borrower=borrowers[borrower_id], today=today())
    ctx.log_context_fields = {"call_id": state.call_id, "borrower": borrower_id, "pipeline": pipeline}
    logger.info("calling %s (%s) on %s", state.borrower.name, borrower_id, pipeline)

    session = build_session(pipeline, state, ctx.proc.userdata["vad"])
    guards = CallGuards(ctx, session, state)
    log_turn_latency(session, state.call_id, pipeline)

    async def on_shutdown() -> None:
        record = state.finalize()
        CALLS_DIR.mkdir(parents=True, exist_ok=True)
        (CALLS_DIR / f"{state.call_id}.json").write_text(
            json.dumps(
                {"pipeline": pipeline, "outcome": record, "transcript": session.history.to_dict()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("outcome: %s", json.dumps(record, ensure_ascii=False))

    ctx.add_shutdown_callback(on_shutdown)

    await session.start(agent=LumenAgent(state), room=ctx.room)
    guards.start()
    if pipeline == "cascade":
        # Outbound call: the agent opens. Ultravox does this itself (first_speaker).
        session.generate_reply()


if __name__ == "__main__":
    cli.run_app(server)
