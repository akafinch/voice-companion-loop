"""Voices saved in VoiceStudio: the default voice, cloned ones, and which one to speak in."""

import asyncio
import json
from pathlib import Path

import httpx

from companion import settings

# Always name VoxCPM2: VoiceStudio serves its default model id, "omnivoice", from a separate engine
# instance, which would load a second copy and miss the warm-up.
MODEL = "voxcpm2"

# The built-in character's voice: designed from a description the first time VoiceStudio lacks it.
DEFAULT_VOICE = json.loads(Path(__file__).with_name("default_character.json").read_text())["voice"]

default_handle: str | None = None  # the default voice's profile id, once VoiceStudio has it
_saving_default = asyncio.Lock()


def client() -> httpx.AsyncClient:
    """A client for VoiceStudio. Its timeouts are 60 s, not httpx's 5 s: a cloned voice's first
    call recompiles the engine, which can take well over ten seconds."""
    return httpx.AsyncClient(
        base_url=settings.TTS_URL,
        headers={"Authorization": f"Bearer {settings.TTS_KEY}"},
        timeout=60,
    )


async def save_from_sample(name: str, audio: bytes, transcript: str) -> str:
    """Clone a voice from a WAV sample and save it, returning its profile id.

    The transcript always goes along: VoiceStudio transcribes a blank one only when a speech
    recogniser is installed. The file name matters too, since the stored clip takes its extension.
    """
    async with client() as voicestudio:
        response = await voicestudio.post(
            "/profiles",
            data={"name": name, "ref_text": transcript, "kind": "clone"},
            files={"ref_audio": ("sample.wav", audio, "audio/wav")},
        )
    response.raise_for_status()
    return response.json()["id"]


async def save_from_design(name: str, description: str, line: str) -> str:
    """Design a voice from a description, then save it cloned from its own reading of `line`."""
    async with client() as voicestudio:
        response = await voicestudio.post(
            "/v1/audio/speech",
            json={"model": MODEL, "input": line, "description": description, "response_format": "wav"},
        )
    response.raise_for_status()
    return await save_from_sample(name, response.content, line)


async def ensure_default_voice() -> str:
    """The default voice's profile id: found by name, or designed and saved, then cached."""
    global default_handle
    async with _saving_default:  # one design, even when a readiness probe and a call ask at once
        if default_handle is None:
            async with client() as voicestudio:
                response = await voicestudio.get("/profiles")
            response.raise_for_status()
            found = [p["id"] for p in response.json() if p["name"] == DEFAULT_VOICE["name"]]
            default_handle = found[0] if found else await save_from_design(**DEFAULT_VOICE)
    return default_handle


async def resolve(handle: str | None) -> str:
    """The voice to speak in: `handle` while VoiceStudio still has it, else the default voice.

    VoiceStudio forwards an id it does not know to the engine, which then speaks in an unconditioned
    voice, so a lost clone would change who is talking. Clones live only as long as VoiceStudio's pod.
    """
    global default_handle
    if handle:
        async with client() as voicestudio:
            response = await voicestudio.get(f"/profiles/{handle}")
        if response.status_code == 200:
            return handle
    if handle == default_handle:
        default_handle = None  # the default voice itself is gone, so save it again
    return await ensure_default_voice()
