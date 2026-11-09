"""Saving voices in VoiceStudio, and choosing the one to speak in."""

from companion import voices


async def test_default_voice_is_designed_once(stand_ins):
    handle = await voices.ensure_default_voice()

    design = voices.DEFAULT_VOICE
    assert stand_ins.speech == [  # spoken from the description alone, with no voice
        {"model": "voxcpm2", "input": design["line"], "description": design["description"], "response_format": "wav"}
    ]
    [saved] = stand_ins.saved
    filename, audio = saved["ref_audio"]
    assert filename.endswith(".wav") and audio.startswith(b"RIFF")
    assert saved == {"name": design["name"], "ref_text": design["line"], "kind": "clone", "ref_audio": (filename, audio)}
    assert stand_ins.profiles[handle]["name"] == design["name"]

    assert await voices.ensure_default_voice() == handle  # cached: no more requests
    assert len(stand_ins.saved) == 1


async def test_default_voice_is_found_by_name(stand_ins):
    stand_ins.profiles["0000abcd"] = {"id": "0000abcd", "name": voices.DEFAULT_VOICE["name"]}
    assert await voices.ensure_default_voice() == "0000abcd"
    assert stand_ins.saved == [] and stand_ins.speech == []


async def test_a_lost_voice_speaks_as_the_default(stand_ins):
    stand_ins.profiles["c1a2b3c4"] = {"id": "c1a2b3c4", "name": "a clone"}
    assert await voices.resolve("c1a2b3c4") == "c1a2b3c4"

    default = await voices.resolve("deadbeef")  # gone, as when VoiceStudio's pod restarts
    assert default == voices.default_handle
    assert stand_ins.profiles[default]["name"] == voices.DEFAULT_VOICE["name"]

    stand_ins.profiles.clear()  # VoiceStudio lost the default voice too: it is saved again
    assert await voices.resolve(default) != default
    assert len(stand_ins.saved) == 2
