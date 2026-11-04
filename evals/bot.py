"""Eval-only websocket bot: EvalTransport, RTVI, and the production graph.

This process is never deployed. It reuses ``make_service``, ``InputContract``,
``PlaybackGate`` and ``AudioDraftController`` so the graph under test is the
production graph. Only the transport differs: an eval harness connects as an
RTVI client instead of a browser over WebRTC.
"""

import argparse
import asyncio
import time
from pathlib import Path

from app.adapters.pipecat_live._activation import activate

activate()

from loguru import logger
from pipecat.evals.serializer import EvalSerializer
from pipecat.evals.transport import EvalTransport, EvalTransportParams
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputTextRawFrame,
    LLMContextFrame,
    LLMRunFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import (
    PipelineParams,
    ProcessorUnusablePolicy,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

from app.adapters.pipecat_live.gemini import START_REQUEST, make_service
from app.adapters.pipecat_live.pipeline import (
    INPUT_RATE,
    OUTPUT_RATE,
    InputContract,
    LivePipelineWorker,
    LiveWorkerRunner,
    PlaybackGate,
)
from app.services.live_audio_draft import REQUEST, AudioDraftController
from app.services.live_runtime import LiveProviderConfig
from evals.env import credentials

PREPARE_TIMEOUT = 30.0

# How long the caller has to stay quiet before the draft is requested. A
# second turn resets it, so a merged or a split utterance both work.
PREPARE_SETTLE_S = 2.5

# If the model never reports hearing the caller, draft from the seeded context
# rather than produce nothing. The transcript already holds the conversation.
COMMIT_FALLBACK_S = 15.0

# Production forces its first turn with START_REQUEST. A replay needs the same
# force, or the model reads the seeded transcript as a finished interview and
# closes the call instead of asking a question.


def enable_debug_logging():
    """Undo the adapter's log suppression, for diagnosing an eval run."""
    import logging

    from loguru import logger

    logger.enable("pipecat")
    for name in ("aiortc", "aioice", "websockets", "google_genai", "httpcore", "httpx"):
        logging.getLogger(name).setLevel(logging.DEBUG)


def provider_config(env_file=None):
    """Build the provider config from the environment, or one named env file."""
    found = credentials(env_file)
    if found is None:
        raise SystemExit("set LIVE_EVAL_GEMINI_KEY and LIVE_EVAL_MODEL")
    return LiveProviderConfig(api_key=found[0], model=found[1])


class FrameSpy(FrameProcessor):
    """Count the frames the transport delivers, so a silent bot is diagnosable."""

    def __init__(self, enabled=False, limit=12):
        super().__init__()
        self.enabled = enabled
        self.seen = {}
        self.limit = limit

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        name = type(frame).__name__
        self.seen[name] = self.seen.get(name, 0) + 1
        if self.enabled and self.seen[name] <= 2 and len(self.seen) <= self.limit:
            logger.info(f"eval frame in: {name} {direction}")
        await self.push_frame(frame, direction)


class HoldAudio(FrameProcessor):
    """Hold caller audio until the provider session can take it.

    Timing the caller from outside drops audio when the guess is wrong. The
    bot is the transport, so it buffers instead and delivers in order.
    """

    def __init__(self, live, on_release=None, max_wait=8.0):
        super().__init__()
        self._live = live
        self._on_release = on_release
        self._max_wait = max_wait
        self._held = []
        self._released = False
        self._task = None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if (
            isinstance(frame, InputAudioRawFrame)
            and direction == FrameDirection.DOWNSTREAM
            and not self._released
        ):
            self._held.append(frame)
            if self._task is None:
                self._task = self.create_task(self._release_when_live())
            return
        await self.push_frame(frame, direction)

    async def _release_when_live(self):
        deadline = time.monotonic() + self._max_wait
        while not self._released:
            if self._live():
                break
            if time.monotonic() >= deadline:
                logger.warning("hold audio: releasing on the timeout")
                break
            await asyncio.sleep(0.1)
        logger.info(f"hold audio: releasing {len(self._held)} frame(s)")
        await self._release()

    async def _release(self):
        self._released = True
        if self._on_release is not None:
            self._on_release()
        held, self._held = self._held, []
        for frame in held:
            await self.push_frame(frame, FrameDirection.DOWNSTREAM)


class EvalBot:
    """One eval session: serve a connection, then draft once the call closes."""

    def __init__(self, config, host, port, proposal_out=None, mode="extraction"):
        self.config = config
        self.host = host
        self.port = port
        self.proposal_out = proposal_out
        self.mode = mode
        self.controller = AudioDraftController()
        self.input_gate = InputContract()
        self.playback_gate = PlaybackGate()
        self.spy = FrameSpy()
        self.hold = HoldAudio(
            lambda: getattr(self.service, "_session", None) is not None
        )
        self.spoke = False
        self.commits = 0
        self._prepare_timer = None
        self._fallback_timer = None
        self.failures = []
        self.prepared = False
        self.service = None
        self.assistant = None
        self.runner = None

    def fail(self, *args):
        self.failures.append(args[0] if args else "pipeline_failed")

    def reserve_finished(self):
        """The model never decides when to draft here; the host does.

        In production the application calls Prepare over HTTP. Refusing the
        tool keeps that decision out of the model's hands. A behaviour case is
        the exception: it measures closure, so the call must be accepted.
        """
        return self.mode == "behaviour"

    def finished(self):
        """Ignored: closure is an assertion, not a trigger."""
        return

    def audio_started(self):
        """Arm a fallback for audio the model never reports hearing."""
        if self.mode != "extraction" or self.commits or self.prepared:
            return
        self._fallback_timer = asyncio.get_running_loop().call_later(
            COMMIT_FALLBACK_S, self._fallback
        )

    def _fallback(self):
        if self.commits or self.prepared:
            return
        logger.warning("no caller turn committed; drafting from the seeded context")
        self._start_prepare()

    def committed(self, *_args):
        """A caller turn was heard. Draft once the caller has stopped."""
        if self.mode != "extraction":
            return
        if self._fallback_timer is not None:
            self._fallback_timer.cancel()
        self.commits += 1
        logger.info(f"caller turn {self.commits} committed; drafting after the settle")
        loop = asyncio.get_running_loop()
        if self._prepare_timer is not None:
            self._prepare_timer.cancel()
        self._prepare_timer = loop.call_later(PREPARE_SETTLE_S, self._start_prepare)

    def _start_prepare(self):
        if self.prepared:
            return
        self.prepared = True
        asyncio.get_running_loop().create_task(self.prepare())

    async def prepare(self):
        try:
            self.controller.reserve(time.monotonic() + PREPARE_TIMEOUT)
            self.input_gate.closed = True
            self.playback_gate.suppressed = True
            self.service.set_audio_input_paused(True)
            await self.controller.request(self.send_request, lambda: None)
            self.write_proposal()
        except Exception as exc:
            logger.warning(f"prepare failed: {exc!r}")
            self.failures.append(f"prepare_failed:{exc!r}")

    def write_proposal(self):
        """Save the settled draft, so the runner scores the real payload."""
        if not self.proposal_out or self.controller.phase != "proposed":
            return
        proposal = self.controller.view("eval").proposal
        if proposal is None:
            return
        Path(self.proposal_out).write_text(
            proposal.model_dump_json(indent=2), encoding="utf-8"
        )

    async def send_request(self):
        await self.service.process_frame(
            InputTextRawFrame(text=REQUEST), FrameDirection.DOWNSTREAM
        )

    async def result_roundtrip(self):
        await self.service.process_frame(
            LLMContextFrame(self.assistant.context), FrameDirection.UPSTREAM
        )

    async def kickoff(self, worker, attempts=6, delay=1.5):
        """Ask for the opening turn until the agent actually speaks.

        The provider session opens a second or more after the client connects,
        and a run queued before that is dropped, so one attempt is not enough.
        """
        for _ in range(attempts):
            await asyncio.sleep(delay)
            if self.spoke or self.input_gate.closed:
                return
            await worker.queue_frames([LLMRunFrame()])

    async def run(self):
        transport = EvalTransport(
            params=EvalTransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=INPUT_RATE,
                audio_out_sample_rate=OUTPUT_RATE,
                audio_in_channels=1,
                audio_out_channels=1,
                video_in_enabled=False,
                video_out_enabled=False,
                serializer=EvalSerializer(),
            ),
            host=self.host,
            port=self.port,
        )
        rtvi = RTVIProcessor()
        user, self.assistant = LLMContextAggregatorPair(
            LLMContext([{"role": "user", "content": START_REQUEST}]),
            realtime_service_mode=True,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=ExternalUserTurnStrategies()
            ),
        )
        self.service = make_service(
            self.config,
            self.fail,
            self.controller,
            finished=self.finished,
            reserve_finished=self.reserve_finished,
        )
        self.controller.result_roundtrip = self.result_roundtrip

        @user.event_handler("on_user_turn_message_added")
        async def heard(*args):
            self.committed()

        worker = LivePipelineWorker(
            Pipeline(
                [
                    transport.input(),
                    rtvi,
                    *([self.spy] if self.spy.enabled else []),
                    self.hold,
                    self.input_gate,
                    user,
                    self.service,
                    self.playback_gate,
                    transport.output(),
                    self.assistant,
                ]
            ),
            params=PipelineParams(
                audio_in_sample_rate=INPUT_RATE,
                audio_out_sample_rate=OUTPUT_RATE,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
            idle_timeout_secs=None,
            enable_turn_tracking=False,
            enable_rtvi=False,
            observers=[RTVIObserver(rtvi)],
            processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
        )

        @self.assistant.event_handler("on_assistant_turn_stopped")
        async def spoke(*args):
            self.spoke = True

        self.runner = LiveWorkerRunner(handle_sigint=False, handle_sigterm=False)

        @self.runner.event_handler("on_error")
        async def runner_failed(*args):
            self.fail("runner_error")

        await self.runner.add_workers(worker)

        @transport.event_handler("on_client_connected")
        async def connected(*args):
            # Off by default: a seeded replay context already holds the
            # interview, and asking for an opening turn makes the model close
            # the call instead of waiting for the caller.
            asyncio.get_running_loop().create_task(self.kickoff(worker))

        runner_task = asyncio.create_task(self.runner.run())
        try:
            await asyncio.shield(runner_task)
        finally:
            await self.runner.cancel()
            await asyncio.gather(runner_task, return_exceptions=True)
            await self.service.close_sdk()


async def main(host, port, env_file, debug=False, proposal_out=None, mode="extraction"):
    if debug:
        enable_debug_logging()
    bot = EvalBot(provider_config(env_file), host, port, proposal_out, mode)
    bot.spy.enabled = debug
    try:
        await bot.run()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    if bot.failures:
        raise SystemExit(f"eval bot failed: {bot.failures}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7900)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--proposal-out", default=None)
    parser.add_argument(
        "--mode", choices=["extraction", "behaviour"], default="extraction"
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            main(
                args.host,
                args.port,
                args.env_file,
                args.debug,
                args.proposal_out,
                args.mode,
            )
        )
    except KeyboardInterrupt:
        pass
