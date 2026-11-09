"""The character page's API on the stand-in services, criteria 1 to 3, and the page's build,
criterion 4.

The audition asks the chat model for one whole greeting, so here the chat model is a stand-in of
its own that answers whole, as vLLM does unless asked to stream. The shared one only streams, as
the calls ask it to. VoiceStudio is the shared stand-in.
"""

import dataclasses
import io
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from companion import character, settings, voices
from companion.studio import router as studio
from companion.studio.persona import compose
from companion.studio.presets import PRESETS

NOVA, BRAM = PRESETS
LINE = "Hello! I'm recording a short sample, so the companion can borrow my voice."  # the transcript
GREETING = "Ahoy, and welcome aboard! I'm Captain Nova."  # the chat model's reply
WEB = Path(__file__).parent.parent / "web"


class WholeChat:
    """What the chat model was asked, and the reply it gives."""

    def __init__(self):
        self.url = ""
        self.asked = []  # JSON bodies sent to it
        self.reply = GREETING

    async def completions(self, request: Request):
        self.asked.append(await request.json())
        choice = {"index": 0, "message": {"role": "assistant", "content": self.reply}, "finish_reason": "stop"}
        return JSONResponse(
            {"id": "greeting", "object": "chat.completion", "created": 0, "model": "chat-model", "choices": [choice]}
        )


@pytest.fixture(scope="module")
def whole_chat_server():
    """Served over real HTTP on its own thread, like the shared stand-ins."""
    chat = WholeChat()
    routes = [Route("/v1/chat/completions", chat.completions, methods=["POST"])]
    server = uvicorn.Server(uvicorn.Config(Starlette(routes=routes), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    chat.url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}/v1"
    yield chat
    server.should_exit = True
    thread.join()


@pytest.fixture
def chat(whole_chat_server, stand_ins, monkeypatch):
    """The whole-answer chat model, emptied, with the bot's settings pointed at it."""
    whole_chat_server.asked.clear()
    whole_chat_server.reply = GREETING
    monkeypatch.setattr(settings, "CHAT_URL", whole_chat_server.url)
    return whole_chat_server


@pytest.fixture(autouse=True)
def no_draft(monkeypatch):
    """Each test starts without a draft, as the bot does."""
    monkeypatch.setattr(studio, "_draft", None)


def clip(seconds: float) -> bytes:
    """A WAV of silence, as the page sends a sample: 16 kHz, mono, 16-bit PCM."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(int(seconds * 16000) * 2))
    return buffer.getvalue()


async def clone(bot, sample: bytes, fields: dict):
    """Send a sample to clone, as the page does."""
    return await bot.post("/api/studio/voice", data=fields, files={"sample": ("sample.wav", sample, "audio/wav")})


async def test_personas_are_composed_as_probed(stand_ins, bot):
    """The presets compose, word for word, the two characters the chat model was tested with, and a
    new draft is the built-in character, in the default voice. An edit recomposes the persona."""
    await voices.ensure_default_voice()
    assert (await bot.get("/api/studio/presets")).json() == [dataclasses.asdict(p) for p in PRESETS]
    assert compose(NOVA) == character.BUILT_IN_PERSONA
    assert character.system_prompt(character.Character(compose(BRAM), None)) == (
        "You are Bram. You keep a small inn in a mountain village, and you have never seen the sea. "
        "You are gruff, dry and secretly kind. You speak in few words, plainly, with the odd dry joke. "
        "Speak in short, plain sentences. Keep each reply to two or three sentences. Never use markup, "
        "emoji, lists or stage directions. Never step out of character."
    )

    draft = (await bot.get("/api/studio/draft")).json()
    assert draft == {"traits": dataclasses.asdict(NOVA), "persona": compose(NOVA), "voice": voices.default_handle}

    edited = {**dataclasses.asdict(BRAM), "temperament": "warm and patient"}
    draft = (await bot.put("/api/studio/draft", json=edited)).json()
    assert draft["traits"] == edited
    assert draft["persona"] == compose(dataclasses.replace(BRAM, temperament="warm and patient"))
    assert draft["voice"] == voices.default_handle  # an edit keeps the voice


async def test_audition(stand_ins, bot, chat):
    """Criterion 1: the chat model is asked for a greeting with the draft's persona, VoiceStudio
    speaks it in the draft's voice, and the page gets a WAV clip to play."""
    await bot.put("/api/studio/draft", json=dataclasses.asdict(BRAM))
    sample = clip(10)
    cloned = await clone(bot, sample, {"transcript": LINE, "consent": "true"})
    assert cloned.status_code == 200
    draft = cloned.json()
    assert stand_ins.saved == [
        {"name": "Bram's voice", "ref_text": LINE, "kind": "clone", "ref_audio": ("sample.wav", sample)}
    ]
    assert draft["voice"] in stand_ins.profiles

    response = await bot.post("/api/studio/audition")

    persona_and_rules = character.system_prompt(character.Character(compose(BRAM), draft["voice"]))
    assert chat.asked == [
        {
            "model": stand_ins.chat_model,
            "messages": [
                {"role": "system", "content": persona_and_rules},
                {"role": "user", "content": "Greet the person you're about to meet, in one or two short sentences."},
            ],
            "max_completion_tokens": 120,
        }
    ]
    assert stand_ins.speech == [
        {"model": "voxcpm2", "voice": draft["voice"], "input": GREETING, "response_format": "wav"}
    ]
    assert stand_ins.keys == {f"Bearer {stand_ins.key}"}
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(response.content)) as audio:
        assert audio.getnframes() > 0


async def test_a_failed_audition_is_a_502(stand_ins, bot, chat, monkeypatch):
    chat.reply = None  # a null reply: there is nothing to speak
    assert (await bot.post("/api/studio/audition")).status_code == 502
    assert stand_ins.speech == []

    chat.reply = GREETING
    monkeypatch.setattr(settings, "TTS_URL", "http://127.0.0.1:9")  # VoiceStudio unreachable
    assert (await bot.post("/api/studio/audition")).status_code == 502


async def test_no_clone_without_consent(stand_ins, bot):
    """Criterion 2: without the speaker's consent a clone is refused, before any other check, and
    nothing changes."""
    draft = (await bot.get("/api/studio/draft")).json()
    active = character.get()

    assert (await clone(bot, clip(10), {"transcript": LINE})).status_code == 403
    assert (await clone(bot, clip(10), {"transcript": LINE, "consent": "false"})).status_code == 403
    assert (await clone(bot, clip(31), {})).status_code == 403  # consent is checked first

    assert stand_ins.saved == [] and stand_ins.speech == []  # VoiceStudio was never asked
    assert (await bot.get("/api/studio/draft")).json() == draft
    assert character.get() == active


@pytest.mark.parametrize(
    ("sample", "transcript", "status"),
    [
        pytest.param(clip(31), LINE, 422, id="31s"),
        pytest.param(clip(2), LINE, 422, id="2s"),
        pytest.param(b"ID3\x04 an MP3, not a WAV", LINE, 422, id="not-wav"),
        pytest.param(clip(10), " ", 422, id="no-transcript"),
        pytest.param(bytes(5 * 1024 * 1024 + 1), LINE, 413, id="over-5MB"),
    ],
)
async def test_unusable_samples_are_refused(stand_ins, bot, sample, transcript, status):
    """Criterion 2, continued: a 31 s WAV is refused, as is any sample VoiceStudio could not clone
    well from."""
    response = await clone(bot, sample, {"transcript": transcript, "consent": "true"})
    assert response.status_code == status
    assert stand_ins.saved == []


async def test_apply_then_reset(stand_ins, bot):
    """Criterion 3: after Apply, the seam the loop reads before each reply yields the draft's
    persona and voice; after Reset, the built-in character's."""
    await voices.ensure_default_voice()
    await bot.put("/api/studio/draft", json=dataclasses.asdict(BRAM))
    draft = (await clone(bot, clip(10), {"transcript": LINE, "consent": "true"})).json()

    applied = await bot.post("/api/studio/apply")
    assert applied.json() == {"persona": compose(BRAM), "voice": draft["voice"]}
    assert character.get() == character.Character(compose(BRAM), draft["voice"])

    reset = await bot.post("/api/studio/reset")
    assert reset.json() == {"persona": character.BUILT_IN_PERSONA, "voice": voices.default_handle}
    assert character.get() == character.default()
    assert (await bot.get("/api/studio/draft")).json() == draft  # kept, to apply again


@pytest.mark.skipif(
    shutil.which("npm") is None or not (WEB / "node_modules").is_dir(),
    reason="building the pages needs npm, and web/node_modules: run `npm ci` in web/",
)
def test_character_page_builds():
    """Criterion 4: the build writes the character page, with its script, beside the talk page."""
    page = WEB / "dist" / "character.html"
    page.unlink(missing_ok=True)
    build = subprocess.run(["npm", "run", "build"], cwd=WEB, capture_output=True, text=True)
    assert build.returncode == 0, build.stdout + build.stderr
    assert '<script type="module" crossorigin src="/assets/character-' in page.read_text()
    assert (WEB / "dist" / "index.html").is_file()
