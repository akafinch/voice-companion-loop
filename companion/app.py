"""The bot's one web process: the talk page, its WebRTC signalling, each call's relay settings and the
active character, all on one origin. Run it with `uvicorn companion.app:app --port 8080`.
"""

import httpx
from fastapi import BackgroundTasks, FastAPI, Response
from fastapi.staticfiles import StaticFiles
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from companion import character, relay, voices
from companion.pipeline import run_bot
from companion.studio import router as studio

app = FastAPI(title="Voice Companion Loop")

# The bot's end of each call gets no ICE server, so it announces only its pod address, which the
# relay reaches inside the cluster. pipecat hands aiortc an empty list for None, and aiortc adds
# Google's public STUN server only when it is given no list at all:
# https://github.com/aiortc/aiortc/blob/1.15.0/src/aiortc/rtcicetransport.py
webrtc = SmallWebRTCRequestHandler(ice_servers=None)


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    """Answer the page's WebRTC offer, then run the call once the answer has been sent."""

    async def start_call(connection: SmallWebRTCConnection):
        background_tasks.add_task(run_bot, connection)

    return await webrtc.handle_web_request(request, start_call)


@app.patch("/api/offer")
async def ice_candidates(request: SmallWebRTCPatchRequest):
    await webrtc.handle_patch_request(request)
    return {"status": "success"}


@app.get("/api/relay")
async def relay_settings() -> list[dict]:
    """The ICE servers for the page's next call, fetched per call: the relay's address can change."""
    return await relay.ice_servers()


@app.get("/api/character")
def read_character() -> character.Character:
    return character.get()


@app.put("/api/character")
def replace_character(new: character.Character) -> character.Character:
    character.replace(new.persona, new.voice)
    return character.get()


@app.delete("/api/character")
def reset_character() -> character.Character:
    character.reset()
    return character.get()


@app.get("/healthz")
async def healthz():
    """Ready once VoiceStudio holds the default voice; until then 503, and the probe asks again."""
    try:
        await voices.ensure_default_voice()
    except httpx.HTTPError as error:
        return Response(f"default voice not ready: {error}", status_code=503)
    return {"status": "ok"}


# ── Include further routers here, above the static mount: mounted at "/", it would answer their
# ── paths itself, and POST with 405. The character page's router goes here.
app.include_router(studio.router)

# Last, the built pages. Routes match in order, so every route above wins. check_dir=False lets the
# bot start, and the tests run, before web/ is built.
app.mount("/", StaticFiles(directory="web/dist", html=True, check_dir=False), name="pages")
