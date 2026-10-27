"""Pure audio proposal validation and settlement, with no optional SDK imports."""

import asyncio
from types import SimpleNamespace

import pytest

from app.models.intake import LegalAnalysis
from app.models.live_audio_draft import AudioDraftRequest
from app.services.live_audio_draft import (
    FACT_FIELDS,
    AudioDraftController,
    tool_schema,
    validate_arguments,
)
from tests.support import accept_callback, arguments, params

pytestmark = pytest.mark.unit


async def inert():
    pass


def controller(clock=lambda: 0):
    result = AudioDraftController(monotonic=clock)
    result.result_roundtrip = inert
    return result


def test_exact_canonical_schema_defaults_and_nullable_facts():
    facts, analysis = validate_arguments(arguments())
    assert type(analysis) is LegalAnalysis
    assert len(facts) == 11 and all(v is None for v in facts.values())
    assert analysis.case_title_en == "" and analysis.key_entities.parties == []
    schema = tool_schema()["function_declarations"][0]["parameters_json_schema"]
    assert set(schema["properties"]["facts"]["required"]) == set(FACT_FIELDS)
    assert "$ref" not in str(schema)
    assert AudioDraftRequest(acknowledge_unproven_tail=True)


@pytest.mark.parametrize("value", [False, 1, 0, "true", None])
def test_strict_true_ack(value):
    with pytest.raises(ValueError):
        AudioDraftRequest(acknowledge_unproven_tail=value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a.update(extra=True),
        lambda a: a["facts"].pop("timeline"),
        lambda a: a["facts"].update(timeline=3),
        lambda a: a["facts"].update(timeline=" " * 2),
        lambda a: a["facts"].update(timeline="x" * 2049),
        lambda a: a["analysis"].update(unknown=True),
        lambda a: a["analysis"].update(confidence_score=float("nan")),
        lambda a: a["analysis"].update(confidence_score=True),
        lambda a: a["analysis"].update(confidence_score="0.5"),
        lambda a: a["analysis"].update(confidence_score=2),
        lambda a: a["analysis"].update(adr_suitable=1),
        lambda a: a["analysis"].update(urgency="immediate"),
        lambda a: a["analysis"].update(key_entities={"extra": []}),
        lambda a: a["analysis"].update(key_entities={"parties": [1]}),
        lambda a: a["analysis"].update(key_entities={"parties": ["x"] * 33}),
        lambda a: a.update(analysis={"nested": [[[[[[[[[[[[[]]]]]]]]]]]]]}),
        lambda a: a["analysis"].update(issue_summary="x" * 4097),
        lambda a: a["analysis"].update(key_entities={"parties": ["x" * 4096] * 32}),
    ],
)
def test_untrusted_output_rejected(mutation):
    data = arguments()
    mutation(data)
    with pytest.raises(ValueError):
        validate_arguments(data)


@pytest.mark.asyncio
async def test_early_duplicate_late_calls_do_not_reserve_or_replace():
    c = controller()
    await c.handle_tool(params(accept_callback), properties_factory=SimpleNamespace)
    assert c.phase == "not_requested"
    c.reserve(30)

    async def send():
        await c.handle_tool(params(accept_callback), properties_factory=SimpleNamespace)
        changed = arguments()
        changed["facts"]["timeline"] = "replacement"
        await c.handle_tool(
            params(accept_callback, changed), properties_factory=SimpleNamespace
        )

    await c.request(send, lambda: None)
    assert c.phase == "proposed"
    view = c.view("session")
    assert view.proposal.facts["timeline"] is None
    view.proposal.analysis.issue_summary = "mutated"
    assert c.view("session").proposal.analysis.issue_summary == "invented"
    await c.handle_tool(params(accept_callback), properties_factory=SimpleNamespace)
    with pytest.raises(ValueError):
        c.reserve(30)
    c.discard()
    assert c.view("session").proposal is None


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["callback_first", "context_first"])
async def test_publication_requires_send_callback_context_and_owner(order):
    c = controller()
    c.reserve(30)
    sent, release_send, release_callback = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    properties_slot = []

    async def callback(result, *, properties):
        properties_slot.append(properties)
        sent.set()
        if order == "context_first":
            await properties.on_context_updated()
        await release_callback.wait()

    async def send():
        tool = asyncio.create_task(
            c.handle_tool(params(callback), properties_factory=SimpleNamespace)
        )
        await asyncio.wait_for(sent.wait(), timeout=5)
        await tool
        await release_send.wait()

    checked = []
    task = asyncio.create_task(c.request(send, lambda: checked.append(True)))
    await asyncio.wait_for(sent.wait(), timeout=5)
    assert c.view("s").proposal is None
    release_callback.set()
    if order == "callback_first":
        await properties_slot[0].on_context_updated()
    await asyncio.sleep(0)
    assert c.view("s").proposal is None and not checked
    release_send.set()
    await task
    assert checked == [True] and c.phase == "proposed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["send", "callback", "context", "owner", "cancel", "timeout", "invalid"]
)
async def test_failures_never_publish_or_retry(failure):
    clock = [0]
    c = controller(lambda: clock[0])
    c.reserve(0.01 if failure == "timeout" else 30)

    async def broken(*args, **kwargs):
        raise ValueError("sensitive")

    if failure == "context":
        c.result_roundtrip = broken

    async def send():
        if failure == "send":
            raise ValueError()
        if failure in ("cancel", "timeout"):
            await asyncio.Event().wait()
        await c.handle_tool(
            params(
                broken if failure == "callback" else accept_callback,
                {} if failure == "invalid" else None,
            ),
            properties_factory=SimpleNamespace,
        )

    def owner():
        if failure == "owner":
            raise ValueError()

    task = asyncio.create_task(c.request(send, owner))
    if failure == "cancel":
        await asyncio.sleep(0)
        task.cancel()
    expected_error = (
        asyncio.CancelledError
        if failure == "cancel"
        else TimeoutError
        if failure == "timeout"
        else ValueError
    )
    async with asyncio.timeout(5) as watchdog:
        with pytest.raises(expected_error):
            await task
        assert not watchdog.expired(), "Test coordination timed out"
    assert c.phase == "failed" and c.view("s").proposal is None and c._pending is None
    with pytest.raises(ValueError):
        c.reserve(30)


EXPECTED_FACT_FIELDS = (
    "alias_name",
    "issue_category",
    "issue_description",
    "timeline",
    "parties",
    "location",
    "financial_impact",
    "previous_action",
    "urgency",
    "documentation",
    "desired_outcome",
)


def test_literal_fact_contract_order_and_bounds():
    from app.models.live import LegalIntakeField
    from app.models.live_audio_draft import AudioContextProposal

    assert tuple(field.value for field in LegalIntakeField) == EXPECTED_FACT_FIELDS
    assert FACT_FIELDS == EXPECTED_FACT_FIELDS
    schema = tool_schema()["function_declarations"][0]["parameters_json_schema"]
    facts_schema = schema["properties"]["facts"]
    assert facts_schema["required"] == list(EXPECTED_FACT_FIELDS)
    assert tuple(facts_schema["properties"]) == EXPECTED_FACT_FIELDS
    model_schema = AudioContextProposal.model_json_schema()
    assert model_schema["$defs"]["LegalIntakeField"]["enum"] == list(
        EXPECTED_FACT_FIELDS
    )
    for value in facts_schema["properties"].values():
        assert value == {
            "anyOf": [
                {"type": "string", "minLength": 1, "maxLength": 2048},
                {"type": "null"},
            ]
        }


@pytest.mark.parametrize("value", [None, "x", "x" * 2048, "ا" * 2048])
def test_fact_boundary_accepted_by_controller_and_model(value):
    from app.models.live_audio_draft import AudioContextProposal

    data = arguments()
    data["facts"]["timeline"] = value
    facts, _ = validate_arguments(data)
    proposal = AudioContextProposal(tool_call_id="fixture", **data)
    assert facts["timeline"] == proposal.facts["timeline"] == value


@pytest.mark.parametrize("value", ["", " \t", "x" * 2049, 1, True, [], {}])
def test_fact_boundary_rejected_by_controller_and_model(value):
    from app.models.live_audio_draft import AudioContextProposal

    data = arguments()
    data["facts"]["timeline"] = value
    with pytest.raises(ValueError):
        validate_arguments(data)
    with pytest.raises(ValueError):
        AudioContextProposal(tool_call_id="fixture", **data)


@pytest.mark.parametrize(
    "data", [{}, {"facts": {}, "analysis": {}}, {"facts": None, "analysis": {}}]
)
def test_malformed_fact_contract_rejected(data):
    from app.models.live_audio_draft import AudioContextProposal

    with pytest.raises(ValueError):
        validate_arguments(data)
    with pytest.raises(ValueError):
        AudioContextProposal(tool_call_id="fixture", **data)


def test_recursive_node_budget_is_independent_of_fact_length():
    data = arguments()
    # Keep arrays within their own bound while exceeding the total node budget.
    data["analysis"] = {"unknown": [[[None] * 32 for _ in range(32)] for _ in range(2)]}
    with pytest.raises(ValueError, match="argument_budget"):
        validate_arguments(data)
