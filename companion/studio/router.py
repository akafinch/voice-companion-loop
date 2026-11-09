"""The character page's API, under /api/studio.

The page edits one draft character, which no call hears until it is applied through the
active-character seam. Nothing is stored: like the active character, the draft lives in the bot's
memory, and a cloned voice lives in VoiceStudio until its pod restarts.
"""

import dataclasses
import io
import wave

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile

from companion import character, settings, voices
from companion.studio.persona import Traits, compose
from companion.studio.presets import PRESETS

router = APIRouter(prefix="/api/studio")

# The audition's prompt, word for word as the chat model was tested with it before it was chosen.
GREETING = "Greet the person you're about to meet, in one or two short sentences."

MAX_SAMPLE_BYTES = 5 * 1024 * 1024


@dataclasses.dataclass(frozen=True)
class Draft:
    traits: Traits
    persona: str  # composed from the traits
    voice: str | None  # a VoiceStudio profile id; None speaks in the default voice


_draft: Draft | None = None


def draft() -> Draft:
    """The draft, made on first use: the first preset, in the default voice. Not at import, since
    the default voice's handle is known only once VoiceStudio has saved it, after the bot starts."""
    global _draft
    if _draft is None:
        _draft = Draft(PRESETS[0], compose(PRESETS[0]), character.default().voice)
    return _draft


@router.get("/presets")
def list_presets() -> list[Traits]:
    return PRESETS


@router.get("/draft")
def read_draft() -> Draft:
    return draft()


@router.put("/draft")
def edit_draft(traits: Traits) -> Draft:
    """New traits for the draft, composed into its persona. Its voice stays."""
    global _draft
    _draft = dataclasses.replace(draft(), traits=traits, persona=compose(traits))
    return _draft


@router.post("/voice")
async def clone_voice(
    consent: bool = Form(False),
    sample: UploadFile | None = File(None),
    transcript: str = Form(""),
) -> Draft:
    """Clone a voice from a few seconds of speech, recorded or uploaded, and make it the draft's.

    Every field has a default, so that a request without consent reaches the check below rather
    than failing FastAPI's validation of a missing field first.
    """
    global _draft
    # A voice belongs to its speaker: without their permission, nothing else is looked at.
    if not consent:
        raise HTTPException(403, "Cloning a voice needs its speaker's permission.")
    if sample is None:
        raise HTTPException(422, "Send a sample of the voice.")
    # Starlette caps a form's text fields, but spools an uploaded file of any size to disk.
    if sample.size > MAX_SAMPLE_BYTES:
        raise HTTPException(413, "The sample is over 5 MB.")
    audio = await sample.read()
    try:
        with wave.open(io.BytesIO(audio)) as clip:
            rate = clip.getframerate()
            seconds = clip.getnframes() / rate if rate else 0
    # The wave module reads only PCM. On a chunk that overruns the file, Python 3.13's raises a
    # bare RuntimeError (Lib/wave.py, _Chunk.seek).
    except (wave.Error, EOFError, RuntimeError):
        raise HTTPException(422, "The sample must be a WAV file of PCM audio.")
    # VoiceStudio would cut a longer clip to 30 s, and then drop its transcript.
    if not 3 <= seconds <= 30:
        raise HTTPException(422, f"The sample lasts {seconds:.1f} s, and must last 3 to 30 s.")
    # VoxCPM2 continues the clip from its transcript, so the transcript must match it.
    if not transcript.strip():
        raise HTTPException(422, "Send the sample's transcript.")

    name = f"{draft().traits.name}'s voice"
    try:
        handle = await voices.save_from_sample(name, audio, transcript.strip())
    except httpx.HTTPError as error:
        raise HTTPException(502, f"VoiceStudio could not save the voice: {error}")
    # Read again after the wait, in case the traits were edited meanwhile.
    _draft = dataclasses.replace(draft(), voice=handle)
    return _draft


@router.post("/audition")
async def audition() -> Response:
    """The draft introduces itself: the chat model the calls use writes a greeting from its
    persona, and VoiceStudio speaks it in its voice, as a clip the page plays without a call."""
    d = draft()
    try:
        greeting = await greet(character.Character(d.persona, d.voice))
        audio = await speak(greeting, d.voice)
    except httpx.HTTPError as error:
        raise HTTPException(502, f"The audition failed: {error}")
    return Response(audio, media_type="audio/wav")


async def greet(c: character.Character) -> str:
    """A greeting in character, asked for the bot's way: the persona and speaking rules first."""
    request = {
        "model": settings.CHAT_MODEL,
        "messages": [
            {"role": "system", "content": character.system_prompt(c)},
            {"role": "user", "content": GREETING},
        ],
        "max_completion_tokens": 120,
    }
    # Not streamed: the greeting is spoken whole. The chat model also serves the calls, so the
    # request may wait its turn: allow 30 s, not httpx's default 5 s.
    async with httpx.AsyncClient(timeout=30) as chat:
        response = await chat.post(f"{settings.CHAT_URL}/chat/completions", json=request)
    response.raise_for_status()
    greeting = (response.json()["choices"][0]["message"]["content"] or "").strip()  # may be null
    if not greeting:
        raise HTTPException(502, "The chat model wrote no greeting.")
    return greeting


async def speak(text: str, handle: str | None) -> bytes:
    """`text` spoken in the voice `handle` names, as WAV: 16-bit PCM at VoxCPM2's 48 kHz, which
    VoiceStudio writes without ffmpeg and a browser plays as it is."""
    request = {
        "model": voices.MODEL,
        "voice": await voices.resolve(handle),
        "input": text,
        "response_format": "wav",
    }
    async with voices.client() as voicestudio:  # 60 s: a new clone's first take recompiles the engine
        response = await voicestudio.post("/v1/audio/speech", json=request)
    response.raise_for_status()
    return response.content


@router.post("/apply")
def apply() -> character.Character:
    """Make the draft the active character: the companion's next reply comes as it, in its voice,
    on calls already in progress too."""
    d = draft()
    character.replace(d.persona, d.voice)
    return character.get()


@router.post("/reset")
def reset() -> character.Character:
    """Make the built-in character active again. The draft stays as it is."""
    character.reset()
    return character.get()
