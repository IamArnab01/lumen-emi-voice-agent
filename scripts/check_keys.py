"""Checks every key in .env against its provider, without printing secrets.

    python scripts/check_keys.py
"""

import asyncio
import os
import sys

import aiohttp
from dotenv import load_dotenv

load_dotenv()


def _ok(name: str, detail: str = "") -> None:
    print(f"  OK    {name} {detail}")


def _bad(name: str, detail: str) -> None:
    print(f"  FAIL  {name}: {detail}")


async def livekit() -> bool:
    from livekit import api

    try:
        async with api.LiveKitAPI() as lk:
            rooms = await lk.room.list_rooms(api.ListRoomsRequest())
        _ok("LiveKit", f"({os.environ['LIVEKIT_URL']}, {len(rooms.rooms)} active rooms)")
        return True
    except Exception as e:
        _bad("LiveKit", repr(e)[:200])
        return False


async def ultravox(http: aiohttp.ClientSession) -> bool:
    want = os.environ.get("ULTRAVOX_VOICE", "Priya")
    headers = {"X-API-Key": os.environ.get("ULTRAVOX_API_KEY", "")}
    voices: list[dict] = []
    url = "https://api.ultravox.ai/api/voices?pageSize=200"
    while url:
        async with http.get(url, headers=headers) as r:
            if r.status != 200:
                _bad("Ultravox", f"HTTP {r.status}: {(await r.text())[:200]}")
                return False
            page = await r.json()
        voices += page.get("results", [])
        url = page.get("next")
    names = {v.get("name") for v in voices}
    _ok("Ultravox", f"({len(voices)} voices)")
    print(f"        voice {want!r}: {'found' if want in names else 'NOT FOUND'}")
    indian = sorted(
        f"{v.get('name')} [{v.get('languageLabel') or v.get('primaryLanguage') or '?'}, {v.get('provider', '?')}]"
        for v in voices
        if any(t in f"{v.get('name')} {v.get('description')} {v.get('primaryLanguage')}".lower() for t in ("hindi", "indian", "hi-", "hi_in", "india"))
    )
    print("        Indian/Hindi voices:", *(indian or ["none matched"]), sep="\n          ")
    return want in names


async def sarvam(http: aiohttp.ClientSession) -> bool:
    body = {"text": "नमस्ते", "target_language_code": "hi-IN", "model": "bulbul:v3", "speaker": os.environ.get("SARVAM_SPEAKER", "ritu")}
    async with http.post(
        "https://api.sarvam.ai/text-to-speech",
        json=body,
        headers={"api-subscription-key": os.environ.get("SARVAM_API_KEY", "")},
    ) as r:
        if r.status != 200:
            _bad("Sarvam", f"HTTP {r.status}: {(await r.text())[:200]}")
            return False
    _ok("Sarvam", f"(bulbul:v3 / {body['speaker']} speaks Hindi)")
    return True


async def main() -> int:
    print("checking keys from .env")
    async with aiohttp.ClientSession() as http:
        results = [await livekit(), await ultravox(http), await sarvam(http)]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
