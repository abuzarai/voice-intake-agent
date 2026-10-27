"""Focused real-aggregator contracts with fake transport and worker lifecycle."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

from app.services.live_runtime import LiveProviderConfig

pytestmark = pytest.mark.sdk


@pytest_asyncio.fixture
async def aggregation_graph(pipeline, monkeypatch):
    captured = {}

    class Transport:
        def __init__(self, connection, params):
            captured["params"] = params
            self.events = captured["transport_events"] = {}

        def input(self):
            return Mock()

        def output(self):
            return Mock()

        def event_handler(self, name):
            return lambda callback: self.events.setdefault(name, callback)

    class Worker:
        def __init__(self, graph, **kwargs):
            captured["graph"] = graph
            captured["worker_kwargs"] = kwargs

        def event_handler(self, name):
            return lambda callback: captured.setdefault(name, callback)

        queue_frames = AsyncMock()

    class Runner:
        def __init__(self, **kwargs):
            assert kwargs == {"handle_sigint": False, "handle_sigterm": False}

        def event_handler(self, name):
            return lambda callback: captured.setdefault(name, callback)

        add_workers = AsyncMock()
        run = AsyncMock()
        cancel = AsyncMock()

    monkeypatch.setattr(pipeline, "SmallWebRTCTransport", Transport)
    monkeypatch.setattr(pipeline, "Pipeline", lambda graph: graph)
    monkeypatch.setattr(pipeline, "LivePipelineWorker", Worker)
    monkeypatch.setattr(pipeline, "LiveWorkerRunner", Runner)
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
        LocalSmartTurnAnalyzerV3,
    )

    monkeypatch.setattr(
        LocalSmartTurnAnalyzerV3,
        "__init__",
        Mock(side_effect=AssertionError("no local model")),
    )
    commit, fail, disconnect = Mock(), Mock(), Mock()
    assistant_commit = Mock()
    handle = pipeline.LivePipeline(
        Mock(),
        LiveProviderConfig("offline-placeholder", "explicit-model"),
        commit=commit,
        fail=fail,
        disconnect=disconnect,
        assistant_commit=assistant_commit,
    )

    user = assistant = None
    try:
        await handle.run()
        user, assistant = captured["graph"][2], captured["graph"][-1]
        user.push_context_frame = AsyncMock()
        assistant.push_context_frame = AsyncMock()
        yield SimpleNamespace(
            handle=handle,
            captured=captured,
            user=user,
            assistant=assistant,
            worker=Worker,
            commit=commit,
            assistant_commit=assistant_commit,
            fail=fail,
            disconnect=disconnect,
        )
    finally:
        try:
            if user is not None:
                await user.cleanup()
        finally:
            try:
                if assistant is not None:
                    await assistant.cleanup()
            finally:
                await handle.close()


def test_graph_preserves_media_policy_and_initial_context(aggregation_graph):
    g = aggregation_graph
    graph, params = g.captured["graph"], g.captured["params"]
    assert graph[1] is g.handle.input_gate
    assert graph[3] is g.handle.service
    assert graph[4] is g.handle.playback_gate
    assert g.user.context.get_messages() == [
        {
            "role": "user",
            "content": "Begin the interview now with your opening greeting.",
        }
    ]
    assert g.user._realtime_service_mode and g.assistant._realtime_service_mode
    assert params.audio_in_sample_rate == 16000
    assert params.audio_out_sample_rate == 24000
    assert not params.video_in_enabled and not params.video_out_enabled
    assert g.captured["worker_kwargs"]["idle_timeout_secs"] is None
    assert g.captured["worker_kwargs"]["enable_rtvi"] is False
    assert g.handle.service._inference_on_context_initialization is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "startup", ["active", "stopped", "queue-error", "stop-during-queue"]
)
async def test_startup_queues_context_at_most_once(
    aggregation_graph, pipeline, startup
):
    g = aggregation_graph
    if startup == "stopped":
        g.handle.gate_audio()
    elif startup == "queue-error":
        g.worker.queue_frames.side_effect = ValueError("private startup failure")
    elif startup == "stop-during-queue":

        async def stop_during_queue(frames):
            g.handle.gate_audio()

        g.worker.queue_frames.side_effect = stop_during_queue
    connected = g.captured["transport_events"]["on_client_connected"]
    await connected()
    await connected()
    if startup == "stopped":
        g.worker.queue_frames.assert_not_awaited()
    else:
        g.worker.queue_frames.assert_awaited_once()
        assert isinstance(
            g.worker.queue_frames.call_args.args[0][0], pipeline.LLMRunFrame
        )
    assert g.handle.transport_ready is (startup == "active")
    assert not g.handle.ready
    if startup == "queue-error":
        g.fail.assert_called_once()
    else:
        g.fail.assert_not_called()
    g.commit.assert_not_called()
    g.handle.gate_audio()
    await connected()
    assert g.worker.queue_frames.await_count == (0 if startup == "stopped" else 1)


@pytest.mark.asyncio
async def test_user_commits_only_final_aggregation_once(aggregation_graph):
    from pipecat.frames.frames import TranscriptionFrame

    g = aggregation_graph
    await g.user._handle_transcription(
        TranscriptionFrame(text="invented alias", user_id="", timestamp="")
    )
    g.commit.assert_not_called()
    await g.user.push_aggregation()
    await g.user.push_aggregation()
    await asyncio.sleep(0)
    g.commit.assert_called_once_with("invented alias")
    g.assistant_commit.assert_not_called()


@pytest.mark.asyncio
async def test_assistant_final_turn_preserves_interruption_without_duplicate_text(
    aggregation_graph,
):
    from pipecat.frames.frames import LLMTextFrame, TTSTextFrame
    from pipecat.utils.text.base_text_aggregator import AggregationType

    g = aggregation_graph
    await g.assistant._trigger_assistant_turn_started()
    duplicate = LLMTextFrame(text="do not duplicate")
    duplicate.append_to_context = False
    await g.assistant._handle_text(duplicate)
    await g.assistant._handle_text(
        TTSTextFrame(text="invented assistant", aggregated_by=AggregationType.SENTENCE)
    )
    await g.assistant._trigger_assistant_turn_stopped(interrupted=True)
    await g.assistant._trigger_assistant_turn_stopped()
    await asyncio.sleep(0)
    g.assistant_commit.assert_called_once_with("invented assistant", True)
    g.commit.assert_not_called()


@pytest.mark.asyncio
async def test_assistant_capture_failure_does_not_fail_voice(aggregation_graph):
    from pipecat.frames.frames import TTSTextFrame
    from pipecat.utils.text.base_text_aggregator import AggregationType

    g = aggregation_graph
    g.assistant_commit.side_effect = ValueError("private transcript fault")
    await g.assistant._trigger_assistant_turn_started()
    await g.assistant._handle_text(
        TTSTextFrame(text="invented assistant", aggregated_by=AggregationType.SENTENCE)
    )
    await g.assistant._trigger_assistant_turn_stopped()
    await asyncio.sleep(0)
    g.assistant_commit.assert_called_once()
    g.fail.assert_not_called()


@pytest.mark.asyncio
async def test_closed_input_fences_late_user_and_assistant_commits(aggregation_graph):
    from pipecat.frames.frames import TranscriptionFrame

    g = aggregation_graph
    g.handle.gate_audio()
    await g.user._handle_transcription(
        TranscriptionFrame(text="late user", user_id="", timestamp="")
    )
    await g.user.push_aggregation()
    await g.assistant._trigger_assistant_turn_started()
    await g.assistant._trigger_assistant_turn_stopped()
    await asyncio.sleep(0)
    g.commit.assert_not_called()
    g.assistant_commit.assert_not_called()


@pytest.mark.asyncio
async def test_transport_and_runner_errors_reach_owned_callbacks(aggregation_graph):
    g = aggregation_graph
    await g.captured["transport_events"]["on_client_disconnected"]()
    g.disconnect.assert_called_once()
    await g.captured["on_pipeline_error"]()
    await g.captured["on_error"]()
    assert g.fail.call_count == 2


@pytest.mark.asyncio
async def test_user_commit_failure_reaches_owner_instead_of_sdk_event_logger(
    aggregation_graph,
):
    from pipecat.frames.frames import TranscriptionFrame

    g = aggregation_graph
    g.commit.side_effect = RuntimeError("private callback")
    await g.user._handle_transcription(
        TranscriptionFrame(text="invented issue", user_id="", timestamp="")
    )
    await g.user.push_aggregation()
    await asyncio.sleep(0)
    g.commit.assert_called_once_with("invented issue")
    g.fail.assert_called_once()
