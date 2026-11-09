"""A call end to end on the stand-in services, driven by pipecat's run_test: criteria 1 and 2.

The WebRTC transport is the one part left out. In its place the test sends what it would deliver
for each spoken turn: voice activity starting, the audio, voice activity stopping.
"""

import asyncio

from pipecat.frames.frames import (
    InputAudioRawFrame,
    MetricsFrame,
    TTSAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.pipeline.worker import PipelineParams
from pipecat.processors.filters.identity_filter import IdentityFilter
from pipecat.tests.utils import SleepFrame, run_test

from companion import character, voices
from companion.pipeline import build_pipeline

BRAM = (
    "You are Bram. You keep a small inn in a mountain village, and you have never seen the sea. "
    "You are gruff, dry and secretly kind. You speak in few words, plainly, with the odd dry joke."
)


def spoken_turn() -> list:
    """One turn of the reader's speech, as the transport and voice-activity detection present it."""
    return [
        VADUserStartedSpeakingFrame(start_secs=0.2),
        InputAudioRawFrame(audio=bytes(16000 * 2), sample_rate=16000, num_channels=1),  # 1 s
        VADUserStoppedSpeakingFrame(stop_secs=0.2),
    ]


async def call():
    """Run a call of two turns through the bot's pipeline, returning the frames it put out."""
    pause = SleepFrame(sleep=1.5)  # time enough to hear, answer and speak
    down, _ = await run_test(
        build_pipeline(IdentityFilter(), IdentityFilter()),
        frames_to_send=[*spoken_turn(), pause, *spoken_turn(), pause],
        pipeline_params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
    )
    return down


async def test_default_character_and_voice(stand_ins):
    """Criterion 1: no character set, two turns, every request in the default persona and voice."""
    down = await call()

    default = character.default()
    assert default.voice in stand_ins.profiles  # the default voice, saved in VoiceStudio
    assert [body["model"] for body in stand_ins.transcriptions] == [stand_ins.stt_model] * 2

    assert len(stand_ins.chat) == 2
    for body in stand_ins.chat:
        assert body["model"] == stand_ins.chat_model
        assert body["messages"][0] == {"role": "system", "content": character.system_prompt(default)}
        assert body["messages"][0]["content"].startswith(character.BUILT_IN_PERSONA)
        assert body["max_completion_tokens"] == 120
        assert "temperature" not in body and "chat_template_kwargs" not in body  # left to the server

    assert len(stand_ins.spoken()) == 2  # one sentence a reply
    assert all(body["model"] == "voxcpm2" for body in stand_ins.speech)  # the design request too
    assert all(body["voice"] == default.voice for body in stand_ins.spoken())
    assert stand_ins.keys == {f"Bearer {stand_ins.key}"}

    audio = [frame for frame in down if isinstance(frame, TTSAudioRawFrame)]
    assert audio and all(frame.sample_rate == 24000 and frame.num_channels == 1 for frame in audio)

    # Every stage reports its time to first output, which the page shows.
    timed = {data.processor.split("#")[0] for frame in down if isinstance(frame, MetricsFrame)
             for data in frame.data if isinstance(data, TTFBMetricsData)}
    assert {"OpenAISTTService", "CompanionLLM", "VoiceStudioTTS"} <= timed


async def test_character_replaced_mid_call(stand_ins, bot):
    """Criterion 2: a character replaced between turns answers the next turn, on the same call."""
    stand_ins.profiles["b7a3c9d1"] = {"id": "b7a3c9d1", "name": "Bram's voice"}  # a saved clone

    async def replace_after_first_reply():
        while not stand_ins.spoken():
            await asyncio.sleep(0.02)
        response = await bot.put("/api/character", json={"persona": BRAM, "voice": "b7a3c9d1"})
        assert response.status_code == 200

    replacing = asyncio.create_task(replace_after_first_reply())
    await call()  # one pipeline, start to end: the same call
    await replacing

    first, second = stand_ins.chat
    assert first["messages"][0]["content"] == character.system_prompt(character.default())
    assert second["messages"] == [
        {"role": "system", "content": character.system_prompt(character.Character(BRAM, "b7a3c9d1"))},
        {"role": "user", "content": stand_ins.heard[0]},  # turn one, kept
        {"role": "assistant", "content": "".join(stand_ins.reply)},
        {"role": "user", "content": stand_ins.heard[1]},
    ]
    before, after = stand_ins.spoken()
    assert before["voice"] == voices.default_handle
    assert after["voice"] == "b7a3c9d1"
