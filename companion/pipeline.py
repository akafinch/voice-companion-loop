"""One call: the pipeline every spoken turn crosses, from the reader's microphone back to their ears.

A pipeline runs per call. pipecat's example of the same shape:
https://github.com/pipecat-ai/pipecat/blob/v1.12.0/examples/voice/voice-openai-http.py
"""

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from companion import character, settings
from companion.speech import VoiceStudioTTS


class CompanionLLM(OpenAILLMService):
    """The chat model, told before every reply who it is.

    The model remembers nothing between requests, so each one leads with the active character's
    persona and the speaking rules, ahead of the whole conversation so far.
    """

    async def get_chat_completions(self, context):
        prompt = character.system_prompt(character.get())
        await self._update_settings(self.Settings(system_instruction=prompt))
        return await super().get_chat_completions(context)


def build_pipeline(transport_in: FrameProcessor, transport_out: FrameProcessor) -> Pipeline:
    """Hear, answer, speak: the stages between the call's incoming audio and its outgoing audio."""
    # Transcription runs once per utterance, when voice-activity detection says it has ended.
    # vLLM runs here without an API key, but the OpenAI client insists on one.
    stt = OpenAISTTService(
        base_url=settings.STT_URL,
        api_key="unused",
        settings=OpenAISTTService.Settings(model=settings.STT_MODEL),
    )
    # No temperature or thinking flag: the deployment starts vLLM with thinking off and sampling
    # at 0.7 by default, and pipecat leaves unset fields out of the request. 120 tokens caps a
    # runaway reply.
    llm = CompanionLLM(
        base_url=settings.CHAT_URL,
        api_key="unused",
        settings=CompanionLLM.Settings(model=settings.CHAT_MODEL, max_completion_tokens=120),
    )
    tts = VoiceStudioTTS()

    # The conversation so far, resent in full with every reply.
    # ASSUMPTION (unverified): a whole call fits the chat model's 16,384-token context untrimmed.
    # vLLM rejects, rather than truncates, a prompt over 16,384 - 120 tokens; the usage metrics
    # show each prompt's size, so a rehearsal can watch it grow.
    context = LLMContext()
    user, assistant = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # The reader's turn ends once voice-activity detection has heard 0.2 s of silence and
            # 0.3 s more have passed: the speech-timeout stop, shortened from its 0.6 s default.
            # pipecat's default stop, the Smart Turn model, can wait up to 3 s on a sentence it
            # judges unfinished.
            # ASSUMPTION (unverified): 0.3 s cuts no one off mid-pause. The rehearsal watches for
            # cut-offs, and Smart Turn is the fallback.
            user_turn_strategies=UserTurnStrategies(
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.3)]
            ),
        ),
    )

    return Pipeline([transport_in, stt, user, llm, tts, transport_out, assistant])


async def run_bot(connection: SmallWebRTCConnection):
    """Run one call over an answered WebRTC connection, until its page disconnects."""
    transport = SmallWebRTCTransport(
        connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    worker = PipelineWorker(
        build_pipeline(transport.input(), transport.output()),
        # Metrics time each stage for the page; usage metrics add each prompt's token count.
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        # A call ends only when its page disconnects: the default would hang up after 300 s.
        idle_timeout_secs=None,
    )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)  # uvicorn owns the process's signals
    await runner.add_workers(worker)
    await runner.run()
