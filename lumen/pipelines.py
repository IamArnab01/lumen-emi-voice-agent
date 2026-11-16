"""The two ways the agent hears and speaks, behind one switch (PIPELINE in .env).

ultravox: one speech-to-speech model (Ultravox) does listening, thinking and
          speaking. Lowest latency, least control.
cascade:  Sarvam STT -> LLM (LiveKit Inference gemini-2.5-flash, or Groq) -> Sarvam TTS,
          with LiveKit's turn detector.
          More hops, but every stage is visible and tunable, and Sarvam is strong
          on Indian English, Hindi and code-mixed speech.
Both run the same LumenAgent, tools and guards.
"""

import os

from livekit.agents import AgentSession, UserInputTranscribedEvent, inference
from livekit.plugins import groq, sarvam, silero, ultravox

from lumen.assistant import _DEVANAGARI, CallState
from lumen.guards import SILENCE_SECONDS

PIPELINES = ("ultravox", "cascade")


def selected() -> str:
    name = os.environ.get("PIPELINE", "ultravox").strip().lower()
    if name not in PIPELINES:
        raise ValueError(f"PIPELINE must be one of {PIPELINES}, got {name!r}")
    return name


def groq_llm() -> groq.LLM:
    # Llama 3.3 was retired from Groq; gpt-oss is open-weight, strong at tool calls,
    # and the plugin runs it at reasoning_effort="low" so it doesn't stall the turn.
    # Free tier: 8k tokens/min (about 4 turns/min), fine for tests, too slow for live calls.
    return groq.LLM(model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"), temperature=0.4)


def cascade_llm():
    """The cascade's brain. CASCADE_LLM is a LiveKit Inference model id (billed to the
    LiveKit project's credits, no extra key) or "groq/<model>" for Groq."""
    name = os.environ.get("CASCADE_LLM", "google/gemini-2.5-flash")
    if name.startswith("groq/"):
        os.environ["GROQ_MODEL"] = name.removeprefix("groq/")
        return groq_llm()
    # gemini-2.5-flash: fastest first token with our prompt and tools (evals/llm_ttft.py:
    # median 773 ms vs 1810 ms for gpt-4.1-mini) and passes the smoke scenarios.
    return inference.LLM(model=name, extra_kwargs={"temperature": 0.4})


def load_vad() -> silero.VAD:
    # 0.3 s of silence ends a speech segment (was 0.4); the turn detector, not VAD,
    # decides whether the caller has actually finished.
    return silero.VAD.load(min_speech_duration=0.15, min_silence_duration=0.3, activation_threshold=0.55)


def build_session(name: str, state: CallState, vad: silero.VAD) -> AgentSession:
    return _ultravox(state, vad) if name == "ultravox" else _cascade(state, vad)


def _ultravox_model(**overrides) -> ultravox.realtime.RealtimeModel:
    options = {
        "model": os.environ.get("ULTRAVOX_MODEL", "fixie-ai/ultravox"),
        # Indian-accented English (Inworld). "Riya" is the Hindi-first alternative.
        "voice": os.environ.get("ULTRAVOX_VOICE", "Priya"),
        # Outbound call: the agent opens, from the system prompt (Uniweave's approach;
        # avoids generate_reply(instructions=...), which Ultravox echoes as user text).
        "first_speaker": "FIRST_SPEAKER_AGENT",
        "temperature": 0.4,
        "input_sample_rate": 16000,
        "output_sample_rate": 24000,
    }
    return ultravox.realtime.RealtimeModel(**(options | overrides))


def ultravox_text_model() -> ultravox.realtime.RealtimeModel:
    """Ultravox answering in text, caller speaks first: for smoke tests and evals."""
    return _ultravox_model(output_medium="text", first_speaker="FIRST_SPEAKER_USER")


def _ultravox(state: CallState, vad: silero.VAD) -> AgentSession:
    return AgentSession(llm=_ultravox_model(), vad=vad, userdata=state, user_away_timeout=SILENCE_SECONDS)


def cascade_stt():
    """CASCADE_STT: a LiveKit Inference STT id (default "deepgram/nova-3") or "sarvam".

    Voice bot, same script: Deepgram's final transcript ~0.29 s after the caller stops,
    Sarvam's ~0.8 s even with flush_signal. Sarvam stays as the option for heavier Hindi."""
    name = os.environ.get("CASCADE_STT", "deepgram/nova-3")
    if name == "sarvam":
        # language "unknown" auto-detects; codemix keeps "मेरा EMI kal" as spoken.
        # flush_signal lets the framework flush the stream at end of turn (StepsAI's
        # FlushedSTT idea, now in the plugin). Measured: finals still ~0.8 s after speech.
        return sarvam.STT(model="saaras:v4", language="unknown", mode="codemix", flush_signal=True)
    # "multi" = Deepgram's code-switching mode: English and Hindi in one conversation.
    return inference.STT(model=name, language="multi")


def _cascade(state: CallState, vad: silero.VAD) -> AgentSession:
    tts = sarvam.TTS(
        model=os.environ.get("SARVAM_TTS_MODEL", "bulbul:v3"),
        # v3 speakers are plain names that work across languages, so a mid-call
        # switch to Hindi only changes target_language_code, not the voice.
        speaker=os.environ.get("SARVAM_SPEAKER", "ritu"),
        target_language_code=state.borrower.language,
        output_audio_codec="linear16",  # skip mp3 decode on our side (StepsAI)
        min_buffer_size=30,  # start speaking on a shorter first chunk (StepsAI)
    )
    session = AgentSession(
        stt=cascade_stt(),
        llm=cascade_llm(),
        tts=tts,
        vad=vad,
        userdata=state,
        user_away_timeout=SILENCE_SECONDS,
        turn_handling={
            # Semantic end-of-turn model (multilingual, incl. Hindi): waits through
            # "my date of birth is... uh..." instead of cutting in at the first pause.
            "turn_detection": inference.TurnDetector(),
            # min_delay applies when the detector is confident the turn is over (was 0.4).
            # max_delay caps the wait when the detector thinks the caller isn't done
            # (was 2.5: the voice bot hit it twice on mid-sentence pauses).
            "endpointing": {"min_delay": 0.25, "max_delay": 1.5},
            "interruption": {
                "enabled": True,
                # Stop after 0.3 s of caller speech (was 0.5: the voice bot measured the
                # agent taking 1.13 s to go quiet when cut off mid-reminder).
                "min_duration": 0.3,
                "resume_false_interruption": True,  # a cough doesn't kill the sentence
                "false_interruption_timeout": 1.0,
            },
            # Start thinking on the interim transcript; cuts perceived latency.
            "preemptive_generation": {"enabled": True},
        },
    )

    # Speak Hindi once the caller does; back to English when they switch back. The script
    # is checked too: a multilingual STT may tag mixed speech "multi" rather than "hi".
    def on_transcript(ev: UserInputTranscribedEvent) -> None:
        if not ev.is_final or not ev.transcript.strip():
            return
        hindi = str(ev.language or "").startswith("hi") or bool(_DEVANAGARI.search(ev.transcript))
        tts.update_options(target_language_code="hi-IN" if hindi else "en-IN")

    session.on("user_input_transcribed", on_transcript)
    return session
