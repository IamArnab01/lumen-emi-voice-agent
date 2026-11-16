"""Admin page: pick a borrower, place the call, talk as the borrower in the browser.

    python web/server.py     -> http://localhost:8090   (needs the agent worker running)

"Place call" creates a LiveKit room tagged with the borrower; the agent worker is
dispatched into it and opens the conversation. No telephony: the browser tab is the
borrower's phone, which the assignment allows.
"""

import json
import os
import sys
import uuid
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from livekit import api

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from lumen.data import load_borrowers, loan_facts, today  # noqa: E402
from lumen.tools import OUTCOMES_FILE  # noqa: E402

PORT = 8090


async def page(_: web.Request) -> web.FileResponse:
    return web.FileResponse(Path(__file__).parent / "index.html")


async def borrowers(_: web.Request) -> web.Response:
    on = today()
    rows = []
    for b in load_borrowers().values():
        f = loan_facts(b, on)
        rows.append(
            {
                "id": b.id,
                "name": b.name,
                "loan_type": b.loan_type,
                "emi": f["emi_amount_display"],
                "due": b.due_date.isoformat(),
                "status": f["status"] + (f" ({f['days_overdue']}d)" if f["days_overdue"] else ""),
                "language": b.language,
            }
        )
    return web.json_response(rows)


async def place_call(request: web.Request) -> web.Response:
    borrower_id = (await request.json()).get("borrower_id")
    if borrower_id not in load_borrowers():
        return web.json_response({"error": "unknown borrower"}, status=400)
    room = f"call-{borrower_id}-{uuid.uuid4().hex[:6]}"
    async with api.LiveKitAPI() as lk:
        # The agent reads the borrower from the room metadata (agent.py).
        await lk.room.create_room(
            api.CreateRoomRequest(name=room, metadata=json.dumps({"borrower_id": borrower_id}), empty_timeout=60)
        )
    token = (
        api.AccessToken()
        .with_identity(f"borrower-{borrower_id}")
        .with_name(borrower_id)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return web.json_response({"url": os.environ["LIVEKIT_URL"], "token": token, "room": room})


async def outcomes(_: web.Request) -> web.Response:
    if not OUTCOMES_FILE.exists():
        return web.json_response([])
    lines = OUTCOMES_FILE.read_text(encoding="utf-8").splitlines()[-25:]
    return web.json_response([json.loads(line) for line in reversed(lines) if line.strip()])


def main() -> None:
    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/api/borrowers", borrowers)
    app.router.add_post("/api/call", place_call)
    app.router.add_get("/api/outcomes", outcomes)
    print(f"Lumen call console on http://localhost:{PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
