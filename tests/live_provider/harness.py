"""Drive the real Gemini Live service through one extraction request.

The graph is the production graph minus the transport: the same
``make_service``, the same prompt, the same tool binding, and the same
aggregator pair. Only the media path is absent, because the replay supplies a
recorded transcript instead of a caller.
"""

import asyncio
import time

from pipecat.frames.frames import LLMContextFrame, LLMRunFrame
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
from pipecat.processors.frame_processor import FrameDirection
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from app.adapters.pipecat_live.gemini import make_service
from app.services.live_audio_draft import REQUEST, AudioDraftController

DRAFT_TIMEOUT = 45.0

# A fixture ends on the caller's last answer, which in production is never the
# state the draft is requested from: the agent closes the interview first. Left
# out, the model treats the recording as a live call and keeps interviewing
# instead of drafting, so the request waits out the deadline.
CLOSURE = {
    "role": "assistant",
    "content": "Thank you. I have what I need for now; I am preparing the draft.",
}


async def propose_draft(config, turns):
    """Run one conversation through the real service and return its draft."""
    controller = AudioDraftController()
    failures = []
    context = LLMContext(
        [dict(turn) for turn in turns] + [CLOSURE, {"role": "user", "content": REQUEST}]
    )
    user, assistant = LLMContextAggregatorPair(
        context,
        realtime_service_mode=True,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=ExternalUserTurnStrategies()
        ),
    )
    service = make_service(
        config,
        lambda *_args: failures.append("pipeline_failed"),
        controller,
    )

    async def result_roundtrip():
        await service.process_frame(
            LLMContextFrame(assistant.context), FrameDirection.UPSTREAM
        )

    controller.result_roundtrip = result_roundtrip

    worker = PipelineWorker(
        Pipeline([user, service, assistant]),
        params=PipelineParams(enable_metrics=False, enable_usage_metrics=False),
        idle_timeout_secs=None,
        enable_turn_tracking=False,
        enable_rtvi=False,
        processor_unusable_policy=ProcessorUnusablePolicy.CANCEL,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    runner_task = asyncio.create_task(runner.run())
    try:

        async def send_request():
            service.set_audio_input_paused(True)
            await worker.queue_frames([LLMRunFrame()])

        # Reserve before the request leaves, so the tool call cannot arrive
        # while the controller is still in ``not_requested``.
        controller.reserve(time.monotonic() + DRAFT_TIMEOUT)
        await controller.request(send_request, lambda: None)
    finally:
        await runner.cancel()
        await asyncio.gather(runner_task, return_exceptions=True)
        await service.close_sdk()

    if failures:
        raise AssertionError(f"pipeline reported failure: {failures}")
    if controller.phase != "proposed" or controller._proposal is None:
        raise AssertionError(f"draft did not settle: phase={controller.phase}")
    return controller._proposal
