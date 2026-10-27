"""Pinned optional adapter compatibility, credential-free and without sockets.

Requires the supported Python 3.12 environment and the locked SDK dependencies.
These are component/compatibility tests, not live-provider or browser tests.
"""

import asyncio
import inspect
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.live_runtime import LiveProviderConfig

pytestmark = pytest.mark.sdk


def test_product_prompt_uses_user_facts_not_a_fictional_roleplay(gemini, pipeline):
    assert "provided by the user" in gemini.PROMPT
    assert "fictional legal intake" not in gemini.PROMPT
    assert "Use only invented facts" not in gemini.PROMPT
    assert "those require the application's controls" in gemini.PROMPT


def test_public_signaling_seams_are_pinned_and_empty_candidate_supported(pipeline):
    from importlib.metadata import version

    assert version("pipecat-ai") == "1.12.0" and version("google-genai") == "2.19.0"
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler

    assert (
        "webrtc_connection_callback"
        in inspect.signature(SmallWebRTCRequestHandler.handle_web_request).parameters
    )
    assert (
        "sdp" in inspect.signature(pipeline.SmallWebRTCConnection.initialize).parameters
    )
    assert callable(pipeline.SmallWebRTCConnection.get_answer)
    assert callable(pipeline.SmallWebRTCConnection.add_ice_candidate)
    assert callable(pipeline.SmallWebRTCConnection.disconnect)
    assert (
        pipeline.LiveAdapter.candidate(
            SimpleNamespace(candidate="", sdp_mid="0", sdp_mline_index=0)
        )
        is None
    )
    candidate = pipeline.LiveAdapter.candidate(
        SimpleNamespace(
            candidate="candidate:fixture 1 udp 123 192.0.2.1 4567 typ host",
            sdp_mid="0",
            sdp_mline_index=0,
        )
    )
    assert (
        candidate.ip == "192.0.2.1"
        and candidate.sdpMid == "0"
        and candidate.sdpMLineIndex == 0
    )
    assert "pipecat.runner.run" not in sys.modules


@pytest.mark.asyncio
async def test_actual_connect_config_native_audio_server_vad_and_explicit_clients(
    gemini, pipeline, llm
):
    assert llm._settings.language is None and llm._settings.vad is None
    assert llm._client._api_client.vertexai is False
    assert (
        llm._client._api_client._http_options.base_url
        == "https://generativelanguage.googleapis.com/"
    )
    assert llm._client._debug_config.client_mode is None
    assert llm._client._debug_config.replays_directory is None
    assert llm._client._api_client._http_options.retry_options.attempts == 1
    configs = []

    async def inert():
        pass

    def collect(*, config):
        configs.append(config)
        return inert()

    def no_task(coroutine):
        coroutine.close()

    llm._connection_task_handler = collect
    llm.create_task = Mock(side_effect=no_task)
    llm.push_error = AsyncMock()
    await llm._connect()
    llm.push_error.assert_not_awaited()
    config = configs[0]
    assert [m.value for m in config.generation_config.response_modalities] == ["AUDIO"]
    assert config.generation_config.speech_config.language_code is None
    assert config.realtime_input_config is None
    assert (
        config.input_audio_transcription is not None
        and config.output_audio_transcription is not None
    )
    assert config.tools
    assert "propose_draft" in llm._functions
    assert not hasattr(llm, "publish_tools_context")
    assert llm._system_instruction_from_init == gemini.PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["auth", "quota", "provider", "goaway", "handshake"])
async def test_errors_goaway_handshake_are_permanent_sanitized_no_reconnect(
    gemini, pipeline, llm, monkeypatch, reason
):
    from google.genai.types import LiveServerMessage

    base_push = AsyncMock()
    monkeypatch.setattr(gemini.GeminiLiveLLMService, "push_error", base_push)
    llm._connect = AsyncMock()
    if reason == "goaway":
        await llm._handle_server_message(LiveServerMessage(go_away={"time_left": "5s"}))
    elif reason == "handshake":

        async def failed(*args, **kwargs):
            raise RuntimeError("private-key-payload")

        monkeypatch.setattr(
            gemini.GeminiLiveLLMService, "_connection_task_handler", failed
        )
        await llm._connection_task_handler(None)
    else:
        assert not await llm._handle_connection_error(
            RuntimeError("private-key-payload")
        )
    await llm._reconnect()
    llm._connect.assert_not_awaited()
    assert llm._fail.call_count == 2
    assert all(
        c.args == ("provider_failure_session_closed",)
        and c.kwargs == {"force_treat_as_permanent": True}
        for c in base_push.call_args_list
    )


@pytest.mark.asyncio
async def test_sdk_clients_close_once_even_when_base_cleanup_raises(
    gemini, pipeline, llm, monkeypatch
):
    monkeypatch.setattr(
        gemini.GeminiLiveLLMService,
        "cleanup",
        AsyncMock(side_effect=RuntimeError("private-cleanup")),
    )
    llm._client.close = Mock(wraps=llm._client.close)
    llm._client.aio.aclose = AsyncMock(wraps=llm._client.aio.aclose)
    with pytest.raises(RuntimeError):
        await llm.cleanup()
    await llm.close_sdk()
    llm._client.close.assert_called_once()
    llm._client.aio.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_pcm_workaround_preserves_audio_frames_without_retaining_bytes(llm):
    from google.genai.types import LiveServerMessage

    llm._bot_is_responding = True
    llm.push_frame = AsyncMock()
    payload = b"\x00\x00" * 240
    message = LiveServerMessage(
        server_content={
            "model_turn": {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "audio/pcm;rate=24000",
                            "data": payload,
                        }
                    }
                ]
            }
        }
    )
    for _ in range(10):
        await llm._handle_msg_model_turn(message)
        assert not llm._bot_audio_buffer
    assert all(
        c.args[0].audio == payload and c.args[0].sample_rate == 24000
        for c in llm.push_frame.call_args_list
    )
    llm.push_frame = AsyncMock(side_effect=RuntimeError("private-output-failure"))
    with pytest.raises(RuntimeError):
        await llm._handle_msg_model_turn(message)
    assert not llm._bot_audio_buffer


@pytest.mark.asyncio
async def test_input_audio_contract_never_commits_interim_or_provider_frames(pipeline):
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        InputTransportMessageFrame,
        TranscriptionFrame,
    )

    contract = pipeline.InputContract()
    contract.push_error = AsyncMock()
    contract.push_frame = AsyncMock()
    for rate, channels, audio in (
        (48000, 1, b"\x00\x00"),
        (16000, 2, b"\x00\x00"),
        (16000, 1, b"\x00"),
    ):
        frame = InputAudioRawFrame(audio=audio, sample_rate=rate, num_channels=channels)
        await contract.process_frame(frame, pipeline.FrameDirection.DOWNSTREAM)
    assert contract.push_error.await_count == 3
    await contract.process_frame(
        InputTransportMessageFrame(
            message={"type": "append-to-context", "text": "injected"}
        ),
        pipeline.FrameDirection.DOWNSTREAM,
    )
    contract.push_frame.assert_not_awaited()
    transcript = TranscriptionFrame(text="invented alias", user_id="", timestamp="")
    await contract.process_frame(transcript, pipeline.FrameDirection.UPSTREAM)
    contract.push_frame.assert_awaited_once_with(
        transcript, pipeline.FrameDirection.UPSTREAM
    )


@pytest.mark.asyncio
async def test_pipeline_cancellation_during_runner_setup_waits_for_cleanup(
    pipeline, monkeypatch
):
    setup_started, setup_release, stopped = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    clean = []

    class Transport:
        def __init__(self, *args):
            pass

        def input(self):
            return Mock()

        def output(self):
            return Mock()

        def event_handler(self, name):
            return lambda callback: callback

    class Worker:
        def __init__(self, *args, **kwargs):
            pass

        def event_handler(self, name):
            return lambda callback: callback

    class Runner:
        def __init__(self, **kwargs):
            pass

        def event_handler(self, name):
            return lambda callback: callback

        async def add_workers(self, *args):
            pass

        async def run(self):
            setup_started.set()
            await setup_release.wait()  # Must not be cancelled mid-setup.
            await asyncio.wait_for(stopped.wait(), timeout=5)
            clean.append(True)

        async def cancel(self):
            stopped.set()

    monkeypatch.setattr(pipeline, "SmallWebRTCTransport", Transport)
    monkeypatch.setattr(pipeline, "Pipeline", lambda graph: graph)
    monkeypatch.setattr(pipeline, "LivePipelineWorker", Worker)
    monkeypatch.setattr(pipeline, "LiveWorkerRunner", Runner)
    handle = pipeline.LivePipeline(
        Mock(),
        LiveProviderConfig("offline-placeholder", "explicit-model"),
        commit=Mock(),
        fail=Mock(),
        disconnect=Mock(),
    )
    task = asyncio.create_task(handle.run())
    await asyncio.wait_for(setup_started.wait(), timeout=5)
    task.cancel()
    await asyncio.wait_for(stopped.wait(), timeout=5)
    assert not task.done() and not clean
    setup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clean == [True] and handle.service._sdk_closed


@pytest.mark.asyncio
async def test_no_ambient_setup_file_code_is_loaded(pipeline, monkeypatch):
    import pipecat.pipeline.worker as worker
    import pipecat.workers.runner as runner

    forbidden = AsyncMock(side_effect=AssertionError("ambient setup code forbidden"))
    monkeypatch.setattr(runner, "run_setup_hook", forbidden)
    monkeypatch.setattr(worker, "run_setup_hook", forbidden)
    monkeypatch.setenv("PIPECAT_SETUP_FILES", "/must-not-read.py")
    # The hook ignores its instance; no runner/media initialization needed.
    await pipeline.LiveWorkerRunner._load_setup_files(None)
    await pipeline.LivePipelineWorker._load_setup_files(None)
    forbidden.assert_not_awaited()


def test_sensitive_upstream_debug_and_error_logs_suppressed(pipeline, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        for name in (
            "aiortc.rtcpeerconnection",
            "aioice.ice",
            "websockets.client",
            "google_genai.live",
            "httpcore.connection",
            "httpx",
        ):
            logging.getLogger(name).debug("private-sdp-key-transcript")
            logging.getLogger(name).error("private-provider-payload")
    assert "private" not in caplog.text


@pytest.mark.asyncio
async def test_sdk_sync_client_closes_even_when_async_close_fails(llm):
    llm._client.aio.aclose = AsyncMock(side_effect=RuntimeError("private-close"))
    llm._client.close = Mock(wraps=llm._client.close)
    with pytest.raises(RuntimeError):
        await llm.close_sdk()
    llm._client.close.assert_called_once()


@pytest.mark.asyncio
async def test_actual_public_connection_lifecycle_with_socket_free_aiortc_peer(
    pipeline, monkeypatch
):
    import pipecat.transports.smallwebrtc.connection as connection_module

    class Peer:
        connectionState = "new"

        def __init__(self, config):
            assert config.iceServers == []
            self.events = {}
            self.localDescription = None
            self.candidates = []
            self.closed = False

        def on(self, name):
            return lambda callback: self.events.setdefault(name, callback)

        def getTransceivers(self):
            return []

        async def setRemoteDescription(self, offer):
            assert offer.sdp == "fictional-offer" and offer.type == "offer"

        async def createAnswer(self):
            return connection_module.RTCSessionDescription(
                sdp="fictional-answer", type="answer"
            )

        async def setLocalDescription(self, answer):
            self.localDescription = answer

        async def addIceCandidate(self, candidate):
            self.candidates.append(candidate)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(connection_module, "RTCPeerConnection", Peer)
    connection = pipeline.LiveAdapter().connection()
    assert connection.get_answer() is None
    await connection.initialize(sdp="fictional-offer", type="offer")
    answer = connection.get_answer()
    assert answer == {
        "sdp": "fictional-answer",
        "type": "answer",
        "pc_id": connection.pc_id,
    }
    await connection.add_ice_candidate(None)
    assert connection.pc.candidates == [None]
    await connection.disconnect()
    assert connection.pc.closed
    await connection.cleanup()


def test_adapter_uses_only_explicit_stun_configuration(pipeline, monkeypatch):
    received = []
    monkeypatch.setattr(
        pipeline,
        "SmallWebRTCConnection",
        lambda **kwargs: received.append(kwargs["ice_servers"]),
    )
    pipeline.LiveAdapter().connection()
    pipeline.LiveAdapter(stun_url="stun:relay.example.invalid:3478").connection()
    assert received == [[], ["stun:relay.example.invalid:3478"]]


@pytest.mark.asyncio
async def test_audio_prepare_closes_app_gates_before_pause_and_uses_same_service(
    pipeline,
):
    from pipecat.frames.frames import EndFrame, InputTextRawFrame, OutputAudioRawFrame

    handle = pipeline.LivePipeline(
        Mock(),
        LiveProviderConfig("offline", "model"),
        commit=Mock(),
        fail=Mock(),
        disconnect=Mock(),
    )
    service = Mock()

    def pause(value):
        assert (
            value is True
            and handle.input_gate.closed
            and handle.playback_gate.suppressed
        )

    service.set_audio_input_paused = Mock(side_effect=pause)
    service.process_frame = AsyncMock()
    handle.service = service
    handle.gate_audio()
    await handle.request_audio_draft()
    service.set_audio_input_paused.assert_called_once_with(True)
    request, direction = service.process_frame.call_args.args
    assert (
        isinstance(request, InputTextRawFrame)
        and direction == pipeline.FrameDirection.DOWNSTREAM
    )
    handle.input_gate.push_frame = AsyncMock()
    handle.playback_gate.push_frame = AsyncMock()
    pcm = pipeline.InputAudioRawFrame(
        audio=b"\x00\x00", sample_rate=16000, num_channels=1
    )
    await handle.input_gate.process_frame(pcm, direction)
    handle.input_gate.push_frame.assert_not_awaited()
    audio = OutputAudioRawFrame(audio=b"\x00\x00", sample_rate=24000, num_channels=1)
    await handle.playback_gate.process_frame(audio, direction)
    handle.playback_gate.push_frame.assert_not_awaited()
    end = EndFrame()
    await handle.playback_gate.process_frame(end, direction)
    handle.playback_gate.push_frame.assert_awaited_once_with(end, direction)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gemini-2.5-live", "gemini-3.8-live"])
async def test_real_graph_starts_interview_once_and_preserves_tool_roundtrip(
    gemini, pipeline, monkeypatch, model
):
    """Pinned SDK startup and tool-result forwarding with an offline session."""
    from contextlib import asynccontextmanager

    from google.genai.types import LiveServerMessage

    from app.services.live_audio_draft import REQUEST, AudioDraftController
    from tests.support import arguments

    messages = asyncio.Queue()
    sent, contents, responses, constructions, connections = [], [], [], [], []

    class Session:
        async def send_realtime_input(self, **kwargs):
            sent.append(kwargs)

        async def send_client_content(self, **kwargs):
            contents.append(kwargs)

        async def send_tool_response(self, **kwargs):
            responses.append(kwargs)

        async def receive(self):
            while True:
                yield await messages.get()

        async def close(self):
            pass

    session = Session()

    @asynccontextmanager
    async def connect(**kwargs):
        connections.append(kwargs)
        yield session

    sdk = SimpleNamespace(
        aio=SimpleNamespace(live=SimpleNamespace(connect=connect), aclose=AsyncMock()),
        close=Mock(),
    )

    def client(**kwargs):
        constructions.append(kwargs)
        return sdk

    monkeypatch.setattr(gemini, "Client", client)

    class Peer:
        pc_id = "fake-peer"
        connected = False

        def __init__(self):
            self.events = {}

        def event_handler(self, name):
            def register(callback):
                self.events.setdefault(name, []).append(callback)
                return callback

            return register

        async def connect(self):
            self.connected = True
            for callback in self.events.get("connected", []):
                await callback(self)

        def is_connected(self):
            return self.connected

        def audio_input_track(self):
            return None

        video_input_track = screen_video_input_track = audio_input_track

        def replace_audio_track(self, track):
            pass

        def send_app_message(self, message):
            pass

        async def disconnect(self):
            self.connected = False

    async def eventually(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0)

    c = AudioDraftController()
    failures = []

    def fail():
        c.discard()
        failures.append(True)

    completion_seen = []
    preparing_tasks = []

    def finished():
        # A synchronous client response cannot overtake tool-result forwarding.
        assert len(responses) == 2
        assert responses[1]["function_responses"].response["accepted"] is True
        completion_seen.append(True)
        c.reserve(asyncio.get_running_loop().time() + 5)
        handle.gate_audio()
        preparing_tasks.append(
            asyncio.create_task(c.request(handle.request_audio_draft, lambda: None))
        )

    handle = pipeline.LivePipeline(
        Peer(),
        LiveProviderConfig("offline", model),
        commit=Mock(),
        fail=fail,
        disconnect=fail,
        audio_draft=c,
        reserve_finished=lambda: not completion_seen,
        finished=finished,
    )
    running = asyncio.create_task(handle.run())
    try:
        await eventually(lambda: handle.transport_ready and len(connections) == 1)
        await eventually(lambda: handle.ready)
        assert handle.service._inference_on_context_initialization is True
        assert len(contents) == 1 and contents[0]["turn_complete"] is True
        turns = contents[0]["turns"]
        assert len(turns) == 1 and turns[0].role == "user"
        assert (
            turns[0].parts[0].text
            == "Begin the interview now with your opening greeting."
        )
        assert sent == ([{"text": " "}] if model.startswith("gemini-3") else [])
        assert not constructions[0]["vertexai"] and len(constructions) == 1
        assert handle.commit.call_count == 0
        initial_sent, initial_contents = len(sent), len(contents)
        await messages.put(
            LiveServerMessage(
                tool_call={
                    "function_calls": [
                        {"name": "propose_draft", "id": "early", "args": arguments()},
                    ]
                }
            )
        )
        await eventually(lambda: len(responses) == 1)
        assert responses[0]["function_responses"].response == {
            "accepted_unconfirmed_proposal": False
        }
        assert c.phase == "not_requested" and not handle.input_gate.closed
        assert len(sent) == initial_sent and len(contents) == initial_contents
        await messages.put(
            LiveServerMessage(
                tool_call={
                    "function_calls": [
                        {"name": "interview_finished", "id": "finished", "args": {}}
                    ]
                }
            )
        )
        await eventually(lambda: bool(preparing_tasks))
        preparing = preparing_tasks[0]
        assert completion_seen == [True]
        await eventually(lambda: len(sent) > initial_sent)
        assert sent[initial_sent:] == [{"text": REQUEST}]
        await messages.put(
            LiveServerMessage(
                tool_call={
                    "function_calls": [
                        {
                            "name": "propose_draft",
                            "id": "accepted",
                            "args": arguments(),
                        },
                    ]
                }
            )
        )
        await asyncio.wait_for(preparing, 3)
        assert c.view("s").proposal.tool_call_id == "accepted"
        assert len(responses) == 3
        assert responses[2]["function_responses"].response == {
            "accepted_unconfirmed_proposal": True
        }
        assert len(sent) == initial_sent + 1 and len(contents) == initial_contents
        assert len(connections) == len(constructions) == 1 and not failures
    finally:
        c.discard()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await handle.close()
    sdk.close.assert_called_once()
    sdk.aio.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,accepted",
    [({}, True), ({"finish": True}, False), (None, False), ([], False)],
)
async def test_explicit_finish_tool_validates_empty_arguments_and_suppresses_continuation(
    pipeline, arguments, accepted
):
    finished = Mock(return_value=True)
    reserved = Mock(return_value=True)
    service = pipeline.make_service(
        LiveProviderConfig("offline-placeholder-not-a-key", "explicit-model"),
        Mock(),
        finished=finished,
        reserve_finished=reserved,
    )
    result = AsyncMock()
    try:
        await service._functions["interview_finished"].handler(
            SimpleNamespace(arguments=arguments, result_callback=result)
        )
        assert reserved.call_count == int(accepted)
        finished.assert_not_called()
        assert result.call_args.args[0] == {"accepted": accepted}
        assert result.call_args.kwargs["properties"].run_llm is False
    finally:
        await service.close_sdk()


def test_completion_envelope_serialized_by_real_connection_without_network(pipeline):
    import json

    peer = pipeline.SmallWebRTCConnection(ice_servers=[])
    peer._data_channel = SimpleNamespace(readyState="open", send=Mock())
    envelope = {
        "label": "rtvi-ai",
        "type": "server-message",
        "id": "opaque-event",
        "data": {"type": "interview_finished", "sessionId": "non-secret-session"},
    }
    peer.send_app_message(envelope)
    assert json.loads(peer._data_channel.send.call_args.args[0]) == envelope


@pytest.mark.asyncio
@pytest.mark.parametrize("forwarding_fails", [False, True])
async def test_completion_forwarding_barrier_and_failure_no_event(
    pipeline, forwarding_fails
):
    from app.services.live_audio_draft import AudioDraftController

    controller = AudioDraftController()
    entered, release = asyncio.Event(), asyncio.Event()

    async def roundtrip():
        entered.set()
        await release.wait()
        if forwarding_fails:
            raise ValueError("offline forwarding failure")

    controller.result_roundtrip = roundtrip
    reserve, finished, fail = Mock(return_value=True), Mock(), Mock()
    service = pipeline.make_service(
        LiveProviderConfig("offline", "explicit-model"),
        fail,
        controller,
        reserve_finished=reserve,
        finished=finished,
    )
    result = AsyncMock()
    try:
        await service._functions["interview_finished"].handler(
            SimpleNamespace(arguments={}, result_callback=result)
        )
        reserve.assert_called_once()
        callback = result.call_args.kwargs["properties"].on_context_updated
        forwarding = asyncio.create_task(callback())
        await asyncio.wait_for(entered.wait(), timeout=5)
        finished.assert_not_called()
        release.set()
        await forwarding
        assert finished.call_count == int(not forwarding_fails)
        assert fail.call_count == int(forwarding_fails)
    finally:
        await service.close_sdk()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["discarded", "cancelled"])
async def test_completion_result_teardown_unblocks_without_late_roundtrip(
    pipeline, ending
):
    from app.services.live_audio_draft import AudioDraftController

    controller = AudioDraftController()
    controller.result_roundtrip = AsyncMock()
    forwarded, finished, fail = Mock(), Mock(), Mock()
    service = pipeline.make_service(
        LiveProviderConfig("offline", "explicit-model"),
        fail,
        controller,
        reserve_finished=Mock(return_value=True),
        finished=finished,
        completion_forwarded=forwarded,
    )
    result = AsyncMock()
    try:
        if ending == "cancelled":
            result.side_effect = asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await service._functions["interview_finished"].handler(
                    SimpleNamespace(arguments={}, result_callback=result)
                )
        else:
            await service._functions["interview_finished"].handler(
                SimpleNamespace(arguments={}, result_callback=result)
            )
            controller.discard()
            await result.call_args.kwargs["properties"].on_context_updated()
        forwarded.assert_called_once_with(False)
        finished.assert_not_called()
        controller.result_roundtrip.assert_not_called()
    finally:
        await service.close_sdk()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gemini-2.5-live", "gemini-3.8-live"])
@pytest.mark.parametrize("context_first", [False, True])
async def test_opening_is_requested_once_for_either_setup_order(
    gemini, pipeline, model, context_first
):
    service = gemini.make_service(LiveProviderConfig("offline", model), Mock())
    session = SimpleNamespace(
        send_client_content=AsyncMock(), send_realtime_input=AsyncMock()
    )
    context = pipeline.LLMContext(
        [
            {
                "role": "user",
                "content": "Begin the interview now with your opening greeting.",
            }
        ]
    )
    try:
        if context_first:
            await service._handle_context(context)
            session.send_client_content.assert_not_awaited()
            await service._handle_session_ready(session)
        else:
            await service._handle_session_ready(session)
            session.send_client_content.assert_not_awaited()
            await service._handle_context(context)
        session.send_client_content.assert_awaited_once()
        request = session.send_client_content.call_args.kwargs
        assert request["turn_complete"] is True
        assert len(request["turns"]) == 1
        assert (
            request["turns"][0].parts[0].text
            == "Begin the interview now with your opening greeting."
        )
        assert service._ready_for_realtime_input
        if model.startswith("gemini-3"):
            session.send_realtime_input.assert_awaited_once_with(text=" ")
        else:
            session.send_realtime_input.assert_not_awaited()
        await service._handle_context(context)
        session.send_client_content.assert_awaited_once()
        assert session.send_realtime_input.await_count == (
            1 if model.startswith("gemini-3") else 0
        )
    finally:
        await service.close_sdk()
