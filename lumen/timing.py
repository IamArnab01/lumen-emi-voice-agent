"""Per-turn latency, measured where the caller feels it: from when they stop
speaking to when the agent starts speaking. Written to logs/latency.jsonl.

Approach from StepsAI voice-worker's observability/turn_timing.py. The framework's
own per-message metrics (LLM time-to-first-token, TTS time-to-first-byte, e2e) are
logged alongside, when the pipeline reports them (the cascade does; Ultravox reports less).
"""

import json
import time
from pathlib import Path

from livekit.agents import (
    AgentSession,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    UserStateChangedEvent,
)

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "latency.jsonl"
METRIC_KEYS = ("e2e_latency", "llm_node_ttft", "tts_node_ttfb", "transcription_delay", "end_of_turn_delay")


def _write(row: dict) -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def log_turn_latency(session: AgentSession, call_id: str, pipeline: str) -> None:
    stopped: list[float] = []  # when the caller last stopped speaking, if not yet answered
    turn = 0

    def on_user_state(ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            stopped.clear()
        elif ev.old_state == "speaking":
            stopped[:] = [ev.created_at]

    def on_agent_state(ev: AgentStateChangedEvent) -> None:
        nonlocal turn
        if ev.new_state == "speaking" and stopped:
            turn += 1
            ms = round((ev.created_at - stopped[0]) * 1000)
            stopped.clear()
            _write({"call_id": call_id, "pipeline": pipeline, "turn": turn, "reply_ms": ms, "at": time.time()})

    def on_item(ev: ConversationItemAddedEvent) -> None:
        metrics = getattr(ev.item, "metrics", None) or {}
        found = {f"{k}_ms": round(metrics[k] * 1000) for k in METRIC_KEYS if isinstance(metrics.get(k), (int, float))}
        if found:
            _write({"call_id": call_id, "pipeline": pipeline, "role": getattr(ev.item, "role", ""), **found})

    session.on("user_state_changed", on_user_state)
    session.on("agent_state_changed", on_agent_state)
    session.on("conversation_item_added", on_item)
