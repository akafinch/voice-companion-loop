"""The bot's HTTP surface: relay settings (criterion 3), the active character, readiness."""

import pytest
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

from companion import character, relay, settings, voices
from companion.app import webrtc


async def test_relay_settings_on_lke(stand_ins, bot):
    """Criterion 3: the page gets only the relay, over UDP and TLS, with credentials, and the
    bot's own end of the call gets no ICE server."""
    first = (await bot.get("/api/relay")).json()
    second = (await bot.get("/api/relay")).json()

    asked = stand_ins.ice[0]
    assert asked["service"] == "turn"
    assert asked["username"] != stand_ins.ice[1]["username"]  # a fresh credential for every call
    assert first == [
        {
            "urls": ["turn:198.51.100.7:31478?transport=udp", "turns:relay.example.net:443?transport=tcp"],
            "username": f"1762300800:{asked['username']}",
            "credential": "aGVsbG8gcmVsYXk=",
        }
    ]
    assert second[0]["urls"] == first[0]["urls"]

    assert webrtc._ice_servers is None
    connection = SmallWebRTCConnection(ice_servers=webrtc._ice_servers)  # as the handler makes it
    assert connection.ice_servers == []  # so aiortc adds no STUN server of its own
    await connection.pc.close()


async def test_no_call_without_the_relay(stand_ins, monkeypatch):
    """The relay is the page's only way to the bot's pod, so without its settings the bot refuses
    to hand out ICE servers instead of falling back to a direct connection."""
    monkeypatch.setattr(settings, "TURN_AUTH_URL", "")
    with pytest.raises(RuntimeError, match="COMPANION_TURN_AUTH_URL"):
        await relay.ice_servers()
    assert stand_ins.ice == []


async def test_active_character(stand_ins, bot):
    stand_ins.profiles["00000042"] = {"id": "00000042", "name": "Captain Nova (default voice)"}
    await voices.ensure_default_voice()
    default = {"persona": character.BUILT_IN_PERSONA, "voice": "00000042"}

    assert (await bot.get("/api/character")).json() == default
    replaced = {"persona": "You are Bram.", "voice": "b7a3c9d1"}
    assert (await bot.put("/api/character", json=replaced)).json() == replaced
    assert character.get() == character.Character("You are Bram.", "b7a3c9d1")
    assert (await bot.delete("/api/character")).json() == default
    assert character.get() == character.default()


async def test_ready_once_the_default_voice_is_saved(stand_ins, bot, monkeypatch):
    monkeypatch.setattr(settings, "TTS_URL", "http://127.0.0.1:9")  # VoiceStudio not up yet
    assert (await bot.get("/healthz")).status_code == 503

    monkeypatch.setattr(settings, "TTS_URL", f"{stand_ins.url}/speech")
    assert (await bot.get("/healthz")).status_code == 200
    assert voices.default_handle in stand_ins.profiles
