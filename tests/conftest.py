"""Stand-ins for every service the bot calls, served over real HTTP on localhost and recording each
request: the chat model and transcription (vLLM's OpenAI-compatible routes), the speech service
(VoiceStudio) and the TURN relay's auth service (STUNner). Each test starts with them empty and
the bot's settings pointed at them.
"""

import io
import json
import threading
import time
import wave
from email.parser import BytesParser
from email.policy import HTTP

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from companion import character, settings, voices
from companion.app import app

class StandIns:
    """What each stand-in was asked, and the voices the VoiceStudio stand-in holds."""

    key = "voicestudio-test-key"
    chat_model = "chat-model"
    stt_model = "stt-model"
    heard = ["Hello, who am I talking to?", "Where are you sailing today?"]  # the reader, turn by turn
    reply = ["Ahoy,", " the sea", " is calm today."]  # the chat model, token by token

    def __init__(self):
        self.url = ""
        self.reset()

    def reset(self):
        self.chat = []  # JSON bodies sent to the chat model
        self.transcriptions = []  # form fields sent for transcription
        self.speech = []  # JSON bodies sent to VoiceStudio's /v1/audio/speech
        self.saved = []  # form fields sent to VoiceStudio's POST /profiles
        self.keys = set()  # Authorization headers VoiceStudio saw
        self.ice = []  # query parameters sent to STUNner's /ice
        self.profiles = {}  # the voices VoiceStudio holds, by id

    def spoken(self):
        """The speech requests that spoke a reply, rather than designed a voice."""
        return [body for body in self.speech if body["response_format"] == "pcm"]


STAND_INS = StandIns()


async def form_fields(request: Request) -> dict:
    """A multipart form's fields, file parts as (filename, bytes), parsed by the standard library."""
    head = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    message = BytesParser(policy=HTTP).parsebytes(head + await request.body())
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        value = part.get_payload(decode=True)
        fields[name] = (part.get_filename(), value) if part.get_filename() else value.decode()
    return fields


def wav(seconds: float, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(bytes(int(seconds * rate) * 2))
    return buffer.getvalue()


async def chat_completions(request: Request):
    STAND_INS.chat.append(await request.json())

    def event(**chunk) -> str:
        chunk = {"id": "reply", "object": "chat.completion.chunk", "created": 0, "model": StandIns.chat_model, **chunk}
        return f"data: {json.dumps(chunk)}\n\n"

    async def stream():
        for token in StandIns.reply:
            yield event(choices=[{"index": 0, "delta": {"content": token}, "finish_reason": None}])
        yield event(choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
        yield event(choices=[], usage={"prompt_tokens": 90, "completion_tokens": 9, "total_tokens": 99})
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def transcriptions(request: Request):
    STAND_INS.transcriptions.append(await form_fields(request))
    heard = StandIns.heard[(len(STAND_INS.transcriptions) - 1) % len(StandIns.heard)]
    return JSONResponse({"text": heard})


async def list_profiles(request: Request):
    STAND_INS.keys.add(request.headers.get("authorization"))
    return JSONResponse(list(STAND_INS.profiles.values()))


async def read_profile(request: Request):
    STAND_INS.keys.add(request.headers.get("authorization"))
    profile = STAND_INS.profiles.get(request.path_params["id"])
    return JSONResponse(profile) if profile else JSONResponse({"detail": "not found"}, status_code=404)


async def save_profile(request: Request):
    STAND_INS.keys.add(request.headers.get("authorization"))
    form = await form_fields(request)
    STAND_INS.saved.append(form)
    profile = {"id": f"{len(STAND_INS.profiles) + 1:08x}", "name": form["name"]}
    STAND_INS.profiles[profile["id"]] = profile
    return JSONResponse(profile)


async def speech(request: Request):
    STAND_INS.keys.add(request.headers.get("authorization"))
    body = await request.json()
    STAND_INS.speech.append(body)
    if body["response_format"] == "wav":
        return Response(wav(2.0), media_type="audio/wav")
    return Response(bytes(int(0.6 * 24000) * 2), media_type="audio/pcm")  # 0.6 s of silence


async def ice(request: Request):
    query = dict(request.query_params)
    STAND_INS.ice.append(query)
    credential = {"username": f"1762300800:{query['username']}", "credential": "aGVsbG8gcmVsYXk="}
    return JSONResponse(
        {
            "iceServers": [
                {**credential, "urls": ["turn:198.51.100.7:31478?transport=udp"]},
                {**credential, "urls": ["turn:10.96.40.12:3478?transport=tcp"]},
            ],
            "iceTransportPolicy": "all",
        }
    )


ROUTES = [
    Route("/chat/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/stt/v1/audio/transcriptions", transcriptions, methods=["POST"]),
    Route("/speech/profiles", list_profiles, methods=["GET"]),
    Route("/speech/profiles", save_profile, methods=["POST"]),
    Route("/speech/profiles/{id}", read_profile, methods=["GET"]),
    Route("/speech/v1/audio/speech", speech, methods=["POST"]),
    Route("/turn/ice", ice, methods=["GET"]),
]


@pytest.fixture(scope="session")
def stand_in_server():
    """One server for the whole session, on its own thread and event loop."""
    server = uvicorn.Server(uvicorn.Config(Starlette(routes=ROUTES), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join()


@pytest.fixture
def stand_ins(stand_in_server, monkeypatch):
    """The stand-ins, emptied, with the bot's settings pointed at them and no relay (local mode)."""
    STAND_INS.reset()
    STAND_INS.url = stand_in_server
    for name, value in {
        "CHAT_URL": f"{stand_in_server}/chat/v1",
        "CHAT_MODEL": StandIns.chat_model,
        "STT_URL": f"{stand_in_server}/stt/v1",
        "STT_MODEL": StandIns.stt_model,
        "TTS_URL": f"{stand_in_server}/speech",
        "TTS_KEY": StandIns.key,
        "TURN_AUTH_URL": "",
        "TURN_TLS_HOST": "",
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(voices, "default_handle", None)
    character.reset()
    return STAND_INS


@pytest.fixture
async def bot(stand_ins):
    """An HTTP client for the bot's app, called in-process."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bot") as client:
        yield client
