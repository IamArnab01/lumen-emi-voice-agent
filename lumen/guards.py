"""Ends calls cleanly: silence nudges, full goodbyes, a hard length limit.

Ported from StepsAI voice-worker's calls/guards.py, using the framework's own
user-away timer instead of a hand-rolled one.
"""

import asyncio
import logging
from collections.abc import Coroutine

from livekit.agents import AgentSession, JobContext, UserStateChangedEvent

from lumen.assistant import CallState, caller_language

logger = logging.getLogger("lumen.guards")

SILENCE_SECONDS = 7.0
SILENCE_NUDGES = 2
MAX_CALL_SECONDS = 8 * 60
# Audio that finished playing is still in flight to the caller; hanging up at
# once clips the last word of the goodbye.
GOODBYE_TAIL_SECONDS = 1.0
GOODBYE_PLAYOUT_TIMEOUT = 20.0

LINES = {
    "en": {
        "still_there": "Hello, are you still there?",
        "no_response": "I can't hear you, so I'll end the call now. We'll try you again later. Goodbye.",
        "time_up": "I'm sorry, I need to wrap up this call now. Thank you for your time. Goodbye.",
    },
    "hi": {
        "still_there": "हैलो, क्या आप line पर हैं?",
        "no_response": "आपकी आवाज़ नहीं आ रही, इसलिए मैं call ख़त्म कर रही हूँ। हम बाद में फिर से call करेंगे। नमस्ते।",
        "time_up": "माफ़ कीजिए, अब मुझे यह call ख़त्म करनी होगी। आपके समय के लिए धन्यवाद। नमस्ते।",
    },
}


class CallGuards:
    def __init__(self, ctx: JobContext, session: AgentSession, state: CallState) -> None:
        self._ctx = ctx
        self._session = session
        self._state = state
        self._nudges = 0
        self._ending = False
        self._hung_up = False
        self._tasks: set[asyncio.Task] = set()
        session.on("user_state_changed", self._on_user_state)
        state.hang_up = self.hang_up_soon

    def start(self) -> None:
        self._spawn(self._limit_call_length())

    def _line(self, key: str) -> str:
        lang = caller_language(self._session.history, self._state.borrower.language)
        return LINES[lang][key]

    def _on_user_state(self, ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            self._nudges = 0
        elif ev.new_state == "away" and not self._ending:
            self._spawn(self._nudge())

    async def _nudge(self) -> None:
        self._nudges += 1
        if self._nudges > SILENCE_NUDGES:
            await self.goodbye(self._line("no_response"), "silence")
            return
        await self._session.say(self._line("still_there")).wait_for_playout()
        # The away timer only re-arms from "listening"; flip back, then arm it again.
        self._session.reset_away_timer()
        self._session.reset_away_timer()

    async def _limit_call_length(self) -> None:
        await asyncio.sleep(MAX_CALL_SECONDS)
        await self.goodbye(self._line("time_up"), "time_limit")

    async def goodbye(self, line: str, reason: str) -> None:
        if self._ending:
            return
        self._ending = True
        self._state.end_reason = reason
        logger.info("ending call %s: %s", self._state.call_id, reason)
        # Not interruptible on the cascade, so "ok thanks" over it doesn't cut it short.
        # (Ultravox's server-side turn detection ignores this flag.)
        speech = self._session.say(line, allow_interruptions=False)
        try:
            await asyncio.wait_for(speech.wait_for_playout(), GOODBYE_PLAYOUT_TIMEOUT)
        except TimeoutError:
            logger.warning("goodbye never finished playing; hanging up anyway")
        await self._hang_up()

    def hang_up_soon(self) -> None:
        """Called by the end_call tool, after the agent's own goodbye has played."""
        self._ending = True
        self._spawn(self._hang_up())

    async def _hang_up(self) -> None:
        if self._hung_up:
            return
        self._hung_up = True
        await asyncio.sleep(GOODBYE_TAIL_SECONDS)
        try:
            await self._ctx.delete_room()
        except Exception:  # console mode has no real room to delete
            logger.debug("delete_room failed", exc_info=True)
        self._ctx.shutdown(reason=self._state.end_reason or "call ended")

    def _spawn(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task
