"""The active character: persona text plus the handle of a voice saved in VoiceStudio.

There is one per bot process, shared by every call. Each call reads it before every reply, so a
replaced character speaks from the next turn of a call already in progress, with the earlier
turns kept.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from companion import voices

# The bot's own speaking rules, which follow every persona. Short replies, and short first
# sentences, keep the reply quick to hear: the speech service renders each sentence whole before
# sending any of its audio.
RULES = (
    "Speak in short, plain sentences. Keep each reply to two or three sentences. Never use markup, "
    "emoji, lists or stage directions. Never step out of character."
)

BUILT_IN_PERSONA = json.loads(Path(__file__).with_name("default_character.json").read_text())["persona"]


@dataclass(frozen=True)
class Character:
    persona: str
    voice: str | None  # a VoiceStudio profile id; None speaks in the default voice


_active: Character | None = None  # None means the built-in character


def default() -> Character:
    """The built-in character, in the default voice once VoiceStudio has saved it."""
    return Character(BUILT_IN_PERSONA, voices.default_handle)


def get() -> Character:
    return _active or default()


def replace(persona: str, voice: str | None) -> None:
    global _active
    _active = Character(persona, voice)


def reset() -> None:
    global _active
    _active = None


def system_prompt(c: Character) -> str:
    """What leads every request to the chat model: the persona, then the speaking rules."""
    return f"{c.persona} {RULES}"
