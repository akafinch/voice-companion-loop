"""Speaking the reply through VoiceStudio, in the active character's voice.

pipecat's OpenAI speech service refuses voices outside OpenAI's own names, so the bot has this thin
adapter instead, modelled on it:
https://github.com/pipecat-ai/pipecat/blob/v1.12.0/src/pipecat/services/openai/tts.py
"""

import httpx
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

from companion import character, voices


class VoiceStudioTTS(TTSService):
    def __init__(self, **kwargs):
        # VoiceStudio's "pcm" format is 24 kHz 16-bit mono, resampled from VoxCPM2's 48 kHz.
        super().__init__(
            sample_rate=24000,
            push_start_frame=True,
            push_stop_frames=True,
            settings=TTSSettings(model=voices.MODEL, voice=None, language=None),
            **kwargs,
        )

    def can_generate_metrics(self) -> bool:
        return True  # so the page can show how long speech takes to start

    async def run_tts(self, text: str, context_id: str):
        try:
            # Read for every sentence, so a replaced character is heard from the next one.
            voice = await voices.resolve(character.get().voice)
            request = {"model": voices.MODEL, "input": text, "voice": voice, "response_format": "pcm"}
            async with (
                voices.client() as voicestudio,
                voicestudio.stream("POST", "/v1/audio/speech", json=request) as response,
            ):
                response.raise_for_status()
                async for chunk in response.aiter_bytes(self.chunk_size):
                    await self.stop_ttfb_metrics()
                    yield TTSAudioRawFrame(chunk, self.sample_rate, 1, context_id=context_id)
        except httpx.HTTPError as error:
            # Reported, and the reply carries on without this sentence's audio.
            yield ErrorFrame(error=f"Speech failed: {error}")
