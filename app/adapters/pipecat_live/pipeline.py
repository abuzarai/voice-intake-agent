"""Owned media graph, gates, runner and WebRTC connection primitives."""

import asyncio

from app.adapters.pipecat_live._activation import activate

activate()

from aiortc.sdp import candidate_from_sdp
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputTextRawFrame,
    InputTransportMessageFrame,
    LLMContextFrame,
    LLMRunFrame,
    OutputAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import (
    PipelineParams,
    PipelineWorker,
    ProcessorUnusablePolicy,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from app.adapters.pipecat_live.gemini import START_REQUEST, make_service
from app.services.live_audio_draft import REQUEST, AudioDraftController

INPUT_RATE = 16000
OUTPUT_RATE = 24000


class PlaybackGate(FrameProcessor):
    """Drops future output PCM, not audio already buffered beyond this gate."""

    def __init__(self):
        super().__init__()
        self.suppressed = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if self.suppressed and isinstance(frame, OutputAudioRawFrame):
            return
        await self.push_frame(frame, direction)


class InputContract(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.closed = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputTransportMessageFrame):
            return  # No client data-channel commands or injected context/state.
        if direction == FrameDirection.DOWNSTREAM and isinstance(
            frame, InputAudioRawFrame
        ):
            if self.closed:
                return
            if (
                frame.sample_rate != INPUT_RATE
                or frame.num_channels != 1
                or len(frame.audio) % 2
            ):
                await self.push_error(
                    "invalid_input_audio", force_treat_as_permanent=True
                )
                return
        await self.push_frame(frame, direction)


class LivePipelineWorker(PipelineWorker):
    async def _load_setup_files(self):
        """Pinned hook: never execute ambient PIPECAT_SETUP_FILES code."""
        return None


class LiveWorkerRunner(WorkerRunner):
    async def _load_setup_files(self):
        """Pinned hook: never execute ambient PIPECAT_SETUP_FILES code."""
        return None


class LivePipeline:
    def __init__(
        self,
        connection,
        config,
        *,
        commit,
        fail,
        disconnect,
        audio_draft=None,
        finished=None,
        reserve_finished=None,
        completion_forwarded=None,
        assistant_commit=None,
    ):
        self.connection = connection
        self.config = config
        self.commit = commit
        self.assistant_commit = assistant_commit
        self.fail = fail
        self.disconnect = disconnect
        self.finished = finished
        self.reserve_finished = reserve_finished
        self.completion_forwarded = completion_forwarded
        self.runner = None
        self.service = None
        self.audio_draft = audio_draft or AudioDraftController()
        self.input_gate = InputContract()
        self.playback_gate = PlaybackGate()
        self.transport_ready = False
        self.context_ready = False
        self._initial_context_queued = False

    @property
    def ready(self):
        return self.transport_ready and self.context_ready

    def gate_audio(self):
        self.input_gate.closed = True
        self.playback_gate.suppressed = True

    async def request_audio_draft(self):
        self.service.set_audio_input_paused(True)
        await self.service.process_frame(
            InputTextRawFrame(text=REQUEST), FrameDirection.DOWNSTREAM
        )

    async def run(self):
        try:
            transport = SmallWebRTCTransport(
                self.connection,
                TransportParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    audio_in_sample_rate=INPUT_RATE,
                    audio_out_sample_rate=OUTPUT_RATE,
                    audio_in_channels=1,
                    audio_out_channels=1,
                    video_in_enabled=False,
                    video_out_enabled=False,
                ),
            )
            user, assistant = LLMContextAggregatorPair(
                LLMContext([{"role": "user", "content": START_REQUEST}]),
                realtime_service_mode=True,
                user_params=LLMUserAggregatorParams(
                    user_turn_strategies=ExternalUserTurnStrategies()
                ),
            )
            self.service = make_service(
                self.config,
                self.fail,
                self.audio_draft,
                context_ready=lambda: setattr(self, "context_ready", True),
                finished=self.finished,
                reserve_finished=self.reserve_finished,
                completion_forwarded=self.completion_forwarded,
            )

            async def result_roundtrip():
                await self.service.process_frame(
                    LLMContextFrame(assistant.context), FrameDirection.UPSTREAM
                )

            self.audio_draft.result_roundtrip = result_roundtrip
            worker = LivePipelineWorker(
                Pipeline(
                    [
                        transport.input(),
                        self.input_gate,
                        user,
                        self.service,
                        self.playback_gate,
                        transport.output(),
                        assistant,
                    ]
                ),
                params=PipelineParams(
                    audio_in_sample_rate=INPUT_RATE,
                    audio_out_sample_rate=OUTPUT_RATE,
                    enable_metrics=False,
                    enable_usage_metrics=False,
                ),
                idle_timeout_secs=None,
                enable_turn_tracking=False,
                enable_rtvi=False,
                processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
            )
            # No development runner, CLI, inherited ICE, file setup or signal hooks.
            self.runner = LiveWorkerRunner(handle_sigint=False, handle_sigterm=False)

            @self.runner.event_handler("on_error")
            async def runner_failed(*args):
                self.fail()

            await self.runner.add_workers(worker)

            @transport.event_handler("on_client_connected")
            async def connected(*args):
                if self.input_gate.closed or self._initial_context_queued:
                    return
                self._initial_context_queued = True
                try:
                    await worker.queue_frames([LLMRunFrame()])
                    if not self.input_gate.closed:
                        self.transport_ready = True
                except Exception:
                    self.fail()

            @transport.event_handler("on_client_disconnected")
            async def disconnected(*args):
                self.disconnect()

            @worker.event_handler("on_pipeline_error")
            async def failed(*args):
                self.fail()

            @user.event_handler("on_user_turn_message_added")
            async def committed(aggregator, message):
                # Do not use interim/provider transcript frames or model turns.
                try:
                    if not self.input_gate.closed:
                        self.commit(message.content)
                except Exception:
                    self.fail()

            @assistant.event_handler("on_assistant_turn_stopped")
            async def assistant_committed(aggregator, message):
                try:
                    if not self.input_gate.closed and self.assistant_commit is not None:
                        self.assistant_commit(message.content, message.interrupted)
                except Exception:
                    pass  # Transcript-only failures must not stop voice.

            # WorkerRunner only protects cancellation AFTER its setup awaits.
            # Let setup finish under ownership, then cancel cooperatively so its
            # workers/bus/processors all reach the upstream cleanup barrier.
            runner_task = asyncio.create_task(self.runner.run())
            try:
                await asyncio.shield(runner_task)
            finally:
                await self.runner.cancel()
                await asyncio.gather(runner_task, return_exceptions=True)
        finally:
            self.transport_ready = self.context_ready = False
            if self.service is not None:
                await self.service.close_sdk()

    async def close(self):
        if self.runner is not None:
            await self.runner.cancel()
        if self.service is not None:
            await self.service.close_sdk()


class LiveAdapter:
    # The public request handler is pinned/compatibility tested, but its initial
    # offer cannot own peers before initialize or surface swallowed callbacks.
    # Use public connection primitives for that narrow orchestration instead.
    pipeline = LivePipeline

    @staticmethod
    def connection():
        return SmallWebRTCConnection(ice_servers=[])

    @staticmethod
    def candidate(value):
        if not value.candidate:
            return None  # RFC 8840 end-of-candidates, including Firefox.
        candidate = candidate_from_sdp(value.candidate)
        candidate.sdpMid = value.sdp_mid
        candidate.sdpMLineIndex = value.sdp_mline_index
        return candidate
