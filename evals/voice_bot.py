"""A borrower that phones the agent with its voice, to measure what a caller feels.

It joins a LiveKit room as the borrower, speaks scripted lines (Sarvam TTS, a male
voice so it's never confused with the agent) and listens to the agent's audio:

  reply latency   end of the borrower's line -> first agent audio (median, p90)
  barge-in stop   borrower starts talking over the agent -> agent audio stops
  talk-over       agent starts speaking while the borrower is still talking

Timing is taken from the audio itself, on the caller's side of LiveKit Cloud, so it
includes the network both ways, the turn detector's wait and the model: the real wait.
Needs the worker running (start.ps1 voicebot does that).

    python -m evals.voice_bot --pipeline ultravox --script happy
"""

import argparse
import asyncio
import base64
import io
import json
import os
import statistics
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import numpy as np
from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "logs" / "voicebot"
CALLS_DIR = ROOT / "logs" / "calls"

RATE = 16000
FRAME_MS = 10
LOUD_RMS = 500  # int16 RMS; agent speech is far above, line noise far below
TURN_GAP_S = 1.5  # agent silent this long = its turn is over, the borrower may speak
STOP_GAP_S = 0.3  # agent silent this long after a barge-in = it stopped
# Cut in only once the agent has talked this long without a pause: longer than a filler
# like "Let me just check that for you" (~1.6 s), which the first runs hit instead.
BARGE_AFTER_S = 2.5
AGENT_TURN_TIMEOUT_S = 25.0


@dataclass
class Line:
    text: str
    barge_in: bool = False  # say it over the agent's next reply instead of after it


SCRIPTS: dict[str, tuple[str, str, list[Line]]] = {
    # name: (borrower, language, lines)
    "happy": (
        "B001",
        "en-IN",
        [
            Line("Yes, speaking."),
            Line("Sure. It's the twelfth of March, nineteen ninety."),
            Line("Sorry, sorry, can you just send me the payment link?", barge_in=True),
            Line("Yes please, send it."),
            Line("No, that's all. Thank you."),
        ],
    ),
    "hindi": (
        "B003",
        "hi-IN",
        [
            Line("हाँ, मैं सुरेश बोल रहा हूँ।"),
            Line("मेरी date of birth है पाँच November, उन्नीस सौ अठहत्तर।"),
            Line("ठीक है, मैं दस December तक pay कर दूँगा।"),
            Line("हाँ, link भेज दीजिए।"),
            Line("नहीं, बस इतना ही। धन्यवाद।"),
        ],
    ),
}


@dataclass
class Ear:
    """Tracks when the agent is audible, from its audio track."""

    loud_at: float = 0.0  # last loud frame
    onsets: list[float] = field(default_factory=list)
    speaking: bool = False

    def hear(self, frame: rtc.AudioFrame, now: float) -> None:
        samples = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32)
        loud = samples.size and float(np.sqrt(np.mean(samples**2))) > LOUD_RMS
        if loud:
            if not self.speaking:
                self.onsets.append(now)
            self.speaking = True
            self.loud_at = now
        elif self.speaking and now - self.loud_at > STOP_GAP_S:
            self.speaking = False

    def onset_after(self, t: float) -> float | None:
        return next((o for o in self.onsets if o > t), None)


async def synthesize(http: aiohttp.ClientSession, text: str, language: str) -> np.ndarray:
    body = {
        "text": text,
        "target_language_code": language,
        "model": "bulbul:v3",
        "speaker": "rahul",
        "speech_sample_rate": RATE,
    }
    async with http.post(
        "https://api.sarvam.ai/text-to-speech",
        json=body,
        headers={"api-subscription-key": os.environ["SARVAM_API_KEY"]},
    ) as r:
        r.raise_for_status()
        wav_b64 = (await r.json())["audios"][0]
    with wave.open(io.BytesIO(base64.b64decode(wav_b64))) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1, "unexpected TTS format"
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    # Trim the TTS clip's silent edges: trailing silence would make our line "end" later
    # than the speech did, and flatter every latency number measured from it.
    loud = np.flatnonzero(np.abs(pcm) > LOUD_RMS)
    return pcm[loud[0] : loud[-1] + 1] if loud.size else pcm


async def say(source: rtc.AudioSource, pcm: np.ndarray) -> float:
    """Play `pcm` into the room in real time; return when the last sample went out."""
    step = RATE * FRAME_MS // 1000
    for i in range(0, len(pcm), step):
        chunk = pcm[i : i + step]
        if len(chunk) < step:
            chunk = np.pad(chunk, (0, step - len(chunk)))
        await source.capture_frame(rtc.AudioFrame(chunk.tobytes(), RATE, 1, step))
    await source.wait_for_playout()
    return time.monotonic()


async def wait_turn_over(ear: Ear, since: float) -> bool:
    """Wait until the agent has spoken after `since` and then gone quiet for TURN_GAP_S."""
    deadline = time.monotonic() + AGENT_TURN_TIMEOUT_S
    while time.monotonic() < deadline:
        now = time.monotonic()
        if ear.onset_after(since) and not ear.speaking and now - ear.loud_at > TURN_GAP_S:
            return True
        await asyncio.sleep(0.02)
    return False


async def _wait_onset(ear: Ear, since: float) -> float | None:
    deadline = time.monotonic() + AGENT_TURN_TIMEOUT_S
    while time.monotonic() < deadline:
        if onset := ear.onset_after(since):
            return onset
        await asyncio.sleep(0.01)
    return None


async def _wait_sustained(ear: Ear, seconds: float) -> None:
    """Return once the agent has been speaking without a pause for `seconds`."""
    deadline = time.monotonic() + AGENT_TURN_TIMEOUT_S
    while time.monotonic() < deadline:
        if ear.speaking and time.monotonic() - ear.onsets[-1] >= seconds:
            return
        await asyncio.sleep(0.01)


async def _first_silence(ear: Ear, since: float) -> float:
    """Time of the agent's last loud frame before it first went quiet after `since`."""
    while ear.speaking:
        await asyncio.sleep(0.01)
    return max(ear.loud_at, since)


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


async def run(pipeline: str, script: str) -> dict:
    borrower_id, language, lines = SCRIPTS[script]
    room_name = f"bot-{pipeline}-{script}-{uuid.uuid4().hex[:6]}"
    started_at = time.time()

    async with aiohttp.ClientSession() as http:
        audio = [await synthesize(http, line.text, language) for line in lines]

    lk = api.LiveKitAPI()
    await lk.room.create_room(
        api.CreateRoomRequest(name=room_name, metadata=json.dumps({"borrower_id": borrower_id}), empty_timeout=30)
    )
    token = (
        api.AccessToken()
        .with_identity("borrower-bot")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )

    room = rtc.Room()
    ear = Ear()
    heard: list[dict] = []
    agent_audio = asyncio.Event()

    @room.on("track_subscribed")
    def _on_track(track: rtc.Track, *_):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            agent_audio.set()

            async def listen() -> None:
                async for ev in rtc.AudioStream(track, sample_rate=RATE, num_channels=1):
                    ear.hear(ev.frame, time.monotonic())

            asyncio.create_task(listen())

    def _on_text(reader: rtc.TextStreamReader, identity: str) -> None:
        async def read() -> None:
            try:
                text = await reader.read_all()
            except rtc.data_stream.StreamError:  # stream cut off when the call hangs up
                return
            if (reader.info.attributes or {}).get("lk.transcription_final") == "true" and text.strip():
                heard.append({"who": identity, "text": text.strip()})

        asyncio.create_task(read())

    room.register_text_stream_handler("lk.transcription", _on_text)
    await room.connect(os.environ["LIVEKIT_URL"], token)
    source = rtc.AudioSource(RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("borrower", source)
    await room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )

    turns: list[dict] = []
    try:
        await asyncio.wait_for(agent_audio.wait(), 40)
        # Outbound call: the agent opens. Wait for its greeting to finish.
        await wait_turn_over(ear, since=0.0)
        for i, (line, pcm) in enumerate(zip(lines, audio, strict=True)):
            turn: dict = {"line": line.text}
            start = time.monotonic()
            if line.barge_in:
                # The agent is mid-reply (see below): talk over it and time how long
                # it takes to go quiet.
                was_speaking = ear.speaking
                stop = asyncio.create_task(_first_silence(ear, start))
                end = await say(source, pcm)
                stopped = stop.result() if stop.done() else None
                stop.cancel()
                turn["barge_in"] = {
                    "agent_was_speaking": was_speaking,
                    "stop_ms": round((stopped - start) * 1000) if was_speaking and stopped else None,
                    "agent_still_talking_when_we_finished": ear.speaking,
                }
            else:
                end = await say(source, pcm)
                turn["talked_over_borrower"] = any(start < o < end for o in ear.onsets)

            next_barges = i + 1 < len(lines) and lines[i + 1].barge_in
            if next_barges:
                # Don't wait for the reply to finish: cut into a sentence that has run
                # BARGE_AFTER_S without a pause (skips short fillers like "let me check").
                onset = await _wait_onset(ear, end)
                turn["agent_replied"] = onset is not None
                await _wait_sustained(ear, BARGE_AFTER_S)
            else:
                turn["agent_replied"] = await wait_turn_over(ear, since=end)
                onset = ear.onset_after(end)
            turn["reply_ms"] = round((onset - end) * 1000) if onset else None
            turns.append(turn)
        await asyncio.sleep(3)  # let the goodbye play and the agent hang up
    finally:
        await room.disconnect()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception:
            pass  # the agent may already have closed it
        await lk.aclose()

    outcome = await _latest_call(borrower_id, after=started_at)
    replies = [t["reply_ms"] for t in turns if t.get("reply_ms") is not None]
    stops = [t["barge_in"]["stop_ms"] for t in turns if t.get("barge_in", {}).get("stop_ms") is not None]
    report = {
        "pipeline": pipeline,
        "script": script,
        "room": room_name,
        "reply_ms": {"median": statistics.median(replies) if replies else None, "p90": _pct(replies, 90), "all": replies},
        "barge_in_stop_ms": stops,
        "talk_overs": sum(t.get("talked_over_borrower", False) for t in turns),
        "turns": turns,
        "heard": heard,
        "outcome": outcome,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{room_name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def _latest_call(borrower_id: str, after: float) -> dict | None:
    """The outcome the worker saved for this call (it writes when the room closes)."""
    for _ in range(30):
        files = sorted(CALLS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if CALLS_DIR.exists() else []
        for f in files:
            if f.stat().st_mtime < after:
                break
            data = json.loads(f.read_text(encoding="utf-8"))
            if data["outcome"]["borrower_id"] == borrower_id:
                return data["outcome"]
        await asyncio.sleep(1)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", default=os.environ.get("PIPELINE", "ultravox"))
    parser.add_argument("--script", default="happy", choices=sorted(SCRIPTS))
    args = parser.parse_args()
    report = asyncio.run(run(args.pipeline, args.script))
    print(f"\n=== voice bot: {report['pipeline']} / {report['script']}  ({report['room']})")
    for t in report["turns"]:
        extra = f"  barge-in: {t['barge_in']}" if "barge_in" in t else ("  [agent talked over borrower]" if t.get("talked_over_borrower") else "")
        print(f"  {t['reply_ms']!s:>6} ms  {t['line'][:60]}{extra}")
    print(f"  reply latency: median {report['reply_ms']['median']} ms, p90 {report['reply_ms']['p90']} ms")
    print(f"  barge-in stop: {report['barge_in_stop_ms']} ms; talk-overs: {report['talk_overs']}")
    print(f"  outcome: {report['outcome']}")
    print("  heard:")
    for h in report["heard"]:
        print(f"    {h['who']}: {h['text']}")


if __name__ == "__main__":
    main()
